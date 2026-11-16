'use strict';

/* ============================================================================
   Refire — frontend sin dependencias.
   Abrir:  static/index.html?mock=1   (sin backend)
           http://127.0.0.1:8770/?mock=1
           http://127.0.0.1:8770/
   Contrato: BRIEF.md sección 5. Nada de elogios, nada de gamificación.
   ========================================================================== */

/* ------------------------------- constantes ------------------------------- */

const PARAMS = new URLSearchParams(location.search);
const MOCK = PARAMS.get('mock') === '1';
const MOCK_AUDIO_501 = PARAMS.get('audio') === '501';   /* ?mock=1&audio=501 */
const MOCK_LONG = PARAMS.get('long') === '1';           /* ?mock=1&long=1  */

/* Si el backend ya registra la respuesta al transcribir, poner esto a false
   para no contar el acierto dos veces. */
const REGISTER_AUDIO_ANSWERS = true;

const MIN_SESSIONS_TREND = 4;

const PATTERN_ORDER = ['conditionals', 'articles', 'prepositions', 'collocations', 'verb-form', 'word-choice', 'discourse'];
const PATTERN_LABELS = {
  conditionals: 'condicionales',
  articles: 'artículos',
  prepositions: 'preposiciones',
  collocations: 'colocaciones',
  'verb-form': 'forma verbal',
  'word-choice': 'elección de palabra',
  discourse: 'discurso'
};

const COMPETENCES = [
  ['fluency', 'fluidez'],
  ['grammar', 'gramática'],
  ['lexis', 'léxico'],
  ['organisation', 'organización'],
  ['interaction', 'interacción']
];

const WARNING_TEXT = {
  drills_pending: 'Los drills no se generaron. El informe está guardado y se pueden regenerar más tarde.',
  vault_no_encontrado: 'No hay nota del registro: el informe se ha guardado en data/reports, pero no se ha añadido la fila. Define REFIRE_VAULT con la ruta de tu nota.'
};

const STEP_LABELS = ['leyendo', 'escribiendo en el vault', 'generando drills'];
const STEP_AT_MS = [0, 1800, 5000];
const SLOW_AFTER_MS = 40000;

/* ------------------------------- utilidades ------------------------------- */

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

const AUTOSCENARIO = new URLSearchParams(location.search).get('autoscenario') === '1';
/* con autoscenario no se simula latencia: la captura headless no espera timers */
const sleep = (ms) => (AUTOSCENARIO ? Promise.resolve() : new Promise((r) => setTimeout(r, ms)));

function el(tag, attrs) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const key of Object.keys(attrs)) {
      const value = attrs[key];
      if (value === null || value === undefined || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key === 'dataset') Object.assign(node.dataset, value);
      else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2).toLowerCase(), value);
      else node.setAttribute(key, value === true ? '' : String(value));
    }
  }
  for (let i = 2; i < arguments.length; i++) {
    const kid = arguments[i];
    if (kid === null || kid === undefined || kid === false) continue;
    if (Array.isArray(kid)) kid.forEach((k) => node.append(k && k.nodeType ? k : document.createTextNode(String(k))));
    else node.append(kid && kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

const SVG_NS = 'http://www.w3.org/2000/svg';
function svg(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  if (attrs) for (const k of Object.keys(attrs)) node.setAttribute(k, String(attrs[k]));
  return node;
}

const clone = (obj) => JSON.parse(JSON.stringify(obj));

function todayISO() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
}

function fmtScore(v) {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? String(n) : '—';
}

function fmtSeconds(s) {
  const total = Math.max(0, Math.round(s));
  return total + ' s';
}

function patternLabel(name) {
  return PATTERN_LABELS[name] || name || '—';
}

function plural(n, one, many) {
  return n + ' ' + (n === 1 ? one : many);
}

/* --------------------------------- tarjetas -------------------------------- */

/* `gap` = frase con hueco (se responde). `model` = frase modelo: una corrección que no
   admite hueco (el arreglo es borrar una palabra). Se mira, se escucha y se marca a mano:
   no es un examen y no puntúa. */
function isModel(item) {
  return !!item && item.kind === 'model';
}

/* ------------------------- voz del navegador (model) ----------------------- */

const speech = {
  supported: typeof window !== 'undefined' && 'speechSynthesis' in window,
  english: [],
  bound: false
};

function loadSpeechVoices() {
  if (!speech.supported) { speech.english = []; return speech.english; }
  let voices = [];
  try { voices = window.speechSynthesis.getVoices() || []; } catch (e) { voices = []; }
  speech.english = voices.filter((v) => String(v.lang || '').toLowerCase().indexOf('en') === 0);
  return speech.english;
}

function englishVoice() {
  if (!speech.english.length) loadSpeechVoices();
  return speech.english[0] || null;
}

function canSpeak() {
  return !!englishVoice();
}

function speak(text) {
  const voice = englishVoice();
  if (!voice || !text) return;
  try {
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(String(text));
    utter.voice = voice;
    utter.lang = voice.lang;
    utter.rate = 0.95;
    window.speechSynthesis.speak(utter);
  } catch (e) { /* sin voz la tarjeta sigue igual: no se rompe nada */ }
}

/* Escuchar una frase modelo.
   1) Primero la voz del servidor (/api/tts → espeak-ng): funciona en cualquier navegador,
      incluso en los que no ven las voces del sistema (Chromium en Linux con speech-dispatcher).
   2) Si el servidor no puede, se cae a la voz del navegador.
   3) Si tampoco, el botón lo dice en el title en vez de quedarse mudo. */
function speakModel(text, button) {
  if (!text) return;
  const note = (msg) => { if (button) button.title = msg; };
  const fallback = () => {
    if (canSpeak()) { speak(text); return; }
    note('sin voz disponible: el servidor no pudo sintetizar y el navegador no tiene voces');
  };
  try {
    const audio = new Audio('/api/tts?text=' + encodeURIComponent(text));
    audio.addEventListener('error', fallback);
    note('');
    const p = audio.play();
    if (p && typeof p.catch === 'function') p.catch(fallback);
  } catch (e) {
    fallback();
  }
}

/* Las voces del navegador llegan tarde: cuando aparecen, se repinta la tarjeta para que
   salga (o no) el botón de escuchar. */
function initSpeech() {
  if (!speech.supported || speech.bound) return;
  speech.bound = true;
  loadSpeechVoices();
  try {
    window.speechSynthesis.addEventListener('voiceschanged', () => {
      const had = speech.english.length;
      loadSpeechVoices();
      if (!had && speech.english.length && isModel(drills.current)) renderDrillCard();
    });
  } catch (e) { /* algunos navegadores no lo emiten */ }
}

/* --------------------------- comparación tolerante ------------------------- */

const CONTRACTION_MAP = {
  "can't": 'cannot', "won't": 'will not', "shan't": 'shall not',
  "i'm": 'i am', "i've": 'i have', "i'll": 'i will', "i'd": 'i would',
  "you're": 'you are', "you've": 'you have', "you'll": 'you will', "you'd": 'you would',
  "he's": 'he is', "she's": 'she is', "it's": 'it is', "that's": 'that is',
  "there's": 'there is', "what's": 'what is', "here's": 'here is',
  "he'll": 'he will', "she'll": 'she will', "it'll": 'it will',
  "we'll": 'we will', "they'll": 'they will',
  "we're": 'we are', "they're": 'they are',
  "we've": 'we have', "they've": 'they have',
  "we'd": 'we would', "they'd": 'they would', "he'd": 'he would',
  "she'd": 'she would', "it'd": 'it would',
  "isn't": 'is not', "aren't": 'are not', "wasn't": 'was not', "weren't": 'were not',
  "don't": 'do not', "doesn't": 'does not', "didn't": 'did not',
  "haven't": 'have not', "hasn't": 'has not', "hadn't": 'had not',
  "couldn't": 'could not', "wouldn't": 'would not', "shouldn't": 'should not',
  "mustn't": 'must not', "let's": 'let us'
};

/* minúsculas, sin puntuación, contracciones expandidas: "I'd" == "I would". */
function normalizeAnswer(text) {
  let t = String(text == null ? '' : text).toLowerCase().replace(/[\u2018\u2019`\u00b4]/g, "'");
  t = t.replace(/[^a-z0-9'\s]/g, ' ');
  t = t.replace(/\s+/g, ' ').trim();
  t = t
    .split(' ')
    .map((w) => (Object.prototype.hasOwnProperty.call(CONTRACTION_MAP, w) ? CONTRACTION_MAP[w] : w))
    .join(' ');
  return t.replace(/\s+/g, ' ').trim();
}

function isCorrectAnswer(given, item) {
  const g = normalizeAnswer(given);
  if (!g) return false;
  const candidates = [item.answer].concat(item.alt_answers || []);
  return candidates.some((c) => normalizeAnswer(c) === g);
}

/* ----------------------------- capa de API -------------------------------- */

class ApiError extends Error {
  constructor(code, detail, status, data) {
    super(detail || code);
    this.code = code || 'error';
    this.detail = detail || '';
    this.status = status || 0;
    this.data = data || null;
  }
}

/* --- mock: mismos nombres de ruta que el backend, datos de static/mock/ --- */

const mock = {
  _state: null,
  _loaded: false,
  _submittedHash: null,
  _cache: {},

  /* Servido por http se usan los .json reales; con file:// el navegador bloquea
     fetch() de ficheros locales, así que se tira de mock/mock-data.js. */
  async file(name) {
    if (Object.prototype.hasOwnProperty.call(this._cache, name)) return clone(this._cache[name]);

    if (location.protocol !== 'file:') {
      try {
        const res = await fetch('mock/' + name + '.json', { cache: 'no-store' });
        if (res.ok) {
          const data = await res.json();
          this._cache[name] = data;
          return clone(data);
        }
      } catch (e) { /* caemos al inline */ }
    }

    const inline = window.__MOCK__ && window.__MOCK__[name];
    if (inline) {
      this._cache[name] = inline;
      return clone(inline);
    }
    throw new ApiError('mock_missing', 'No se pudo cargar el fixture mock/' + name + '.json', 404);
  },

  async state() {
    if (!this._loaded) {
      this._state = await this.file(MOCK_LONG ? 'state-long' : 'state');
      this._loaded = true;
    }
    return this._state;
  },

  async report(body) {
    await sleep(2400);
    const raw = String((body && body.raw) || '');

    if (!/^\s*session\s*:/m.test(raw)) {
      throw new ApiError(
        'yaml_invalid',
        'No se encontró un bloque YAML que empiece por «session:». El texto crudo se ha guardado en data/reports/2026-11-02-1342-raw.txt y la nota del vault no se ha tocado.',
        422
      );
    }
    if (this._submittedHash === raw.trim() || /\b(duplicad[oa]|duplicate)\b/i.test(raw)) {
      throw new ApiError(
        'duplicate',
        'Ya existe una fila con la misma fecha y un informe con el mismo hash (sha256). No se ha escrito nada.',
        409
      );
    }
    this._submittedHash = raw.trim();

    const data = await this.file('report');
    const st = await this.state();
    const session = data.session;
    if (!st.sessions.some((s) => s.date === session.date && s.scenario === session.scenario)) {
      st.sessions.push(session);
    }
    const fresh = (await this.file('drills-new')).items;
    st.drills.items = (st.drills.items || []).concat(fresh);
    st.drills.total = st.drills.items.filter((i) => !i.retired && !isModel(i)).length;
    st.drills.due_now = st.drills.items.filter(
      (i) => !i.retired && !isModel(i) && (!i.due || i.due <= todayISO())
    ).length;
    st.stats = st.stats || {};
    st.stats.sessions = st.sessions.length;
    if (!st.stats.measured) st.stats.measured = {};
    if (session.fillers_per_minute != null) st.stats.measured.fillers_per_minute = session.fillers_per_minute;

    return Object.assign({}, data, { drills_added: fresh.length, state: st });
  },

  answer(body) {
    const item = ((this._state && this._state.drills.items) || []).find((i) => i.id === body.id);
    if (!item) throw new ApiError('not_found', 'Drill ' + body.id + ' no existe.', 404);
    const models = isModel(item);
    if (body.correct) {
      item.streak = (item.streak || 0) + 1;
      item.last_seen = todayISO();
      item.due = null;
      if (item.streak >= 3) item.retired = true;
    } else {
      item.streak = 0;
      item.wrong = (item.wrong || 0) + 1;
      item.last_seen = todayISO();
      const d = new Date();
      d.setDate(d.getDate() + 1);
      item.due = d.toISOString().slice(0, 10);
    }
    if (models) {
      const st = this._state;
      st.stats = st.stats || {};
      const marks = st.stats.models = st.stats.models || { seen: 0, to_review: 0 };
      marks.seen = (Number(marks.seen) || 0) + 1;
      if (!body.correct) marks.to_review = (Number(marks.to_review) || 0) + 1;
      if (st.drills && st.drills.models) {
        st.drills.models.seen = marks.seen;
        st.drills.models.to_review = marks.to_review;
      }
    }
    return { ok: true, item: clone(item) };
  },

  async audio() {
    await sleep(1600);
    if (MOCK_AUDIO_501) {
      const payload = await this.file('audio-501');
      throw new ApiError(payload.error, payload.detail, 501);
    }
    return this.file('audio');
  }
};

function mockShadowCurve(phase) {
  const out = [];
  for (let i = 0; i < 120; i += 1) {
    out.push(i % 19 === 0 ? null : Math.round((3.2 * Math.sin(i / 9 + phase)) * 100) / 100);
  }
  return out;
}

/* Banco y trabajos simulados de la ingesta: `?mock=1` también enseña los pasos sin red. */
let mockShadowPhrases = null;
let mockShadowIngest = null;

function mockShadowBank() {
  if (!mockShadowPhrases) {
    mockShadowPhrases = [
      { id: 'p1', text: 'Even if it has side effects, we should still ship it.', ref: '', has_ref: false },
      { id: 'p2', text: "It is a shared responsibility, not one person's fault.", ref: '', has_ref: false },
      { id: 'p3', text: 'We should probably tell the team before we change the contract.', ref: '', has_ref: false },
      { id: 'p4', text: 'I am not sure I follow you, can you say that again?', ref: '', has_ref: false }
    ];
  }
  return mockShadowPhrases;
}

function mockShadowIngestJob() {
  if (!mockShadowIngest) {
    return { ok: true, id: 'mock-ingest-1', status: 'error', paso: 'error',
      'añadidas': 0, total_candidatas: 0, candidatas: [],
      error: { code: 'ingest_not_found', detail: 'no hay ninguna ingesta simulada' } };
  }
  const started = mockShadowIngest.started;
  const elapsed = Date.now() - started;
  let paso = 'descargando';
  let status = 'running';
  if (elapsed > 2400) {
    status = 'listo';
    paso = 'listo';
    if (!mockShadowIngest.added) {
      mockShadowIngest.added = true;
      mockShadowIngest.candidates.forEach((item) => {
        if (!mockShadowBank().some((p) => p.id === item.pid)) {
          mockShadowBank().push({ id: item.pid, text: item.text, ref: '', has_ref: false });
        }
      });
    }
  } else if (elapsed > 1600) {
    paso = 'cortando';
  } else if (elapsed > 800) {
    paso = 'transcribiendo';
  }
  const candidates = mockShadowIngest && mockShadowIngest.added
    ? mockShadowIngest.candidates : [];
  return {
    ok: true,
    id: 'mock-ingest-1',
    status: status,
    paso: paso,
    'añadidas': candidates.length,
    total_candidatas: 5,
    candidatas: candidates,
    error: null
  };
}

async function mockRequest(method, path, body) {
  if (path === '/api/state') {
    await sleep(120);
    const st = await mock.state();
    return clone(st);
  }
  if (path === '/api/shadow/phrases') {
    return { ok: true, phrases: mockShadowBank() };
  }
  if (path === '/api/shadow/ingest' && method === 'POST') {
    await sleep(200);
    mockShadowIngest = {
      started: Date.now(),
      added: false,
      candidates: [
        { pid: 'y001', text: 'I think we should probably push this to next week.', start: 61.2, end: 64.6, duration: 3.4 },
        { pid: 'y002', text: 'That is exactly what I was trying to say.', start: 71.0, end: 73.8, duration: 2.8 }
      ]
    };
    return { ok: true, id: 'mock-ingest-1', status: 'running' };
  }
  if (path.indexOf('/api/shadow/ingest/') === 0) {
    await sleep(150);
    return mockShadowIngestJob();
  }
  if (path === '/api/shadow/forget' && method === 'POST') {
    await sleep(120);
    const pid = body && body.pid;
    mockShadowPhrases = mockShadowBank().filter((p) => p.id !== pid);
    if (mockShadowIngest) {
      mockShadowIngest.candidates = mockShadowIngest.candidates.filter((c) => c.pid !== pid);
    }
    return { ok: true, pid: pid };
  }
  if (path === '/api/shadow' && method === 'POST') {
    await sleep(220);
    return { ok: true, id: 'mock-shadow-1', status: 'pendiente' };
  }
  if (path.indexOf('/api/shadow/') === 0) {
    await sleep(500);
    return {
      ok: true,
      id: 'mock-shadow-1',
      status: 'listo',
      dev: 'Micro del navegador',
      compare: { kind: 'same_mic', before: 74, dev: 'Micro del navegador' },
      result: {
        mio: { f0: mockShadowCurve(0), dur: 2.4, med: 120 },
        ref: { f0: mockShadowCurve(1.4), dur: 2.7, med: 140 },
        score: 77,
        sonidos_bien: 82,
        transcrito: 'EVEN IF IT HAS SIDE EFFECTS WE SHOULD STILL SHIP IT',
        fonemas: [
          { palabra: 'should', de: 'ʊd', a: 'ul' },
          { palabra: 'ship', de: 'ʃɪp', a: 'ʃip' }
        ]
      }
    };
  }
  if (path === '/api/scenario') {
    await sleep(900);
    const data = await mock.file('scenario');
    let block = data.block_b;
    if (body && body.difficulty) {
      block = block.replace(/\nB2\. Speak/, '\n' + body.difficulty + '. Speak');
    }
    if (body && Array.isArray(body.focus) && body.focus.length) {
      block = block.replace(/^SCENARIO$/m, 'SCENARIO (' + body.focus.join(', ') + ')');
    }
    return Object.assign({}, data, { block_b: block });
  }
  if (path === '/api/report') return mock.report(body);
  if (path === '/api/drill/answer') {
    await sleep(120);
    return mock.answer(body || {});
  }
  if (path === '/api/drill/audio') return mock.audio();
  throw new ApiError('mock_no_route', 'El mock no tiene la ruta ' + method + ' ' + path, 404);
}

async function request(method, path, body) {
  if (MOCK) return mockRequest(method, path, body);

  const opts = { method: method, headers: {} };
  if (body instanceof FormData) {
    opts.body = body;
  } else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }

  let res;
  try {
    res = await fetch(path, opts);
  } catch (err) {
    throw new ApiError('network', 'No se pudo contactar con el backend (127.0.0.1:8770). ¿Está arrancado app.py?', 0);
  }

  const text = await res.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch (e) { data = null; }
  }
  if (!res.ok) {
    throw new ApiError(
      (data && data.error) || 'http_' + res.status,
      (data && data.detail) || (text ? text.slice(0, 300) : ''),
      res.status,
      data
    );
  }
  return data || {};
}

/* ------------------------------- estado ----------------------------------- */

const state = {
  sessions: [],
  patterns: [],
  drills: { due_now: 0, total: 0, retired: 0 },
  stats: { sessions: 0, measured: {} },
  loaded: false
};

const sessionUI = {
  difficulty: 'B2',
  focus: new Set(),
  minutes: 10,
  processing: false
};

const drills = {
  items: [],
  supported: false,
  filter: 'all',
  mode: 'written',
  queue: [],
  index: 0,
  current: null,
  answered: false,
  lastCorrect: null,
  given: '',
  voice: { status: 'idle', result: null, error: null }
};

const mic = { stream: null, recorder: null, chunks: [], timer: null, started: 0 };

const shadowUI = {
  phrases: [],
  phrase: null,
  phase: 'idle',           /* idle | recording | analyzing | done | error */
  takeId: null,
  result: null,
  compare: null,
  dev: '',
  error: '',
  stream: null,
  recorder: null,
  chunks: [],
  ctx: null,
  analyser: null,
  meterRaf: null,
  started: 0,
  runId: 0,
  audio: null
};

/* Ingesta de una URL de YouTube: vive en el backend en un hilo y la UI solo hace polling.
   `run` descarta respuestas de una ingesta anterior (reintentar no mezcla estados). */
const shadowIngest = {
  id: null,
  running: false,
  status: 'idle',          /* idle | running | listo | error */
  paso: '',
  added: 0,
  total: 0,
  candidates: [],
  error: '',
  notice: '',
  url: '',
  run: 0
};

/* ------------------------------- pestañas --------------------------------- */

const TAB_NAMES = ['sesion', 'hablar', 'registro', 'drills', 'shadow'];

function setTab(name, opts) {
  const silent = opts && opts.silent;
  const focusPanel = opts && opts.focusPanel;
  if (TAB_NAMES.indexOf(name) === -1) name = 'sesion';

  TAB_NAMES.forEach((n) => {
    const btn = document.getElementById('tab-' + n);
    const panel = document.getElementById('panel-' + n);
    const on = n === name;
    btn.setAttribute('aria-selected', on ? 'true' : 'false');
    btn.tabIndex = on ? 0 : -1;
    panel.hidden = !on;
  });

  if (!silent) {
    try { history.replaceState(null, '', '#' + name); } catch (e) { /* file:// */ }
  }
  if (focusPanel) {
    const panel = document.getElementById('panel-' + name);
    if (panel) panel.focus({ preventScroll: true });
  }
}

function initTabs() {
  const tablist = $('.tabs');
  const buttons = $$('.tab');

  buttons.forEach((btn) => {
    btn.addEventListener('click', () => {
      setTab(btn.dataset.tab);
      if (btn.dataset.tab === 'drills') focusDrillInput();
      if (btn.dataset.tab === 'shadow' && !shadowUI.phrases.length) {
        loadShadowPhrases().catch(() => renderShadow());
      }
    });
    btn.addEventListener('keydown', (e) => {
      const i = buttons.indexOf(btn);
      let next = null;
      if (e.key === 'ArrowRight') next = buttons[(i + 1) % buttons.length];
      if (e.key === 'ArrowLeft') next = buttons[(i - 1 + buttons.length) % buttons.length];
      if (e.key === 'Home') next = buttons[0];
      if (e.key === 'End') next = buttons[buttons.length - 1];
      if (!next) return;
      e.preventDefault();
      next.focus();
      setTab(next.dataset.tab);
    });
  });

  tablist.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      const btn = e.target.closest('.tab');
      if (btn) { e.preventDefault(); setTab(btn.dataset.tab); }
    }
  });

  window.addEventListener('hashchange', () => {
    setTab((location.hash || '#sesion').slice(1), { silent: true });
  });
}

/* ------------------------------ carga de datos ---------------------------- */

async function loadState() {
  const data = await request('GET', '/api/state');
  applyState(data);
}

function applyState(data) {
  state.sessions = Array.isArray(data.sessions) ? data.sessions : [];
  state.patterns = Array.isArray(data.patterns) ? data.patterns : [];
  state.drills = Object.assign({ due_now: 0, total: 0, retired: 0 }, data.drills || {});
  state.stats = data.stats || { sessions: state.sessions.length, measured: {} };
  if (!state.stats.measured) state.stats.measured = {};
  state.loaded = true;
}

async function loadDrills() {
  let items = Array.isArray(state.drills.items) ? state.drills.items : null;

  if (!items) {
    /* El contrato solo publica contadores; si el backend expone los items en
       /api/drills los usamos, y si no, la pestaña queda en modo contador. */
    try {
      const data = await request('GET', '/api/drills');
      if (Array.isArray(data)) items = data;
      else if (data && Array.isArray(data.items)) items = data.items;
    } catch (e) {
      items = null;
    }
  }

  drills.supported = Array.isArray(items);
  drills.items = drills.supported ? items : [];
  buildDrillFilter();
  buildQueue(false);
  renderDrillCounter();
  renderDrillCard();
  renderDrillFoot();
}

function showGlobalError(err) {
  const main = document.getElementById('main');
  if (!main || document.getElementById('global-error')) return;
  const box = el('div', { class: 'result bad', id: 'global-error', role: 'alert' });
  box.append(el('h3', null, 'No se pudo cargar el estado'));
  box.append(el('p', null, err.detail || err.message || 'Error desconocido.'));
  if (err.code === 'network') {
    box.append(el('p', { class: 'small muted' }, 'Para ver la interfaz sin backend, añade ?mock=1 a la URL.'));
  }
  main.prepend(box);
}

/* -------------------------------- sesión ---------------------------------- */

function initDifficulty() {
  const box = document.getElementById('difficulty');
  ['B1', 'B2', 'C1'].forEach((level) => {
    const btn = el('button', { type: 'button', 'aria-pressed': level === sessionUI.difficulty ? 'true' : 'false', 'data-value': level }, level);
    btn.addEventListener('click', () => {
      sessionUI.difficulty = level;
      $$('button', box).forEach((b) => b.setAttribute('aria-pressed', b.dataset.value === level ? 'true' : 'false'));
    });
    box.append(btn);
  });
}

function initFocus() {
  const box = document.getElementById('focus');
  PATTERN_ORDER.forEach((name) => {
    const input = el('input', { type: 'checkbox', value: name, id: 'focus-' + name });
    input.addEventListener('change', () => {
      if (input.checked) sessionUI.focus.add(name);
      else sessionUI.focus.delete(name);
    });
    const label = el('label', { class: 'chip', for: 'focus-' + name });
    label.append(input, el('span', null, patternLabel(name)));
    box.append(label);
  });
  box.append(el('span', { class: 'small muted' }, 'vacío = sin foco'));

  const clear = document.getElementById('btn-clear-focus');
  if (clear) {
    clear.addEventListener('click', () => {
      box.querySelectorAll('input[type="checkbox"]').forEach((input) => {
        input.checked = false;
      });
      sessionUI.focus.clear();
      clear.blur();
    });
  }
}

/* Portapapeles: API moderna y, si falla (file://, permisos), textarea + execCommand. */
async function copyText(text) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) { /* seguimos con el fallback */ }

  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '-1000px';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, text.length);
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  } catch (e) {
    return false;
  }
}

function setNewStatus(text, isError) {
  const node = document.getElementById('new-status');
  node.textContent = text || '';
  node.style.color = isError ? 'var(--danger)' : '';
}

function renderBrief(data) {
  /* El brief es el asidero: qué defiende, con qué datos y qué le van a objetar. Sin esto el
     escenario es una pregunta abierta y no sabe por dónde empezar. */
  const box = document.getElementById('new-brief');
  if (!box) return;
  box.textContent = '';

  /* Si el modelo no respondió, el escenario es el de reserva: hay que verlo, no adivinarlo. */
  const warnings = ((data && data.warnings) || []).filter(Boolean);
  if (warnings.length) {
    const detalle = warnings.find((w) => w !== 'scenario_fallback' && w !== 'scenario_sin_brief');
    const texto = warnings.includes('scenario_fallback')
      ? 'Escenario de reserva: el modelo no respondió' + (detalle ? ' (' + detalle + ')' : '') +
        '. No es un escenario nuevo; pulsa «Nuevo escenario» otra vez.'
      : 'El modelo no dio brief: se ha rellenado con el texto de reserva.';
    box.append(el('p', { class: 'brief__fallback' }, texto));
  }

  const brief = (data && data.brief) || {};
  const rows = [
    ['Situación', brief.situation],
    ['Lo que defiendes', brief.my_position],
    ['Lo que te juegas', brief.stake]
  ].filter((r) => r[1]);
  if (!rows.length && !warnings.length) { box.hidden = true; return; }

  rows.forEach(([label, value]) => {
    const p = el('p', { class: 'brief__row' });
    p.append(el('span', { class: 'brief__label' }, label), document.createTextNode(value));
    box.append(p);
  });

  const list = (title, items, cls) => {
    if (!Array.isArray(items) || !items.length) return;
    const wrap = el('div', { class: 'brief__block' });
    wrap.append(el('p', { class: 'brief__label' }, title));
    const ul = el('ul', { class: cls || '' });
    items.forEach((i) => ul.append(el('li', null, i)));
    wrap.append(ul);
    box.append(wrap);
  };
  list('Munición (datos que puedes citar)', data.ammo, 'brief__ammo');
  list('Lo que te van a objetar', data.objections, 'brief__objections');
  list('Frases que te pueden servir', data.useful, 'brief__useful');

  if (data.opener) {
    const p = el('p', { class: 'brief__row brief__opener' });
    p.append(el('span', { class: 'brief__label' }, 'Te abre con'), document.createTextNode('«' + data.opener + '»'));
    box.append(p);
  }
  box.hidden = false;
}

async function newSession() {
  if (sessionUI.processing) return;
  const btn = document.getElementById('btn-new');
  btn.disabled = true;
  setNewStatus('generando escenario…', false);

  try {
    const data = await request('POST', '/api/scenario', {
      difficulty: sessionUI.difficulty,
      focus: Array.from(sessionUI.focus),
      minutes: sessionUI.minutes
    });

    const blockB = data.block_b || '';
    const copied = blockB ? await copyText(blockB) : false;

    renderBrief(data);

    /* Si el modelo falló y el escenario es el de reserva, decirlo también arriba: el mensaje
       de "listo" engaña. */
    const reserva = ((data.warnings) || []).includes('scenario_fallback');

    /* No se abre ninguna pestaña: aquí no funciona (bloqueadores, y el prompt no se pega solo).
       El escenario se queda a la vista y el botón "abrir ChatGPT" es un enlace normal que él
       pulsa cuando quiere. */
    const manual = document.getElementById('new-manual');
    if (blockB) {
      manual.hidden = true;    /* el prompt no se enseña: solo el botón de copiar */
      manual.open = false;
      document.getElementById('new-manual-pre').textContent = blockB;
    }

    if (reserva) {
      setNewStatus('escenario de RESERVA: el modelo no respondió · vuelve a pulsar «Nuevo escenario»', true);
    } else if (copied) {
      setNewStatus('escenario listo · prompt copiado, pégalo en ChatGPT', false);
    } else {
      setNewStatus('no se pudo copiar: el prompt está abajo, selecciónalo y pulsa Ctrl+C', true);
      manual.hidden = false;   /* si la copia falla, el prompt se abre para poder seleccionarlo */
      manual.open = true;
      const pre = document.getElementById('new-manual-pre');
      if (pre && window.getSelection) {
        const range = document.createRange();
        range.selectNodeContents(pre);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      }
    }
  } catch (err) {
    setNewStatus('error: ' + (err.detail || err.code || 'desconocido'), true);
  } finally {
    btn.disabled = false;
  }
}

/* --- procesar informe --- */

const progress = { timer: null, started: 0, index: -1 };

function progressNodes() {
  return {
    box: document.getElementById('progress'),
    text: document.getElementById('step-text'),
    elapsed: document.getElementById('elapsed')
  };
}

function startProgress() {
  const n = progressNodes();
  n.box.hidden = false;
  n.text.textContent = STEP_LABELS[0];
  n.elapsed.textContent = '0 s';
  progress.index = 0;
  progress.started = Date.now();

  let slow = document.getElementById('slow-note');
  if (!slow) {
    slow = el('span', { class: 'slow', id: 'slow-note', hidden: true }, 'sigue en marcha; el backend está tardando más de lo normal');
    n.box.append(slow);
  }
  slow.hidden = true;

  clearInterval(progress.timer);
  progress.timer = setInterval(() => {
    const ms = Date.now() - progress.started;
    n.elapsed.textContent = fmtSeconds(ms / 1000);
    let idx = 0;
    for (let i = 0; i < STEP_AT_MS.length; i++) if (ms >= STEP_AT_MS[i]) idx = i;
    if (idx !== progress.index) {
      progress.index = idx;
      n.text.textContent = STEP_LABELS[idx];
    }
    slow.hidden = ms < SLOW_AFTER_MS;
  }, 200);
}

function stopProgress() {
  clearInterval(progress.timer);
  progress.timer = null;
  const n = progressNodes();
  n.box.hidden = true;
  n.text.textContent = '';
}

function mainErrorOf(session) {
  const corrections = (session && session.corrections) || [];
  if (!corrections.length) return '—';
  const counts = {};
  corrections.forEach((c) => {
    const p = c.pattern || 'word-choice';
    counts[p] = (counts[p] || 0) + 1;
  });
  const top = Object.keys(counts).sort((a, b) => counts[b] - counts[a])[0];
  return patternLabel(top) + (counts[top] > 1 ? ' (' + counts[top] + ')' : '');
}

function clearResult() {
  const box = document.getElementById('result');
  box.hidden = true;
  box.textContent = '';
}

function renderSuccess(data) {
  const box = document.getElementById('result');
  box.hidden = false;
  box.classList.remove('bad');
  box.textContent = '';

  const session = data.session || {};
  box.append(el('h3', null, 'Informe procesado'));

  const dl = el('dl');
  dl.append(el('dt', null, 'Fila escrita'));
  dl.append(el('dd', null, (data.row_written === false ? 'no' : (session.date || '—')) + ' · ' + mainErrorOf(session)));
  dl.append(el('dt', null, 'Drills nuevos'));
  dl.append(el('dd', null, String(data.drills_added == null ? 0 : data.drills_added)));
  if (session.scenario) {
    dl.append(el('dt', null, 'Escenario'));
    dl.append(el('dd', null, session.scenario));
  }
  box.append(dl);

  const warnings = (data.warnings || []).filter(Boolean);
  if (warnings.length) {
    const wrap = el('div', { class: 'warnings' });
    wrap.append(el('strong', null, 'Avisos'));
    const ul = el('ul');
    warnings.forEach((w) => ul.append(el('li', null, WARNING_TEXT[w] || w)));
    wrap.append(ul);
    box.append(wrap);
  }

  box.focus({ preventScroll: true });
}

function renderError(err) {
  const box = document.getElementById('result');
  box.hidden = false;
  box.classList.add('bad');
  box.textContent = '';

  const isDuplicate = err.code === 'duplicate' || err.status === 409;
  const isYaml = err.code === 'yaml_invalid' || err.status === 422;

  let title = 'No se pudo procesar';
  let detail = err.detail || err.message || '';

  if (isDuplicate) {
    title = 'Ese informe ya se procesó';
    detail = err.detail || 'Ya existe una fila con esa fecha y el mismo contenido. No se ha escrito nada.';
  } else if (isYaml) {
    title = 'El YAML no se pudo leer';
    detail = err.detail || 'El parser no ha encontrado un bloque válido. El texto crudo está guardado y el vault no se ha tocado.';
  } else if (err.code === 'network') {
    title = 'Sin conexión con el backend';
  } else if (err.code === 'empty') {
    title = 'Nada que procesar';
  }

  box.append(el('h3', null, title));
  box.append(el('p', null, detail));

  const actions = el('div', { class: 'actions' });
  const retry = el('button', { type: 'button', class: 'btn small' }, 'reintentar');
  retry.addEventListener('click', () => processReport());
  actions.append(retry);
  if (isDuplicate) {
    const go = el('button', { type: 'button', class: 'btn small ghost' }, 'ver el registro');
    go.addEventListener('click', () => setTab('registro'));
    actions.append(go);
  }
  box.append(actions);
  box.focus({ preventScroll: true });
}

async function processReport() {
  if (sessionUI.processing) return;

  const raw = document.getElementById('report').value.trim();
  if (!raw) {
    renderError({ code: 'empty', detail: 'Pega primero el informe que ha devuelto ChatGPT.' });
    document.getElementById('report').focus();
    return;
  }

  sessionUI.processing = true;
  const btn = document.getElementById('btn-process');
  btn.disabled = true;
  btn.textContent = 'Procesando…';
  clearResult();
  startProgress();

  try {
    const data = await request('POST', '/api/report', { raw: raw });
    stopProgress();
    renderSuccess(data);

    if (data.state) applyState(data.state);
    else await loadState();

    renderRegistro();
    await loadDrills();
  } catch (err) {
    stopProgress();
    renderError(err);
  } finally {
    sessionUI.processing = false;
    btn.disabled = false;
    btn.textContent = 'Procesar';
  }
}

/* -------------------------------- registro -------------------------------- */

function sortedSessions() {
  return state.sessions.slice().sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
}

function renderSessionsTable(sessions) {
  const wrap = document.getElementById('sessions-wrap');
  wrap.textContent = '';

  if (!sessions.length) {
    wrap.append(el('p', { class: 'nodata' }, 'Todavía no hay sesiones en el registro.'));
    return;
  }

  const table = el('table');
  const thead = el('thead');
  const hr = el('tr');
  ['Fecha', 'Min', 'Dif.', 'Escenario'].forEach((h) => hr.append(el('th', null, h)));
  ['Fluidez', 'Gramática', 'Léxico', 'Organ.', 'Interac.'].forEach((h) => hr.append(el('th', { class: 'num' }, h)));
  thead.append(hr);
  table.append(thead);

  const tbody = el('tbody');
  sessions.slice().reverse().forEach((s) => {
    const tr = el('tr');
    tr.append(el('td', { class: 'date' }, s.date || '—'));
    tr.append(el('td', { class: 'num' }, s.minutes == null ? '—' : String(s.minutes)));
    tr.append(el('td', null, s.difficulty || '—'));
    tr.append(el('td', null, s.scenario || '—'));
    COMPETENCES.forEach(([key]) => tr.append(el('td', { class: 'num' }, fmtScore(s.scores && s.scores[key]))));
    tbody.append(tr);
  });
  table.append(tbody);
  wrap.append(table);
}

function sparkline(values, label, dates) {
  const W = 180;
  const H = 48;
  const PAD = 7;
  const node = svg('svg', {
    viewBox: '0 0 ' + W + ' ' + H,
    preserveAspectRatio: 'none',   /* estira la línea al ancho de su columna */
    class: 'spark',
    role: 'img',
    'aria-label': 'evolución de ' + label + ': ' + values.join(', ') + ' sobre 5'
  });

  const y = (v) => PAD + (H - 2 * PAD) * (1 - (Math.max(1, Math.min(5, v)) - 1) / 4);

  [1, 3, 5].forEach((mark) => {
    node.append(svg('line', { x1: PAD, x2: W - PAD, y1: y(mark), y2: y(mark), class: 'grid' }));
  });

  const step = values.length > 1 ? (W - 2 * PAD) / (values.length - 1) : 0;
  const points = values.map((v, i) => [PAD + i * step, y(v)]);

  node.append(svg('polyline', {
    points: points.map((p) => p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' '),
    class: 'line'
  }));
  points.forEach((p) => node.append(svg('circle', { cx: p[0], cy: p[1], r: 2.4, class: 'dot' })));

  const title = svg('title', {});
  title.textContent = dates && dates.length
    ? dates.join(' · ') + ' — notas: ' + values.join(', ')
    : 'notas: ' + values.join(', ');
  node.append(title);
  return node;
}

function renderCompetences(sessions) {
  const box = document.getElementById('competences');
  box.textContent = '';

  const enough = sessions.length >= MIN_SESSIONS_TREND;

  if (!enough) {
    box.append(el('p', { class: 'nodata', style: 'grid-column:1/-1' },
      'sin datos suficientes — ' + sessions.length + ' de ' + MIN_SESSIONS_TREND +
      ' sesiones para dibujar una tendencia'));
  }

  const dates = sessions.map((s) => s.date);

  COMPETENCES.forEach(([key, label]) => {
    const values = sessions
      .map((s) => Number(s.scores && s.scores[key]))
      .filter((n) => Number.isFinite(n) && n > 0);

    const card = el('div', { class: 'competence' });
    const head = el('div', { class: 'competence-head' });
    head.append(el('span', { class: 'competence-name' }, label));
    head.append(el('span', { class: 'competence-last' }, values.length ? fmtScore(values[values.length - 1]) : '—'));
    card.append(head);

    if (enough && values.length >= 2) {
      card.append(sparkline(values, label, dates));
      card.append(el('div', { class: 'competence-foot' },
        el('span', null, dates[0] || ''),
        el('span', null, dates[dates.length - 1] || '')));
    }

    card.append(el('div', { class: 'competence-foot' },
      el('span', null, values.length + ' notas'),
      el('span', null, 'estimado')));
    box.append(card);
  });
}

function renderPatternBars() {
  const box = document.getElementById('pattern-bars');
  box.textContent = '';

  const patterns = state.patterns.filter((p) => Number(p.attempts) > 0).sort((a, b) => {
    const ia = PATTERN_ORDER.indexOf(a.name);
    const ib = PATTERN_ORDER.indexOf(b.name);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });

  if (!patterns.length) {
    box.append(el('p', { class: 'nodata' }, 'Todavía no hay respuestas de drills por patrón.'));
    renderModelMarks(box);
    return;
  }

  patterns.forEach((p) => {
    const attempts = Number(p.attempts) || 0;
    const correct = Number(p.correct) || 0;
    const rate = typeof p.pass_rate === 'number' ? p.pass_rate : attempts ? correct / attempts : 0;

    const row = el('div', { class: 'bar-row' });
    row.append(el('span', null, patternLabel(p.name)));

    const track = el('div', { class: 'bar-track' });
    const fill = el('div', { class: 'bar-fill' + (rate < 0.4 ? ' low' : rate < 0.7 ? ' mid' : '') });
    fill.style.width = Math.max(0, Math.min(1, rate)) * 100 + '%';
    track.append(fill);
    row.append(track);

    const meta = el('span', { class: 'bar-meta' });
    meta.append(el('span', null, correct + '/' + attempts + ' · ' + Math.round(rate * 100) + ' %'));
    if (p.due) meta.append(el('span', { class: 'due' }, '  vencen ' + p.due));
    row.append(meta);

    box.append(row);
  });

  renderModelMarks(box);
}

/* Línea discreta: las frases modelo no tienen tasa de acierto, solo se miran y se marcan. */
function renderModelMarks(box) {
  const m = (state.stats && state.stats.models) || {};
  const seen = Number(m.seen) || 0;
  const review = Number(m.to_review) || 0;
  if (!seen && !review) return;
  box.append(el('p', { class: 'small muted model-tally' },
    'Frases modelo: ' + plural(seen, 'vista', 'vistas') + ' · ' + plural(review, 'para repasar', 'para repasar')));
}

function renderMeasured() {
  const box = document.getElementById('measured');
  box.textContent = '';
  const m = (state.stats && state.stats.measured) || {};

  if (m.fillers_per_minute == null) {
    box.append(el('p', { class: 'nodata' },
      'Sin métricas de audio todavía. Las notas del informe son estimadas; aquí solo se muestra lo medido.'));
    return;
  }

  const row = el('div', { class: 'bar-row' });
  row.append(el('span', null, 'muletillas por minuto'));
  row.append(el('span', { class: 'competence-last' }, String(m.fillers_per_minute)));
  row.append(el('span', { class: 'bar-meta' }, 'medido'));
  box.append(row);
}

function renderRegistro() {
  const sessions = sortedSessions();
  renderSessionsTable(sessions);
  renderCompetences(sessions);
  renderPatternBars();
  renderMeasured();

  const summary = document.getElementById('registro-summary');
  const total = state.drills.total || 0;
  const due = state.drills.due_now || 0;
  const retired = state.drills.retired || 0;
  summary.textContent = plural(sessions.length, 'sesión', 'sesiones') + ' · ' + total + ' drills (' +
    plural(due, 'vencido', 'vencidos') + ', ' + plural(retired, 'retirado', 'retirados') + ')' +
    (sessions.length ? ' · última: ' + sessions[sessions.length - 1].date : '');
}

/* --------------------------------- drills --------------------------------- */

function buildDrillFilter() {
  const select = document.getElementById('drill-filter');
  const names = PATTERN_ORDER.slice();
  drills.items.forEach((i) => { if (i.pattern && names.indexOf(i.pattern) === -1) names.push(i.pattern); });
  state.patterns.forEach((p) => { if (p.name && names.indexOf(p.name) === -1) names.push(p.name); });

  const previous = drills.filter;
  select.textContent = '';
  select.append(el('option', { value: 'all' }, 'todos los patrones'));
  names.forEach((name) => select.append(el('option', { value: name }, patternLabel(name))));
  if (previous !== 'all' && names.indexOf(previous) === -1) drills.filter = 'all';
  select.value = drills.filter;
}

function buildQueue(includeAll) {
  const today = todayISO();

  if (mic.recorder && mic.recorder.state === 'recording') {
    try { mic.recorder.stop(); } catch (e) { /* noop */ }
  }

  let items = drills.items.filter((i) => !i.retired);
  if (drills.filter !== 'all') items = items.filter((i) => i.pattern === drills.filter);

  let due = items.filter((i) => !i.due || i.due <= today);
  if (!due.length && includeAll) due = items;

  drills.queue = due;
  drills.index = 0;
  drills.current = drills.queue[0] || null;
  drills.answered = false;
  drills.lastCorrect = null;
  drills.given = '';
  drills.voice = { status: 'idle', result: null, error: null };
  stopMic();
}

function remainingModels() {
  if (!drills.supported) return 0;
  const from = drills.index + (drills.answered ? 1 : 0);
  return drills.queue.slice(from).filter(isModel).length;
}

function remainingToday() {
  if (!drills.supported) return state.drills.due_now || 0;
  const rest = drills.queue.length - drills.index - (drills.answered ? 1 : 0);
  return Math.max(0, rest - remainingModels());
}

function renderDrillCounter() {
  const node = document.getElementById('drill-counter');
  const modelsNode = document.getElementById('drill-counter-models');
  node.textContent = String(remainingToday());
  if (modelsNode) {
    const models = remainingModels();
    modelsNode.textContent = models
      ? ' drills y ' + plural(models, 'frase modelo', 'frases modelo')
      : ' drills';
  }
  renderDrillFoot();
}

function renderDrillFoot() {
  const foot = document.getElementById('drill-foot');
  const gaps = drills.items.filter((i) => !i.retired && !isModel(i)).length;
  const models = drills.items.filter((i) => !i.retired && isModel(i)).length;
  const retired = drills.items.filter((i) => i.retired).length;

  if (!drills.supported) {
    foot.textContent = 'El backend no ha expuesto los items de los drills todavía.';
    return;
  }
  foot.textContent = gaps + ' drills' +
    (models ? ' y ' + plural(models, 'frase modelo', 'frases modelo') : '') +
    ' en el mazo · ' + plural(retired, 'retirado', 'retirados') + ' · ' +
    (drills.filter === 'all' ? 'sin filtro' : 'filtro: ' + patternLabel(drills.filter)) +
    ' · enter responde y pasa al siguiente';
}

function promptNode(text) {
  const frag = document.createDocumentFragment();
  const parts = String(text || '').split(/_{2,}/);
  parts.forEach((part, i) => {
    if (i > 0) frag.append(el('span', { class: 'gap', 'aria-label': 'hueco' }, '\u00a0\u00a0\u00a0'));
    if (part) frag.append(document.createTextNode(part));
  });
  return frag;
}

function feedbackNode(item, correct, given) {
  const box = el('div', { class: 'feedback', id: 'drill-feedback', 'aria-live': 'polite' });
  box.append(el('span', { class: 'verdict ' + (correct ? 'ok' : 'bad') }, correct ? 'acierto' : 'fallo'));
  box.append(el('span', { class: 'correct' }, item.answer));
  if (given) box.append(el('span', { class: 'said' }, 'has escrito: ' + given));
  if (item.source && item.source.better) box.append(el('span', { class: 'said' }, 'frase de la sesión: ' + item.source.better));
  return box;
}

function nextButton() {
  const btn = el('button', { type: 'button', class: 'btn primary', id: 'btn-next' }, 'Siguiente');
  btn.addEventListener('click', () => nextDrill());
  return btn;
}

/* --- tarjeta modelo: mirar, escuchar y marcar a mano (no es un examen) --- */

function modelArea(item, revealed) {
  const box = el('div', { class: 'model' });

  const said = el('p', { class: 'model-said' });
  said.append(el('span', { class: 'model-said__label' }, 'Tú dijiste: '));
  said.append(el('span', { class: 'model-said__text' }, '«' + item.prompt + '»'));
  box.append(said);

  const natural = el('p', {
    class: 'model-natural', id: 'model-natural', hidden: !revealed
  }, item.answer);

  const tools = el('div', { class: 'model-tools' });
  if (!revealed) {
    const reveal = el('button', {
      type: 'button', class: 'btn small', id: 'btn-model-reveal',
      'aria-expanded': 'false', 'aria-controls': 'model-natural'
    }, 'ver la versión natural');
    reveal.addEventListener('click', () => {
      const show = natural.hidden;
      natural.hidden = !show;
      reveal.setAttribute('aria-expanded', show ? 'true' : 'false');
      reveal.textContent = show ? 'ocultar la versión natural' : 'ver la versión natural';
    });
    tools.append(reveal);
  }
  const listen = el('button', {
    type: 'button', class: 'btn small ghost', id: 'btn-model-listen',
    'aria-label': 'escuchar la versión natural'
  }, 'escuchar');
  listen.addEventListener('click', () => speakModel(item.answer, listen));
  tools.append(listen);
  if (tools.childNodes.length) box.append(tools);
  box.append(natural);
  return box;
}

function modelMarks() {
  const row = el('div', { class: 'model-marks' });
  const had = el('button', { type: 'button', class: 'btn primary', id: 'btn-model-had' }, 'lo tenía');
  had.addEventListener('click', () => markModel(true));
  const again = el('button', { type: 'button', class: 'btn', id: 'btn-model-review' }, 'para repasar');
  again.addEventListener('click', () => markModel(false));
  row.append(had, again);
  return row;
}

function markModel(correct) {
  const item = drills.current;
  if (!item || drills.answered) return;
  drills.answered = true;
  drills.lastCorrect = correct;
  postAnswer(item.id, correct);
  renderDrillCard();
  renderDrillCounter();
}

/* --- modo voz --- */

function updateMicTimer() {
  const node = document.getElementById('rec-time');
  if (node) node.textContent = fmtSeconds((Date.now() - mic.started) / 1000);
}

function stopMic() {
  clearInterval(mic.timer);
  mic.timer = null;
  if (mic.stream) {
    mic.stream.getTracks().forEach((t) => t.stop());
    mic.stream = null;
  }
}

function pickMime() {
  if (!window.MediaRecorder || typeof MediaRecorder.isTypeSupported !== 'function') return '';
  const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
  for (const m of candidates) if (MediaRecorder.isTypeSupported(m)) return m;
  return '';
}

async function startRecording() {
  if (!drills.current || drills.answered) return;

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === 'undefined') {
    drills.voice = { status: 'unavailable', error: 'Este navegador no permite grabar audio desde la página.' };
    renderDrillCard();
    return;
  }

  drills.voice = { status: 'asking', error: null, result: null };
  renderDrillCard();

  try {
    mic.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    drills.voice = { status: 'error', error: 'No se pudo acceder al micrófono. Revisa el permiso del navegador.', result: null };
    renderDrillCard();
    return;
  }

  const mime = pickMime();
  try {
    mic.recorder = mime ? new MediaRecorder(mic.stream, { mimeType: mime }) : new MediaRecorder(mic.stream);
  } catch (err) {
    stopMic();
    drills.voice = { status: 'unavailable', error: 'No se pudo iniciar el grabador.' };
    renderDrillCard();
    return;
  }

  mic.chunks = [];
  mic.recorder.addEventListener('dataavailable', (e) => {
    if (e.data && e.data.size) mic.chunks.push(e.data);
  });
  mic.recorder.addEventListener('stop', onRecordingStop);
  mic.recorder.start();
  mic.started = Date.now();

  drills.voice = { status: 'recording', error: null, result: null };
  renderDrillCard();
  clearInterval(mic.timer);
  mic.timer = setInterval(updateMicTimer, 250);
}

function stopRecording() {
  if (!mic.recorder) return;
  if (mic.recorder.state !== 'inactive') mic.recorder.stop();
  clearInterval(mic.timer);
  mic.timer = null;
}

async function onRecordingStop() {
  const type = (mic.recorder && mic.recorder.mimeType) || 'audio/webm';
  const blob = new Blob(mic.chunks, { type: type });
  const ext = type.indexOf('wav') !== -1 ? 'wav' : type.indexOf('mp4') !== -1 ? 'm4a' : 'webm';
  const item = drills.current;

  stopMic();

  if (!item) return;
  if (!blob.size) {
    drills.voice = { status: 'error', error: 'La grabación ha salido vacía. Prueba otra vez.', result: null };
    renderDrillCard();
    return;
  }

  drills.voice = { status: 'uploading', error: null, result: null };
  renderDrillCard();

  const fd = new FormData();
  fd.append('id', item.id);
  fd.append('file', blob, 'drill.' + ext);

  try {
    const result = await request('POST', '/api/drill/audio', fd);
    if (result.close) {
      /* Casi: no cuenta como fallo. Reintentar no penaliza. */
      drills.voice = { status: 'close', result: result, error: null };
      renderDrillCard();
      return;
    }
    drills.voice = { status: 'done', result: result, error: null };
    drills.answered = true;
    drills.lastCorrect = !!result.match;
    if (REGISTER_AUDIO_ANSWERS) postAnswer(item.id, !!result.match);
    renderDrillCard();
    renderDrillCounter();
  } catch (err) {
    if (err.status === 501 || err.code === 'audio_unavailable') {
      drills.voice = { status: 'unavailable', error: err.detail || 'El motor de transcripción no está disponible.', result: null };
    } else {
      drills.voice = { status: 'error', error: err.detail || err.code || 'error al enviar el audio', result: null };
    }
    renderDrillCard();
  }
}

function voiceArea(item) {
  const area = el('div', { class: 'voice-area' });
  const v = drills.voice;

  if (v.status === 'idle') {
    area.append(el('button', { type: 'button', class: 'btn rec-btn', id: 'btn-record', onclick: startRecording },
      el('span', { class: 'rec-dot', 'aria-hidden': 'true' }),
      'Grabar'));
    area.append(el('p', { class: 'small muted', style: 'margin-top:8px' },
      'Di la frase entera en inglés. Se te pide el micrófono al pulsar, no antes.'));
    return area;
  }

  if (v.status === 'asking') {
    area.append(el('p', { class: 'small muted' }, 'pidiendo permiso para el micrófono…'));
    return area;
  }

  if (v.status === 'recording') {
    const btn = el('button', { type: 'button', class: 'btn rec-btn recording', id: 'btn-stop', onclick: stopRecording },
      el('span', { class: 'rec-dot', 'aria-hidden': 'true' }),
      'Parar',
      el('span', { class: 'rec-time', id: 'rec-time' }, fmtSeconds((Date.now() - mic.started) / 1000)));
    area.append(btn);
    area.append(el('p', { class: 'small muted', style: 'margin-top:8px' }, 'grabando…'));
    return area;
  }

  if (v.status === 'uploading') {
    area.append(el('p', { class: 'small muted' }, 'transcribiendo…'));
    const box = el('div', { class: 'progress' });
    box.append(el('span', { class: 'spinner', 'aria-hidden': 'true' }));
    area.append(box);
    return area;
  }

  if (v.status === 'done') {
    const result = v.result || {};
    const words = Array.isArray(result.words) ? result.words : null;

    if (words && words.length) {
      const p = el('p', { class: 'transcript' });
      words.forEach((w) => {
        const span = el('span', { class: w.ok ? 'w-ok' : 'w-bad', title: w.ok ? '' : 'esperado: ' + (w.expected || '') }, w.heard || w.expected || '');
        p.append(span, ' ');
      });
      area.append(p);
    } else if (result.transcript) {
      area.append(el('p', { class: 'transcript' }, result.transcript));
    }

    const meta = el('p', { class: 'audio-meta' });
    const bits = [];
    if (result.duration_s != null) bits.push(Number(result.duration_s).toFixed(1).replace('.', ',') + ' s');
    if (result.pauses != null) bits.push(result.pauses + (result.pauses === 1 ? ' pausa' : ' pausas'));
    if (result.engine) bits.push(result.engine);
    meta.textContent = bits.join(' · ');
    area.append(meta);

    if (!result.match) {
      area.append(el('p', { class: 'audio-meta', style: 'margin-top:6px' }, 'esperado: ' + (result.expected || item.answer)));
    }
    return area;
  }

  if (v.status === 'close') {
    const result = v.result || {};
    area.append(el('p', { class: 'audio-meta' }, 'casi, pero no: he oído «' + (result.transcript || '¿?') + '»'));
    area.append(el('p', { class: 'small muted', style: 'margin-top:4px' },
      'esperado: ' + (result.expected || item.answer) + '. No cuenta como fallo, repítelo.'));
    const again = el('button', { type: 'button', class: 'btn rec-btn', id: 'btn-record', onclick: startRecording },
      el('span', { class: 'rec-dot', 'aria-hidden': 'true' }),
      'Grabar otra vez');
    again.style.marginTop = '10px';
    area.append(again);
    return area;
  }

  if (v.status === 'unavailable') {
    area.append(el('p', { class: 'small muted' }, 'Voz no disponible: ' + (v.error || '')));
    area.append(el('p', { class: 'small' }, 'Modo alternativo: ¿te la sabías?'));
    const row = el('div', { class: 'selfreport' });
    const yes = el('button', { type: 'button', class: 'btn' }, 'me lo sé');
    yes.addEventListener('click', () => selfReport(true));
    const no = el('button', { type: 'button', class: 'btn' }, 'no me lo sé');
    no.addEventListener('click', () => selfReport(false));
    row.append(yes, no);
    area.append(row);
    return area;
  }

  /* error */
  area.append(el('p', { class: 'small', style: 'color:var(--danger)' }, v.error || 'Error de audio.'));
  const retry = el('button', { type: 'button', class: 'btn' }, 'volver a grabar');
  retry.addEventListener('click', () => {
    drills.voice = { status: 'idle', result: null, error: null };
    renderDrillCard();
  });
  area.append(retry);
  return area;
}

function selfReport(correct) {
  const item = drills.current;
  if (!item || drills.answered) return;
  drills.answered = true;
  drills.lastCorrect = correct;
  postAnswer(item.id, correct);
  renderDrillCard();
  renderDrillCounter();
}

function postAnswer(id, correct) {
  request('POST', '/api/drill/answer', { id: id, correct: !!correct }).catch(() => {
    /* el feedback ya está en pantalla; no bloqueamos por el registro */
  });
}

function submitWritten() {
  const item = drills.current;
  const input = document.getElementById('drill-input');
  if (!item || !input || drills.answered) return;

  const value = input.value.trim();
  if (!value) { input.focus(); return; }

  const correct = isCorrectAnswer(value, item);
  drills.answered = true;
  drills.lastCorrect = correct;
  drills.given = value;
  postAnswer(item.id, correct);
  renderDrillCard();
  renderDrillCounter();
}

function nextDrill() {
  stopMic();
  drills.index += 1;
  drills.current = drills.queue[drills.index] || null;
  drills.answered = false;
  drills.lastCorrect = null;
  drills.given = '';
  drills.voice = { status: 'idle', result: null, error: null };
  renderDrillCard();
  renderDrillCounter();
  focusDrillInput();
}

function focusDrillInput() {
  if (drills.mode !== 'written' || drills.answered) return;
  const input = document.getElementById('drill-input');
  if (input) input.focus({ preventScroll: true });
}

function renderDrillCard() {
  const card = document.getElementById('drill-card');
  if (!card) return;
  card.textContent = '';

  if (!drills.supported) {
    card.append(el('p', { class: 'empty' },
      'El backend no ha devuelto los items de los drills. Cuando exponga /api/drills o drills.items en /api/state, aparecerán aquí.'));
    return;
  }

  if (!drills.items.length) {
    card.append(el('p', { class: 'empty' }, 'No hay drills en el mazo. Procesa un informe para generarlos.'));
    return;
  }

  const item = drills.current;
  if (!item) {
    const others = drills.items.filter((i) => !i.retired && (drills.filter === 'all' || i.pattern === drills.filter));
    card.append(el('p', { class: 'empty' }, 'No quedan drills para hoy.'));
    if (others.length) {
      const btn = el('button', { type: 'button', class: 'btn small', style: 'margin-top:10px' }, 'practicar repasos (' + others.length + ')');
      btn.addEventListener('click', () => {
        drills.queue = others;
        drills.index = 0;
        drills.current = drills.queue[0];
        drills.answered = false;
        drills.given = '';
        drills.voice = { status: 'idle', result: null, error: null };
        renderDrillCard();
        renderDrillCounter();
      });
      card.append(btn);
    }
    return;
  }

  const meta = el('div', { class: 'drill-meta' });
  meta.append(el('span', { class: 'tag' }, patternLabel(item.pattern)));
  if (isModel(item)) {
    meta.append(el('span', { class: 'small muted' }, 'frase modelo'));
  } else if (item.source && item.source.said) {
    meta.append(el('span', { class: 'small muted' }, 'de «' + item.source.said + '»'));
  }
  card.append(meta);

  /* Frase modelo: no hay hueco ni respuesta que escribir. Se lee, se escucha y se marca. */
  if (isModel(item)) {
    card.append(modelArea(item, !!drills.answered));
    if (drills.answered) {
      card.append(el('p', { class: 'model-marked' },
        drills.lastCorrect ? 'marcada' : 'marcada: vuelve mañana'));
      const actions = el('div', { class: 'actions', style: 'margin-top:16px' });
      actions.append(nextButton());
      card.append(actions);
    } else {
      card.append(modelMarks());
    }
    return;
  }

  const prompt = el('p', { class: 'drill-prompt' });
  prompt.append(promptNode(item.prompt));
  card.append(prompt);

  if (drills.mode === 'written') {
    const form = el('form', { class: 'answer-row', id: 'drill-form' });
    const input = el('input', {
      type: 'text',
      id: 'drill-input',
      autocomplete: 'off',
      autocapitalize: 'off',
      autocorrect: 'off',
      spellcheck: 'false',
      'aria-label': 'Tu respuesta en inglés',
      placeholder: 'responde en inglés',
      disabled: drills.answered
    });
    input.value = drills.given || '';
    const submit = el('button', { type: 'submit', class: 'btn primary', id: 'btn-answer', disabled: drills.answered }, 'Responder');
    form.append(input, submit);
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      submitWritten();
    });
    card.append(form);
  } else {
    card.append(voiceArea(item));
  }

  if (drills.answered) {
    card.append(feedbackNode(item, !!drills.lastCorrect, drills.mode === 'written' ? drills.given : ''));
    const actions = el('div', { class: 'actions', style: 'margin-top:16px' });
    actions.append(nextButton());
    card.append(actions);
  }

  if (!drills.answered && drills.mode === 'written') {
    setTimeout(() => {
      const input = document.getElementById('drill-input');
      if (input && document.getElementById('panel-drills') && !document.getElementById('panel-drills').hidden) {
        input.focus({ preventScroll: true });
      }
    }, 0);
  }
}

function initDrillControls() {
  const select = document.getElementById('drill-filter');
  select.addEventListener('change', () => {
    drills.filter = select.value;
    buildQueue(false);
    renderDrillCard();
    renderDrillCounter();
    renderDrillFoot();
    focusDrillInput();
  });

  ['written', 'voice'].forEach((mode) => {
    const btn = document.getElementById('mode-' + mode);
    btn.addEventListener('click', () => {
      if (drills.mode === mode) return;
      if (mic.recorder && mic.recorder.state === 'recording') stopRecording();
      stopMic();
      drills.mode = mode;
      drills.voice = { status: 'idle', result: null, error: null };
      document.getElementById('mode-written').setAttribute('aria-pressed', mode === 'written' ? 'true' : 'false');
      document.getElementById('mode-voice').setAttribute('aria-pressed', mode === 'voice' ? 'true' : 'false');
      renderDrillCard();
      focusDrillInput();
    });
  });

  /* Enter: responde en el input (submit del form) y, ya respondido, pasa al siguiente. */
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return;
    if (document.getElementById('panel-drills').hidden) return;

    const active = document.activeElement;
    if (active && ['TEXTAREA', 'BUTTON', 'SELECT'].indexOf(active.tagName) !== -1) return;
    if (!drills.current) return;

    if (drills.answered) {
      e.preventDefault();
      nextDrill();
    }
  });
}

/* -------------------------------- shadowing -------------------------------- */

const SHADOW_POINTS = 120;
const SHADOW_POLL_MS = 1000;
const SHADOW_MAX_POLLS = 150;

function shadowPhrase() {
  const select = document.getElementById('shadow-phrase');
  if (!select) return null;
  return shadowUI.phrases.find((p) => p.id === select.value) || shadowUI.phrases[0] || null;
}

function shadowErrorLine(err) {
  const code = err && err.code;
  if (code === 'shadow_unavailable') {
    return 'El análisis no está disponible en este servidor: falta el analizador de shadowing.';
  }
  if (code === 'no_voice') return 'No se ha detectado voz en tu toma. Acércate al micro y repítela.';
  if (code === 'reference_missing' || code === 'reference_silent') {
    return 'La referencia de esta frase no se puede analizar.';
  }
  if (code === 'take_missing') return 'No se encuentra el audio de esa toma.';
  if (err && err.detail) return err.detail;
  if (err && err.message) return err.message;
  return 'No se pudo analizar la toma.';
}

function shadowMotion(values) {
  const list = values.filter((v) => typeof v === 'number' && isFinite(v));
  if (list.length < 2) return null;
  const mean = list.reduce((a, b) => a + b, 0) / list.length;
  const variance = list.reduce((a, b) => a + (b - mean) * (b - mean), 0) / list.length;
  return Math.sqrt(variance);
}

function shadowSegments(values) {
  const out = [];
  let current = null;
  values.forEach((value, index) => {
    const number = typeof value === 'number' && isFinite(value) ? value : null;
    if (number === null) {
      current = null;
      return;
    }
    if (!current) {
      current = [];
      out.push(current);
    }
    current.push([index, number]);
  });
  return out;
}

function shadowCompareLine(now, compare) {
  const kind = compare && compare.kind;
  if (kind === 'same_mic' && typeof compare.before === 'number') {
    return 'antes ' + Math.round(compare.before) + '% → ahora ' + now + '%';
  }
  if (kind === 'first') return 'primera toma de esta frase con este micro';
  if (kind === 'other_mic') return 'no comparable: la última vez usaste otro micro';
  if (kind === 'unknown_mic') return 'no comparable: la última toma no guardó el micro';
  if (kind === 'no_score') return 'no comparable: la última toma no trajo número';
  if (kind === 'no_dev') return 'no comparable: esta toma no guardó el micro';
  return '';
}

/* Cómo suena cada símbolo, en castellano: el IPA solo no sirve para aprender. */
const IPA_HINTS = {
  'æ': 'la a de «cat», tirando a e',
  'ə': 'la vocal floja de «the», casi un «uh»',
  'ʌ': 'la a corta de «cup»',
  'ʊ': 'la u corta de «book»',
  'u': 'la u larga de «food»',
  'ɪ': 'la i corta de «ship»',
  'i': 'la i larga de «sheep»',
  'iː': 'la i larga de «sheep»',
  'ʃ': 'la sh de «ship»',
  'ʒ': 'la j francesa de «jour»',
  'ɲ': 'la ñ del castellano',
  'θ': 'la z de «zapato» en España',
  'ð': 'la d suave de «nada»',
  'ŋ': 'la n de «sing», con la lengua atrás',
  'ɹ': 'la r inglesa, lengua sin tocar el paladar',
  'r': 'la r inglesa, lengua sin tocar el paladar',
  'z': 'la s sonora de «zoo»',
  's': 'la s de «see»',
  'v': 'la v de «very»: labio inferior contra los dientes',
  'b': 'la b de «bed»',
  'w': 'la w de «we», labios redondos',
  'j': 'la y de «you»',
  'ɛ': 'la e abierta de «bed»',
  'ɑ': 'la a de «father»',
  'ɔ': 'la o de «thought»',
  'ɜ': 'la vocal de «bird»',
  'ɚ': 'la vocal floja de «butter»',
  'd': 'la d de «day»',
  't': 'la t de «tea», con soplo',
  'l': 'la l de «let»',
  'k': 'la c de «cat»',
  'g': 'la g de «go»',
  'f': 'la f de «find»',
  'h': 'la h aspirada de «hat»',
  'm': 'la m de «man»',
  'n': 'la n de «no»',
  'p': 'la p de «pen», con soplo',
  'eɪ': 'el diptongo de «day»',
  'aɪ': 'el diptongo de «my»',
  'oʊ': 'el diptongo de «go»',
  'aʊ': 'el diptongo de «now»',
  'ɔɪ': 'el diptongo de «boy»'
};

function ipaHints(heard, expected) {
  const seen = [];
  const candidates = [];
  String(expected || '').split('').forEach((ch) => candidates.push(ch));
  for (let i = 0; i < String(expected || '').length - 1; i += 1) {
    candidates.push(String(expected).slice(i, i + 2));
  }
  candidates.forEach((sym) => {
    const hint = IPA_HINTS[sym];
    if (hint && seen.indexOf(sym) === -1) seen.push(sym + ': ' + hint);
  });
  return seen.slice(0, 2);
}

function playWordAudio(word, button) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = '…';
  const restore = () => { button.disabled = false; button.textContent = label; };
  fetch('/api/shadow/word?w=' + encodeURIComponent(word))
    .then((r) => r.json())
    .then((data) => {
      if (!data || !data.ok || !data.url) throw new Error('sin audio');
      const audio = new Audio(data.url);
      audio.onended = restore;
      audio.onerror = restore;
      audio.play().catch(restore);
    })
    .catch(restore);
}

function renderShadowFeedback(result, compare) {
  const box = document.getElementById('shadow-score');
  if (!box) return;

  const hasScore = result && typeof result.sonidos_bien === 'number' &&
    isFinite(result.sonidos_bien);
  box.hidden = !hasScore;
  if (!hasScore) return;

  const now = Math.round(result.sonidos_bien);
  const big = document.getElementById('shadow-big');
  if (big) big.textContent = now + '%';

  const compareBox = document.getElementById('shadow-compare');
  if (compareBox) compareBox.textContent = shadowCompareLine(now, compare);

  const misses = document.getElementById('shadow-misses');
  if (!misses) return;
  misses.textContent = '';
  const list = Array.isArray(result.fonemas) ? result.fonemas.slice(0, 4) : [];
  if (!list.length) {
    misses.append(el('p', { class: 'shadow__miss shadow__miss--none' },
      'ningún sonido claramente fuera'));
    return;
  }
  misses.append(el('p', { class: 'shadow__miss-hint' },
    'Pulsa ▶ para oír cómo tiene que sonar la palabra.'));
  list.forEach((item) => {
    const word = (item && item.palabra) || '?';
    const heard = (item && item.a) || '?';
    const expected = (item && item.de) || '?';
    const row = el('div', { class: 'shadow__miss' },
      'en «' + word + '» dijiste ',
      el('em', { text: heard }),
      ' en vez de ',
      el('em', { text: expected })
    );
    const play = el('button', {
      class: 'shadow__play', type: 'button',
      title: 'oír «' + word + '»',
      'aria-label': 'oír ' + word
    }, '▶');
    play.addEventListener('click', () => playWordAudio(word, play));
    row.prepend(play);
    const hints = ipaHints(heard, expected);
    if (hints.length) {
      row.append(el('span', { class: 'shadow__hint' }, hints.join(' · ')));
    }
    misses.append(row);
  });
}

function renderShadowChart(result) {
  const box = document.getElementById('shadow-chart');
  if (!box) return;
  box.textContent = '';
  const note = document.getElementById('shadow-note');

  const mine = (result && result.mio && result.mio.f0) || [];
  const ref = (result && result.ref && result.ref.f0) || [];
  const all = mine.concat(ref).filter((v) => typeof v === 'number' && isFinite(v));
  if (!all.length) {
    if (note) { note.textContent = ''; note.hidden = true; }
    return;
  }

  const sorted = all.map((v) => Math.abs(v)).sort((a, b) => a - b);
  const p95 = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] || 2;
  /* eje Y compartido y simétrico, en semitonos sobre la mediana de cada voz; el
     percentil 95 evita que un salto de octava aplaste la forma. Sin números en pantalla. */
  const span = Math.max(2, Math.min(12, p95));
  const W = 720;
  const H = 230;
  const padX = 14;
  const padY = 20;
  const mid = H / 2;
  const half = (H - 2 * padY) / 2;
  const xOf = (i) => padX + (i / (SHADOW_POINTS - 1)) * (W - 2 * padX);
  const yOf = (v) => mid - (Math.max(-span, Math.min(span, v)) / span) * half;

  const node = svg('svg', {
    viewBox: '0 0 ' + W + ' ' + H,
    class: 'shadow-svg',
    role: 'img',
    'aria-label': 'curvas de melodía: tu toma y la referencia, sin escala numérica'
  });

  [span, 0, -span].forEach((mark) => {
    node.append(svg('line', {
      x1: padX, x2: W - padX, y1: yOf(mark), y2: yOf(mark),
      class: mark === 0 ? 'shadow-grid shadow-grid--zero' : 'shadow-grid'
    }));
  });

  const draw = (values, cls) => {
    shadowSegments(values).forEach((segment) => {
      if (segment.length < 2) return;
      const d = segment.map(([i, v], k) => (k ? 'L' : 'M') + xOf(i).toFixed(1) + ' ' + yOf(v).toFixed(1)).join(' ');
      node.append(svg('path', { d: d, class: 'shadow-line ' + cls }));
    });
  };

  draw(ref, 'shadow-line--ref');
  draw(mine, 'shadow-line--mine');
  box.append(node);

  const mineMotion = shadowMotion(mine);
  const refMotion = shadowMotion(ref);
  let line = '';
  if (mineMotion != null && refMotion != null && refMotion > 0.12) {
    if (mineMotion < refMotion * 0.72) line = 'tu línea se mueve menos que la suya';
    else if (mineMotion > refMotion * 1.38) line = 'tu línea se mueve más que la suya';
  }
  if (note) {
    note.textContent = line;
    note.hidden = !line;
  }
}

function shadowSourceLine(phrase) {
  if (!phrase || !phrase.source) return '';
  const seconds = Number(phrase.duration);
  if (!isFinite(seconds) || seconds <= 0) return 'fuente: ' + phrase.source;
  return 'fuente: ' + phrase.source + ' · ' + seconds.toFixed(1).replace('.', ',') + ' s';
}

/* ------------------------- ingesta desde una URL ------------------------- */

const SHADOW_INGEST_POLL_MS = 1000;
const SHADOW_INGEST_MAX_POLLS = 900;

function shadowIngestErrorLine(err) {
  const text = (err && (err.detail || err.message || err.code)) ||
    'no se pudieron añadir las frases';
  return String(text).replace(/\s+/g, ' ').trim();
}

function shadowIngestLine() {
  if (shadowIngest.running) {
    const labels = {
      descargando: 'descargando…',
      transcribiendo: 'transcribiendo…',
      cortando: 'cortando…'
    };
    return labels[shadowIngest.paso] || 'preparando…';
  }
  if (shadowIngest.status === 'error') return shadowIngest.error || 'la ingesta falló';
  if (shadowIngest.notice) return shadowIngest.notice;
  if (shadowIngest.status === 'listo') {
    if (!shadowIngest.added) return 'listo: ninguna frase pasó el filtro';
    if (shadowIngest.total > 0) {
      return 'listo: ' + shadowIngest.added + ' frases de ' +
        shadowIngest.total + ' candidatas';
    }
    return 'listo: ' + shadowIngest.added + ' frases nuevas';
  }
  return '';
}

function renderShadowIngest() {
  const status = document.getElementById('shadow-ingest-status');
  const button = document.getElementById('btn-shadow-ingest');
  const retry = document.getElementById('btn-shadow-ingest-retry');
  const list = document.getElementById('shadow-candidates');
  if (!status) return;

  status.textContent = shadowIngestLine();
  status.classList.toggle('is-error', shadowIngest.status === 'error');
  if (button) {
    button.disabled = shadowIngest.running;
    button.textContent = shadowIngest.running ? 'añadiendo…' : 'añadir frases';
  }
  if (retry) {
    retry.hidden = !(shadowIngest.status === 'error' && !!shadowIngest.url);
  }
  if (!list) return;

  list.textContent = '';
  const items = Array.isArray(shadowIngest.candidates) ? shadowIngest.candidates : [];
  list.hidden = !items.length;
  items.forEach((item) => {
    const pid = item.pid || item.id;
    const row = el('div', { class: 'shadow__cand' });
    row.append(el('span', { class: 'shadow__cand-text', lang: 'en' }, item.text || ''));
    const seconds = Number(item.duration);
    if (isFinite(seconds) && seconds > 0) {
      row.append(el('span', { class: 'shadow__cand-meta' },
        seconds.toFixed(1).replace('.', ',') + ' s'));
    }
    const remove = el('button', {
      class: 'shadow__cand-remove',
      type: 'button',
      'aria-label': 'quitar la frase: ' + (item.text || '')
    }, 'quitar');
    remove.addEventListener('click', () => shadowIngestForget(pid, remove));
    row.append(remove);
    list.append(row);
  });
}

async function shadowIngestStart() {
  if (shadowIngest.running) return;
  const input = document.getElementById('shadow-url');
  const url = (input && input.value ? input.value : '').trim();
  if (!url) {
    shadowIngest.status = 'error';
    shadowIngest.error = 'pega un enlace de YouTube';
    shadowIngest.url = '';
    renderShadowIngest();
    if (input) input.focus();
    return;
  }

  shadowIngest.run += 1;
  const run = shadowIngest.run;
  shadowIngest.id = null;
  shadowIngest.running = true;
  shadowIngest.status = 'running';
  shadowIngest.paso = 'descargando';
  shadowIngest.added = 0;
  shadowIngest.total = 0;
  shadowIngest.candidates = [];
  shadowIngest.error = '';
  shadowIngest.notice = '';
  shadowIngest.url = url;
  renderShadowIngest();

  try {
    const out = await request('POST', '/api/shadow/ingest', { url: url });
    if (run !== shadowIngest.run) return;
    shadowIngest.id = out.id;
    await shadowIngestPoll(out.id, run, 0);
  } catch (err) {
    if (run !== shadowIngest.run) return;
    shadowIngest.running = false;
    shadowIngest.status = 'error';
    shadowIngest.error = shadowIngestErrorLine(err);
    renderShadowIngest();
  }
}

async function shadowIngestPoll(id, run, tries) {
  if (run !== shadowIngest.run) return;
  let job = null;
  try {
    job = await request('GET', '/api/shadow/ingest/' + encodeURIComponent(id));
  } catch (err) {
    if (run !== shadowIngest.run) return;
    shadowIngest.running = false;
    shadowIngest.status = 'error';
    shadowIngest.error = shadowIngestErrorLine(err);
    renderShadowIngest();
    return;
  }
  if (run !== shadowIngest.run) return;

  shadowIngest.status = job.status || 'running';
  shadowIngest.paso = job.paso || '';
  shadowIngest.added = Number(job['añadidas'] || job.anadidas) || 0;
  shadowIngest.total = Number(job.total_candidatas) || 0;
  shadowIngest.candidates = Array.isArray(job.candidatas) ? job.candidatas : [];
  if (job.error && job.error.detail) {
    shadowIngest.error = shadowIngestErrorLine(job.error);
  }

  if (job.status === 'listo') {
    shadowIngest.running = false;
    shadowIngest.error = '';
    renderShadowIngest();
    const first = shadowIngest.candidates[0];
    /* Si hay una toma en curso, no se cambia la frase bajo los pies del micro. */
    const busy = shadowUI.phase === 'recording' || shadowUI.phase === 'analyzing';
    await loadShadowPhrases(busy ? undefined : (first ? (first.pid || first.id) : undefined));
    return;
  }
  if (job.status === 'error') {
    shadowIngest.running = false;
    if (!shadowIngest.error) shadowIngest.error = 'la ingesta falló';
    renderShadowIngest();
    return;
  }

  renderShadowIngest();
  if (tries >= SHADOW_INGEST_MAX_POLLS) {
    shadowIngest.running = false;
    shadowIngest.status = 'error';
    shadowIngest.error = 'la ingesta está tardando demasiado; puedes reintentarla';
    renderShadowIngest();
    return;
  }
  await sleep(SHADOW_INGEST_POLL_MS);
  return shadowIngestPoll(id, run, tries + 1);
}

function shadowIngestRetry() {
  const input = document.getElementById('shadow-url');
  if (shadowIngest.url && input && !input.value.trim()) input.value = shadowIngest.url;
  shadowIngestStart();
}

async function shadowIngestForget(pid, button) {
  if (!pid) return;
  if (button) {
    button.disabled = true;
    button.textContent = '…';
  }
  try {
    await request('POST', '/api/shadow/forget', { pid: pid });
    shadowIngest.candidates = shadowIngest.candidates.filter(
      (item) => (item.pid || item.id) !== pid
    );
    shadowIngest.added = Math.max(0, shadowIngest.added - 1);
    shadowIngest.status = 'listo';
    shadowIngest.error = '';
    shadowIngest.notice = 'frase quitada';
    renderShadowIngest();
    const previous = shadowUI.phrase && shadowUI.phrase.id;
    await loadShadowPhrases(previous === pid ? undefined : previous);
    setTimeout(() => {
      if (shadowIngest.notice === 'frase quitada') {
        shadowIngest.notice = '';
        renderShadowIngest();
      }
    }, 2500);
  } catch (err) {
    if (button) {
      button.disabled = false;
      button.textContent = 'quitar';
    }
    shadowIngest.notice = 'no se pudo quitar: ' + shadowIngestErrorLine(err);
    renderShadowIngest();
  }
}

function renderShadow() {
  const select = document.getElementById('shadow-phrase');
  const text = document.getElementById('shadow-text');
  const source = document.getElementById('shadow-source');
  const listen = document.getElementById('btn-shadow-listen');
  const record = document.getElementById('btn-shadow-record');
  const cancel = document.getElementById('btn-shadow-cancel');
  const status = document.getElementById('shadow-status');
  const meter = document.getElementById('shadow-meter');
  const bar = document.getElementById('shadow-meter-bar');
  const result = document.getElementById('shadow-result');
  const errorBox = document.getElementById('shadow-error');
  const errorText = document.getElementById('shadow-error-text');
  const retry = document.getElementById('btn-shadow-retry');
  if (!select || !text || !listen || !record) return;

  const phrase = shadowPhrase();
  shadowUI.phrase = phrase;
  const busy = shadowUI.phase === 'recording' || shadowUI.phase === 'analyzing';

  select.disabled = busy;
  text.textContent = phrase ? phrase.text : 'No hay frases en el banco.';
  if (source) {
    source.textContent = shadowSourceLine(phrase);
    source.hidden = !source.textContent;
  }
  listen.disabled = !phrase || !phrase.has_ref || busy;
  record.disabled = !phrase || shadowUI.phase === 'analyzing';
  if (cancel) cancel.hidden = shadowUI.phase !== 'recording';

  let label = 'Grabar';
  if (shadowUI.phase === 'recording') label = 'Parar';
  else if (shadowUI.phase === 'done') label = 'Grabar otra vez';
  record.classList.toggle('recording', shadowUI.phase === 'recording');
  record.replaceChildren(
    el('span', { class: 'rec-dot', 'aria-hidden': 'true' }),
    document.createTextNode(label)
  );

  meter.hidden = shadowUI.phase !== 'recording';
  if (bar && meter.hidden) bar.style.width = '0%';

  let line = '';
  if (shadowUI.phase === 'recording') line = 'grabando…';
  else if (shadowUI.phase === 'analyzing') line = 'analizando…';
  else if (!phrase) line = 'sin frases en el banco';
  else if (!phrase.has_ref) line = 'esta frase no tiene referencia de audio';
  else if (shadowUI.phase === 'idle') line = 'pulsa Grabar y repite encima de la referencia';
  status.textContent = line;

  if (result) {
    result.hidden = shadowUI.phase !== 'done' || !shadowUI.result;
    if (!result.hidden) {
      renderShadowFeedback(shadowUI.result, shadowUI.compare);
      renderShadowChart(shadowUI.result);
    }
  }

  if (errorBox) {
    errorBox.hidden = shadowUI.phase !== 'error';
    if (!errorBox.hidden && errorText) errorText.textContent = shadowUI.error;
    if (!errorBox.hidden && retry) {
      retry.textContent = shadowUI.takeId ? 'Reintentar' : 'Volver a grabar';
    }
  }
}

function shadowStopCapture() {
  if (shadowUI.meterRaf) {
    cancelAnimationFrame(shadowUI.meterRaf);
    shadowUI.meterRaf = null;
  }
  if (shadowUI.stream) {
    shadowUI.stream.getTracks().forEach((track) => track.stop());
    shadowUI.stream = null;
  }
  if (shadowUI.ctx) {
    try { shadowUI.ctx.close(); } catch (e) { /* noop */ }
    shadowUI.ctx = null;
  }
  shadowUI.analyser = null;
}

function shadowMeterLoop() {
  if (shadowUI.phase !== 'recording') return;
  const bar = document.getElementById('shadow-meter-bar');
  let level = 0;
  if (shadowUI.analyser && bar) {
    const data = new Uint8Array(shadowUI.analyser.fftSize);
    shadowUI.analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (let i = 0; i < data.length; i += 1) {
      const v = (data[i] - 128) / 128;
      sum += v * v;
    }
    level = Math.min(1, Math.sqrt(sum / data.length) * 4);
  }
  if (bar) bar.style.width = Math.round(level * 100) + '%';
  shadowUI.meterRaf = requestAnimationFrame(shadowMeterLoop);
}

async function shadowStart() {
  const phrase = shadowPhrase();
  if (!phrase || shadowUI.phase === 'analyzing') return;

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === 'undefined') {
    shadowUI.phase = 'error';
    shadowUI.error = 'Este navegador no permite grabar audio desde la página.';
    shadowUI.takeId = null;
    renderShadow();
    return;
  }

  shadowUI.result = null;
  shadowUI.compare = null;
  shadowUI.dev = '';
  shadowUI.error = '';
  shadowUI.takeId = null;
  shadowUI.runId += 1;
  const runId = shadowUI.runId;
  shadowUI.phase = 'recording';
  renderShadow();

  try {
    shadowUI.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    shadowUI.phase = 'error';
    shadowUI.error = 'No se pudo acceder al micrófono. Revisa el permiso del navegador.';
    renderShadow();
    return;
  }

  /* Si se canceló mientras el navegador pedía permiso, la toma ya no vale: se sueltan las
     pistas para no dejar el micro abierto y no se crea el grabador. */
  if (runId !== shadowUI.runId) {
    shadowUI.stream.getTracks().forEach((track) => track.stop());
    shadowUI.stream = null;
    return;
  }

  /* `track.label` solo trae el nombre real después de conceder el permiso: se lee aquí, no
     antes de getUserMedia. Se guarda con la toma para decidir si la anterior es comparable. */
  const audioTrack = shadowUI.stream.getAudioTracks
    ? shadowUI.stream.getAudioTracks()[0]
    : null;
  shadowUI.dev = (audioTrack && audioTrack.label) || '';

  const mime = pickMime();
  try {
    shadowUI.recorder = mime
      ? new MediaRecorder(shadowUI.stream, { mimeType: mime })
      : new MediaRecorder(shadowUI.stream);
  } catch (err) {
    shadowStopCapture();
    shadowUI.phase = 'error';
    shadowUI.error = 'No se pudo iniciar el grabador.';
    renderShadow();
    return;
  }

  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (AudioCtx) {
      shadowUI.ctx = new AudioCtx();
      const source = shadowUI.ctx.createMediaStreamSource(shadowUI.stream);
      shadowUI.analyser = shadowUI.ctx.createAnalyser();
      shadowUI.analyser.fftSize = 1024;
      source.connect(shadowUI.analyser);
    }
  } catch (err) {
    /* el medidor es un extra: sin él se graba igual */
    shadowUI.analyser = null;
  }

  shadowUI.chunks = [];
  shadowUI.recorder.addEventListener('dataavailable', (event) => {
    if (runId !== shadowUI.runId) return;
    if (event.data && event.data.size) shadowUI.chunks.push(event.data);
  });
  shadowUI.recorder.addEventListener('stop', () => shadowOnStop(runId));
  shadowUI.recorder.start();
  shadowUI.started = Date.now();
  renderShadow();
  shadowUI.meterRaf = requestAnimationFrame(shadowMeterLoop);
}

function shadowStop() {
  if (!shadowUI.recorder) return;
  if (shadowUI.recorder.state !== 'inactive') shadowUI.recorder.stop();
}

/* Aborta la toma en curso sin enviarla. Subir la runId deja sordos a los eventos que el
   MediaRecorder aún tenga en cola (el `stop` final), así que no se crea blob ni petición. */
function shadowCancel() {
  if (shadowUI.phase !== 'recording') return;
  shadowUI.runId += 1;
  const recorder = shadowUI.recorder;
  shadowUI.recorder = null;
  shadowUI.chunks = [];
  shadowUI.started = 0;
  shadowUI.takeId = null;
  shadowUI.result = null;
  shadowUI.compare = null;
  shadowUI.dev = '';
  shadowUI.error = '';
  shadowUI.phase = 'idle';
  if (recorder && recorder.state !== 'inactive') {
    try { recorder.stop(); } catch (e) { /* noop */ }
  }
  shadowStopCapture();
  renderShadow();
}

async function shadowOnStop(runId) {
  if (runId !== shadowUI.runId) return;
  const type = (shadowUI.recorder && shadowUI.recorder.mimeType) || 'audio/webm';
  const blob = new Blob(shadowUI.chunks, { type: type });
  const phrase = shadowPhrase();
  const ext = type.indexOf('wav') !== -1 ? 'wav' : type.indexOf('mp4') !== -1 ? 'm4a' : 'webm';

  shadowStopCapture();
  shadowUI.chunks = [];

  if (!phrase) {
    shadowUI.phase = 'idle';
    renderShadow();
    return;
  }
  if (!blob.size) {
    shadowUI.phase = 'error';
    shadowUI.error = 'La grabación ha salido vacía. Prueba otra vez.';
    shadowUI.takeId = null;
    renderShadow();
    return;
  }

  shadowUI.phase = 'analyzing';
  shadowUI.error = '';
  renderShadow();

  const fd = new FormData();
  fd.append('pid', phrase.id);
  fd.append('file', blob, 'toma.' + ext);
  if (shadowUI.dev) fd.append('dev', shadowUI.dev);

  try {
    const out = await request('POST', '/api/shadow', fd);
    shadowUI.takeId = out.id;
    renderShadow();
    await shadowPoll(out.id, 0);
  } catch (err) {
    if (err && err.data && err.data.id) shadowUI.takeId = err.data.id;
    shadowUI.phase = 'error';
    shadowUI.error = shadowErrorLine(err);
    renderShadow();
  }
}

async function shadowPoll(takeId, tries) {
  if (shadowUI.phase !== 'analyzing' || shadowUI.takeId !== takeId) return;
  try {
    const item = await request('GET', '/api/shadow/' + encodeURIComponent(takeId));
    if (item.status === 'listo') {
      shadowUI.phase = 'done';
      shadowUI.result = item.result || null;
      shadowUI.compare = item.compare || null;
      shadowUI.error = '';
      renderShadow();
      return;
    }
    if (item.status === 'error') {
      shadowUI.phase = 'error';
      shadowUI.error = shadowErrorLine(item.error || {});
      renderShadow();
      return;
    }
  } catch (err) {
    shadowUI.phase = 'error';
    shadowUI.error = shadowErrorLine(err);
    renderShadow();
    return;
  }

  if (tries >= SHADOW_MAX_POLLS) {
    shadowUI.phase = 'error';
    shadowUI.error = 'El análisis está tardando demasiado. Puedes reintentarlo.';
    renderShadow();
    return;
  }
  await sleep(SHADOW_POLL_MS);
  return shadowPoll(takeId, tries + 1);
}

async function shadowRetry() {
  const takeId = shadowUI.takeId;
  if (!takeId) {
    shadowUI.phase = 'idle';
    shadowUI.error = '';
    shadowUI.result = null;
    shadowUI.compare = null;
    renderShadow();
    return;
  }
  shadowUI.phase = 'analyzing';
  shadowUI.error = '';
  renderShadow();
  try {
    await request('POST', '/api/shadow/' + encodeURIComponent(takeId) + '/retry');
    await shadowPoll(takeId, 0);
  } catch (err) {
    shadowUI.phase = 'error';
    shadowUI.error = shadowErrorLine(err);
    renderShadow();
  }
}

async function shadowListen() {
  const phrase = shadowPhrase();
  if (!phrase || !phrase.has_ref) return;
  try {
    if (shadowUI.audio) shadowUI.audio.pause();
    shadowUI.audio = new Audio(phrase.ref);
    await shadowUI.audio.play();
  } catch (err) {
    const status = document.getElementById('shadow-status');
    if (status) status.textContent = 'No se pudo reproducir la referencia.';
  }
}

async function loadShadowPhrases(prefer) {
  const select = document.getElementById('shadow-phrase');
  const previous = prefer || (shadowUI.phrase && shadowUI.phrase.id);
  try {
    const data = await request('GET', '/api/shadow/phrases');
    shadowUI.phrases = Array.isArray(data.phrases) ? data.phrases : [];
  } catch (err) {
    shadowUI.phrases = [];
  }

  if (select) {
    select.textContent = '';
    shadowUI.phrases.forEach((phrase) => {
      select.append(el('option', { value: phrase.id }, phrase.text));
    });
  }
  /* Tras una ingesta se elige la primera frase nueva; al quitar una, la siguiente. */
  const next = shadowUI.phrases.find((p) => p.id === previous) ||
    shadowUI.phrases[0] || null;
  if (next && shadowUI.phrase && next.id !== shadowUI.phrase.id) {
    shadowUI.phase = 'idle';
    shadowUI.takeId = null;
    shadowUI.result = null;
    shadowUI.compare = null;
    shadowUI.dev = '';
    shadowUI.error = '';
  }
  shadowUI.phrase = next;
  if (shadowUI.phrase && select) select.value = shadowUI.phrase.id;
  renderShadow();
  renderShadowIngest();
}

function initShadow() {
  const select = document.getElementById('shadow-phrase');
  if (!select) return;
  select.addEventListener('change', () => {
    shadowUI.phase = 'idle';
    shadowUI.takeId = null;
    shadowUI.result = null;
    shadowUI.compare = null;
    shadowUI.dev = '';
    shadowUI.error = '';
    renderShadow();
  });
  document.getElementById('btn-shadow-listen').addEventListener('click', shadowListen);
  document.getElementById('btn-shadow-record').addEventListener('click', () => {
    if (shadowUI.phase === 'recording') shadowStop();
    else shadowStart();
  });
  document.getElementById('btn-shadow-cancel').addEventListener('click', shadowCancel);

  const url = document.getElementById('shadow-url');
  const ingest = document.getElementById('btn-shadow-ingest');
  const ingestRetry = document.getElementById('btn-shadow-ingest-retry');
  if (ingest) ingest.addEventListener('click', shadowIngestStart);
  if (ingestRetry) ingestRetry.addEventListener('click', shadowIngestRetry);
  if (url) {
    url.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      shadowIngestStart();
    });
  }
  renderShadowIngest();

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || shadowUI.phase !== 'recording') return;
    const panel = document.getElementById('panel-shadow');
    if (!panel || panel.hidden) return;
    const active = document.activeElement;
    if (active && active !== document.body && !panel.contains(active)) return;
    e.preventDefault();
    shadowCancel();
  });
  document.getElementById('btn-shadow-retry').addEventListener('click', shadowRetry);
}

/* ------------------------------- arranque --------------------------------- */

function bindSession() {
  document.getElementById('btn-new').addEventListener('click', newSession);
  const copyPrompt = document.getElementById('btn-copy-prompt');
  if (copyPrompt) {
    copyPrompt.addEventListener('click', async () => {
      const pre = document.getElementById('new-manual-pre');
      const text = (pre && pre.textContent) || '';
      if (!text) return;
      const ok = await copyText(text);
      copyPrompt.textContent = ok ? 'copiado' : 'selecciona y Ctrl+C';
      setTimeout(() => { copyPrompt.textContent = 'copiar prompt'; }, 2500);
      if (!ok && window.getSelection && pre) {
        const range = document.createRange();
        range.selectNodeContents(pre);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      }
    });
  }
  document.getElementById('btn-process').addEventListener('click', processReport);

  document.getElementById('report').addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      processReport();
    }
  });

  if (MOCK) {
    document.getElementById('samples').hidden = false;

    document.getElementById('btn-example').addEventListener('click', async () => {
      const data = await mock.file('samples');
      const ta = document.getElementById('report');
      ta.value = data.report;
      ta.focus();
    });

    document.getElementById('btn-example-broken').addEventListener('click', async () => {
      const data = await mock.file('samples');
      const ta = document.getElementById('report');
      ta.value = data.broken;
      ta.focus();
    });
  }
}

async function boot() {
  document.getElementById('mode-badge').hidden = !MOCK;
  if (MOCK) document.getElementById('samples').hidden = false;
  /* ?mock=1&autoscenario=1 genera el escenario al cargar: sirve para capturar la pestaña
     Sesión con contenido sin tener que pulsar nada (Brave headless no hace clics). */
  if (MOCK && PARAMS.get('autoscenario') === '1') {
    setTimeout(async () => {
      try {
        await newSession();
        const m = document.getElementById('new-manual');
        if (m) { m.open = false; m.hidden = true; }   /* estado normal: el prompt vive detrás del botón */
        /* el navegador headless no concede portapapeles: para la captura se deja el mensaje bueno */
        const st = document.getElementById('new-status');
        if (st) { st.textContent = 'escenario listo · prompt copiado, pégalo en ChatGPT'; st.style.color = ''; }
      } catch (e) { /* noop */ }
    }, 60);
  }

  initTabs();
  initDifficulty();
  initFocus();
  bindSession();
  initDrillControls();
  initSpeech();
  initShadow();

  const fromHash = (location.hash || '').replace('#', '');
  setTab(TAB_NAMES.indexOf(fromHash) === -1 ? 'sesion' : fromHash, { silent: true });
  if (fromHash === 'drills') focusDrillInput();

  try {
    await loadState();
    renderRegistro();
  } catch (err) {
    showGlobalError(err);
  }

  try {
    await loadDrills();
  } catch (err) {
    drills.supported = false;
    renderDrillCard();
    renderDrillFoot();
  }

  try {
    await loadShadowPhrases();
  } catch (err) {
    renderShadow();
  }
}

document.addEventListener('DOMContentLoaded', boot);

/* -------------------------- Hablar · módulo aislado ------------------------ */

function initTalk() {
  const panel = document.getElementById('panel-hablar');
  const startButton = document.getElementById('talk-start');
  const pushButton = document.getElementById('talk-push');
  const endButton = document.getElementById('talk-end');
  const legend = document.getElementById('talk-states');
  const notice = document.getElementById('talk-notice');
  const message = document.getElementById('talk-message');
  const repeat = document.getElementById('talk-repeat');
  const audio = document.getElementById('talk-audio');
  const playback = document.getElementById('talk-playback');
  const connection = document.getElementById('talk-connection');
  const recent = document.getElementById('talk-turns');
  const earlier = document.getElementById('talk-earlier');
  const historyLink = document.getElementById('talk-history');
  const report = document.getElementById('talk-report');
  const turns = new Map();
  const queue = [];
  let session = null;
  let source = null;
  let reconnectTimer = null;
  let seq = 0;
  let connectionFailures = 0;
  let starting = false;
  let ending = false;
  let requestingMic = false;
  let uploading = false;
  let working = false;
  let recorder = null;
  let stream = null;
  let playing = false;
  let audioPending = false;
  let audioBlocked = false;

  function showNotice(text, canRepeat = false) {
    message.textContent = text;
    repeat.hidden = !canRepeat;
    notice.hidden = !text;
  }

  function updateControls() {
    const recording = recorder !== null;
    const busy = starting || ending || requestingMic || recording || uploading || working || queue.length > 0;
    const state = recording ? 'listening' : playing ? 'speaking' : busy ? 'thinking' : 'ready';
    legend.querySelectorAll('[data-state]').forEach((item) => {
      if (item.dataset.state === state) item.setAttribute('aria-current', 'true');
      else item.removeAttribute('aria-current');
    });
    pushButton.disabled = !session || (busy && !recording);
    pushButton.setAttribute('aria-pressed', recording ? 'true' : 'false');
    pushButton.textContent = recording ? 'Terminar y enviar' : 'Empezar a hablar';
    startButton.disabled = starting || ending || !!session;
    startButton.hidden = !!session;
    endButton.disabled = !session || busy;
    playback.hidden = !audioBlocked;
  }

  async function talkRequest(path, body) {
    const multipart = body instanceof FormData;
    const response = await fetch(path, {
      method: 'POST',
      headers: multipart ? {} : { 'Content-Type': 'application/json' },
      body: multipart ? body : JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(data.detail || 'No se pudo completar la petición.');
      error.code = data.error;
      throw error;
    }
    return data;
  }

  function describeError(error) {
    const labels = {
      talk_busy: 'Hay otra sesión o un turno activo.',
      talk_unavailable: 'El motor de conversación no está disponible.',
      NotAllowedError: 'Permite el acceso al micrófono en el navegador y vuelve a pulsar.',
      NotFoundError: 'No se ha encontrado un micrófono.',
      NotReadableError: 'No se puede abrir el micrófono. Comprueba si otra aplicación lo está usando.'
    };
    return labels[error.code || error.name] || error.message || 'No se pudo completar la operación.';
  }

  function renderScenario(scenario, settings) {
    document.getElementById('talk-meta').textContent = settings.difficulty + ' · ' +
      (settings.focus.map(patternLabel).join(', ') || 'sin foco') + ' · ' + settings.minutes + ' min';
    document.getElementById('talk-title').textContent = scenario ? scenario.scenario : 'Entrevista libre';
    document.getElementById('talk-situation').textContent = scenario ?
      (scenario.brief?.situation || scenario.you_play || '') :
      'No se pudo generar un escenario. El entrevistador usará el contexto general.';
    const details = document.getElementById('talk-scenario');
    const brief = document.getElementById('talk-brief');
    brief.replaceChildren();
    details.open = false;
    details.hidden = !scenario;
    if (!scenario) return;
    const fields = [
      ['Lo que defiendes', scenario.brief?.my_position],
      ['Lo que está en juego', scenario.brief?.stake],
      ['Munición', scenario.ammo],
      ['Te van a objetar', scenario.objections],
      /* `you_play` es el papel del modelo y `i_play` el del alumno: ver prompts/scenario.md */
      ['Tu papel', scenario.i_play],
      ['Entrevistador', scenario.you_play],
      ['Para empezar', scenario.opener]
    ];
    fields.forEach(([label, value]) => {
      const text = Array.isArray(value) ? value.join('\n') : value;
      if (text) brief.append(el('dt', null, label), el('dd', null, text));
    });
  }

  function renderTurns() {
    const rows = [];
    for (const turn of turns.values()) {
      if (turn.user) rows.push(['Tú', turn.user]);
      if (turn.assistant) rows.push(['Entrevistador', turn.assistant]);
    }
    const nodes = rows.map(([who, text]) => el('li', null,
      el('span', { class: 'talk-who' + (who === 'Tú' ? ' talk-who--user' : '') }, who),
      el('p', { lang: 'en' }, text)));
    earlier.replaceChildren(...nodes.slice(0, -3));
    recent.replaceChildren(...nodes.slice(-3));
    document.getElementById('talk-more').hidden = rows.length <= 3;
    document.getElementById('talk-empty').hidden = rows.length > 0;
    const count = Array.from(turns.values()).filter((turn) => turn.user).length;
    document.getElementById('talk-count').textContent = plural(count, 'turno', 'turnos');
  }

  function playNext() {
    if (audioPending || audioBlocked || !queue.length) { updateControls(); return; }
    audioPending = true;
    audio.src = queue[0];
    audio.play().catch(blockAudio);
    updateControls();
  }

  function blockAudio() {
    playing = false;
    audioPending = false;
    audioBlocked = true;
    updateControls();
  }

  function handleEvent(event) {
    if (event.v !== 1 || event.session !== session || !Number.isInteger(event.seq) || event.seq <= seq) return;
    seq = event.seq;
    const data = event.data || {};
    if (event.type === 'stt.final' || event.type === 'llm.delta') {
      if (!turns.has(event.turn)) turns.set(event.turn, { user: '', assistant: '' });
      const turn = turns.get(event.turn);
      if (event.type === 'stt.final') turn.user = data.text || '';
      else turn.assistant += data.text || '';
      renderTurns();
    } else if (event.type === 'tts.sentence') {
      if (data.url) { queue.push(data.url); playNext(); }
      else showNotice('Esta frase no tiene audio disponible. Puedes leer la respuesta en el panel.');
    } else if (event.type === 'turn.state') {
      /* «speaking» en el motor significa síntesis; aquí manda el audio real. */
      if (data.state !== 'idle') working = true;
    } else if (event.type === 'turn.done') {
      working = false;
    } else if (event.type === 'error') {
      if (data.code === 'stt_empty_audio' && data.retry) showNotice('No te oí nada en el último turno.', true);
      else showNotice((data.code || 'error') + ': ' + (data.detail || 'No se pudo completar el turno.'));
    }
    updateControls();
  }

  /* El servidor entrega una instantánea SSE de los eventos nuevos y cierra la
   * conexión: no es un flujo continuo. Un EventSource aquí reconectaría en bucle
   * y machacaría el servidor, así que se pide con fetch y se deja respirar. */
  let pollTimer = null;

  function schedulePoll(delay) {
    clearTimeout(pollTimer);
    if (!session || ending) return;
    pollTimer = setTimeout(pollEvents, delay);
  }

  async function pollEvents() {
    if (!session || ending) return;
    try {
      const response = await fetch('/api/talk/events?since=' + seq, {
        headers: { Accept: 'text/event-stream' },
        cache: 'no-store',
      });
      if (!response.ok) throw new Error('events');
      const text = await response.text();
      connectionFailures = 0;
      connection.hidden = true;
      text.split('\n\n').forEach((chunk) => {
        const line = chunk.split('\n').find((item) => item.startsWith('data: '));
        if (!line) return;
        try {
          handleEvent(JSON.parse(line.slice(6)));
        } catch (error) {
          showNotice('No se pudo leer un evento de la conversación.');
        }
      });
      /* con un turno en marcha se sondea seguido; en reposo, de segundo en segundo */
      schedulePoll(working || uploading || queue.length ? 300 : 1200);
    } catch (error) {
      connectionFailures += 1;
      connection.hidden = connectionFailures < 3;
      if (connectionFailures === 3) connection.textContent = 'Sin conexión con los eventos. Reconectando…';
      schedulePoll(connectionFailures < 3 ? 800 : 2500);
    }
  }

  function connectEvents() {
    connectionFailures = 0;
    source = { close() { clearTimeout(pollTimer); } };
    schedulePoll(200);
  }

  function closeEvents() {
    if (source) source.close();
    source = null;
    connection.hidden = true;
  }

  async function startSession() {
    if (starting || session) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined' || typeof fetch === 'undefined') {
      showNotice('Este navegador no permite grabar o recibir la conversación. Abre Refire en localhost con un navegador compatible.');
      return;
    }
    starting = true;
    showNotice('Preparando el escenario…');
    updateControls();
    const settings = { difficulty: sessionUI.difficulty, focus: Array.from(sessionUI.focus), minutes: sessionUI.minutes };
    try {
      const data = await talkRequest('/api/talk/start', settings);
      session = data.session;
      seq = 0;
      turns.clear();
      renderTurns();
      earlier.hidden = true;
      historyLink.setAttribute('aria-expanded', 'false');
      historyLink.textContent = 'ver los turnos anteriores';
      report.hidden = true;
      renderScenario(data.scenario, settings);
      showNotice('');
      connectEvents();
    } catch (error) {
      showNotice(describeError(error));
    } finally {
      starting = false;
      updateControls();
      if (session) pushButton.focus();
    }
  }

  function stopTracks() {
    if (stream) stream.getTracks().forEach((track) => track.stop());
    stream = null;
  }

  async function uploadTurn(blob) {
    uploading = true;
    working = true;
    updateControls();
    const form = new FormData();
    const extension = blob.type.includes('mp4') ? 'mp4' : blob.type.includes('ogg') ? 'ogg' : 'webm';
    form.append('file', blob, 'turn.' + extension);
    try {
      await talkRequest('/api/talk/turn', form);
    } catch (error) {
      working = false;
      showNotice(describeError(error));
    } finally {
      uploading = false;
      updateControls();
    }
  }

  async function startTalking() {
    if (pushButton.disabled || requestingMic || recorder) return;
    requestingMic = true;
    showNotice('Abriendo el micrófono… en cuanto aparezca «escuchando», habla y pulsa otra vez para enviar.');
    updateControls();
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      /* Soltar mientras se concede permiso nunca deja el micro abierto. */
      if (panel.hidden || document.hidden) { stopTracks(); return; }
      const mimeType = 'audio/webm;codecs=opus';
      recorder = MediaRecorder.isTypeSupported(mimeType) ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      const current = recorder;
      const chunks = [];
      let failed = false;
      current.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      current.onerror = () => {
        failed = true;
        showNotice('La grabación se interrumpió. Vuelve a pulsar para hablar.', true);
        if (current.state !== 'inactive') current.stop();
        stopTracks();
        recorder = null;
        updateControls();
      };
      current.onstop = () => {
        stopTracks();
        recorder = null;
        if (!failed) {
          const blob = new Blob(chunks, { type: current.mimeType || chunks[0]?.type || '' });
          if (blob.size) uploadTurn(blob);
          else showNotice('No te oí nada en el último turno.', true);
        }
        updateControls();
      };
      current.start();
      showNotice('');
    } catch (error) {
      recorder = null;
      stopTracks();
      showNotice(describeError(error));
    } finally {
      requestingMic = false;
      if (!recorder && !notice.hidden && message.textContent.startsWith('Abriendo')) {
        showNotice('Pulsa otra vez para hablar.', true);
      }
      updateControls();
    }
  }

  /* Un clic abre el turno y otro lo cierra: nada de mantener el dedo pulsado. */
  function stopTalking() {
    if (recorder && recorder.state !== 'inactive') recorder.stop();
    updateControls();
  }

  async function toggleTalking() {
    if (!session || ending) return;
    if (recorder) stopTalking();
    else await startTalking();
  }

  function renderReport(data) {
    const result = data.report;
    report.replaceChildren(el('h2', null, 'Informe de la conversación'));
    report.append(el('p', null, 'Sesión terminada · ' + data.session));
    if (result) {
      report.append(el('p', null, 'Fila del registro: ' + (result.row ? 'guardada' : 'no disponible') +
        ' · ' + plural(result.drills.length, 'drill', 'drills')));
      if (result.row) {
        const details = el('details', null, el('summary', null, 'ver la fila guardada'));
        const row = el('dl');
        [['Fecha', 'date'], ['Minutos', 'minutes'], ['Escenario', 'topics'],
          ['Corrección principal', 'primary_correction'], ['Vocabulario nuevo', 'new_vocab'],
          ['Próximo objetivo', 'next_goal']].forEach(([label, key]) => {
          if (result.row[key] != null) row.append(el('dt', null, label), el('dd', null, String(result.row[key])));
        });
        details.append(row);
        report.append(details);
      }
      if (result.warnings.length) report.append(el('ul', null,
        result.warnings.map((warning) => el('li', null, WARNING_TEXT[warning] || warning))));
    }
    report.hidden = false;
    report.focus();
  }

  async function endSession() {
    if (endButton.disabled) return;
    ending = true;
    closeEvents();
    showNotice('Preparando el informe…');
    updateControls();
    try {
      const data = await talkRequest('/api/talk/end', { report: true });
      session = null;
      renderReport(data);
      showNotice('');
    } catch (error) {
      /* report_failed llega después de cerrar; repetir end no recupera esa sesión. */
      if (error.code === 'report_failed') {
        session = null;
        showNotice('La sesión se cerró, pero no se pudo generar el informe: ' + error.message);
      } else showNotice(describeError(error));
    } finally {
      ending = false;
      if (session) connectEvents();
      updateControls();
    }
  }

  startButton.addEventListener('click', startSession);
  endButton.addEventListener('click', endSession);
  pushButton.addEventListener('click', (event) => {
    event.preventDefault();
    toggleTalking();
  });
  panel.addEventListener('keydown', (event) => {
    if (event.code !== 'Space' || event.altKey || event.ctrlKey || event.metaKey || panel.hidden) return;
    if (event.target !== pushButton && event.target.closest('button, a, input, textarea, select, summary, [contenteditable]')) return;
    event.preventDefault();
    if (!event.repeat) toggleTalking();
  });
  /* Si la pestaña se oculta o se cierra, el turno se cierra en vez de seguir grabando. */
  document.addEventListener('visibilitychange', () => { if (document.hidden && recorder) stopTalking(); });
  new MutationObserver(() => { if (panel.hidden && recorder) stopTalking(); }).observe(panel, { attributes: true, attributeFilter: ['hidden'] });
  window.addEventListener('pagehide', () => { if (recorder) stopTalking(); stopTracks(); closeEvents(); });
  repeat.addEventListener('click', (event) => { event.preventDefault(); pushButton.focus(); });
  historyLink.addEventListener('click', (event) => {
    event.preventDefault();
    earlier.hidden = !earlier.hidden;
    historyLink.setAttribute('aria-expanded', String(!earlier.hidden));
    historyLink.textContent = earlier.hidden ? 'ver los turnos anteriores' : 'ocultar los turnos anteriores';
  });
  audio.addEventListener('playing', () => { playing = true; updateControls(); });
  audio.addEventListener('ended', () => {
    queue.shift();
    playing = false;
    audioPending = false;
    playNext();
  });
  audio.addEventListener('error', blockAudio);
  document.getElementById('talk-play').addEventListener('click', () => {
    audioBlocked = false;
    playNext();
  });
  updateControls();
}

document.addEventListener('DOMContentLoaded', initTalk);
