"""Puente entre la app y el motor de voz de ``voicekit`` (H4, H10, H12).

El motor vive en su paquete y aquí solo se conecta: una sesión en disco, un ``Engine`` con
los tres proveedores reales y un sink que guarda los eventos. **No** se genera el informe:
eso es M5c. ``voicekit`` se importa de forma perezosa para que la app siga arrancando con
solo la biblioteca estándar y responda 503 ``talk_unavailable`` si el motor no está.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import threading
from datetime import date, datetime
from pathlib import Path

from . import agent, patterns, prompts, text as textutils, yamlmini

__all__ = (
    "TalkBusy",
    "TalkReportFailed",
    "TalkSession",
    "TalkUnavailable",
    "available",
    "build_session_block",
    "build_system_prompt",
    "current",
)

# Una sola sesión activa en todo el proceso (app de una persona, H12).
_LOCK = threading.RLock()
_ACTIVE: "TalkSession | None" = None


class TalkUnavailable(RuntimeError):
    """``voicekit`` no se puede importar en este intérprete."""


class TalkBusy(RuntimeError):
    """Ya hay una sesión o un turno activo."""


class TalkReportFailed(RuntimeError):
    """El informe de la sesión no se pudo construir o guardar (-> 502)."""


# Reglas duras del papel. Son literales: el motor las recibe tal cual y los tests
# comprueban que no se reescriben.
HARD_RULES = (
    "Reply in at most two short sentences, then ask one question.",
    "Never use markdown, bullet points or lists: your answer is read aloud.",
    "Do not invent facts about the candidate. If a number is missing, ask for it.",
    "Stay in character for the whole session.",
)


def _neutral_system_prompt() -> str:
    """Prompt mínimo: papel genérico y reglas, sin datos del escenario inventados."""
    return "\n".join(("You are a technical interviewer.",) + HARD_RULES)


def _text(value) -> str:
    return str(value or "").strip()


def _difficulty_value(difficulty, scenario=None) -> str:
    """Nivel de la sesión: el pedido al arrancar o el que traiga el escenario."""
    value = _text(difficulty)
    if not value and isinstance(scenario, dict):
        value = _text(scenario.get("difficulty")) or _text(scenario.get("cefr"))
    return (value or "B2").upper()


def _items(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item).strip()]


def build_system_prompt(scenario: dict) -> str:
    """Convierte el escenario de ``session.scenario`` en el prompt de sistema.

    La munición se copia literal (incluidos los ``[his figure]``): el modelo no
    debe añadir cifras que el escenario no trae. Sin escenario utilizable se
    devuelve el prompt neutro, que no inventa ni describe ningún caso.
    """
    if not isinstance(scenario, dict):
        return _neutral_system_prompt()

    you_play = _text(scenario.get("you_play"))
    i_play = _text(scenario.get("i_play"))
    situation_name = _text(scenario.get("scenario"))
    brief = scenario.get("brief") if isinstance(scenario.get("brief"), dict) else {}
    situation = _text(brief.get("situation"))
    my_position = _text(brief.get("my_position"))
    stake = _text(brief.get("stake"))
    ammo = _items(scenario.get("ammo"))
    objections = _items(scenario.get("objections"))
    useful = _items(scenario.get("useful"))
    opener = _text(scenario.get("opener"))

    if not any((you_play, i_play, situation_name, situation, my_position, stake,
                ammo, objections, useful, opener)):
        return _neutral_system_prompt()

    lines: list[str] = []
    # `scenario.md` reparte los papeles al revés de lo que parece: `you_play` es
    # el papel de ChatGPT y `i_play` el del alumno. Al modelo se le dice «you»
    # solo para su propio papel; el del alumno va descrito en tercera persona.
    if you_play:
        lines.append("You play: %s" % you_play)
    if i_play:
        lines.append("He plays: %s" % i_play)
    if situation_name:
        lines.append("Scenario: %s" % situation_name)
    if situation or my_position or stake:
        # El brief del escenario está escrito en segunda persona («You want...»):
        # aquí «you» es el alumno, no el modelo.
        lines.append("His brief (when it says 'you', it means him, not you):")
    if situation:
        lines.append("Situation: %s" % situation)
    if my_position:
        lines.append("His position: %s" % my_position)
    if stake:
        lines.append("What is at stake: %s" % stake)
    for item in ammo:
        lines.append("Ammo he can use, exactly as written: %s" % item)
    for item in objections:
        lines.append("Objection you may raise: %s" % item)
    for item in useful:
        lines.append("Useful phrase he may need: %s" % item)
    if opener:
        lines.append('Open the scene with this line, in character: "%s"' % opener)

    if lines:
        lines.append("")
    lines.extend(HARD_RULES)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# wrap-up: transcripción -> bloque YAML `session:`
# --------------------------------------------------------------------------- #

# Claves del escenario que sirven para el wrap-up. El resto (block_b, chatgpt_url)
# es ruido de la interfaz y no se manda al modelo.
_WRAPUP_SCENARIO_KEYS = (
    "scenario",
    "you_play",
    "i_play",
    "brief",
    "ammo",
    "objections",
    "useful",
    "opener",
)

# Solo los turnos del alumno pueden citarse en una corrección.
_LEARNER_ROLES = ("user", "learner")


def _turn_rows(turns) -> list[dict]:
    """Normaliza los turnos a ``{number, role, text}``, vengan como dict o dataclass."""
    rows = []
    for index, turn in enumerate(turns or [], 1):
        if isinstance(turn, str):
            role, body, number, heard = "user", turn, index, True
        elif isinstance(turn, dict):
            role = turn.get("role")
            body = turn.get("text")
            number = turn.get("number", index)
            heard = turn.get("heard", True)
        else:
            role = getattr(turn, "role", None)
            body = getattr(turn, "text", None)
            number = getattr(turn, "number", index)
            heard = getattr(turn, "heard", True)
        body = _text(body)
        if not body:
            continue
        row = {
            "number": number if number is not None else index,
            "role": _text(role) or "user",
            "text": body,
        }
        if heard is False:
            row["heard"] = False
        rows.append(row)
    return rows


def _scenario_input(scenario):
    """Vista compacta del escenario para el JSON del prompt (sin la piel de la app)."""
    if scenario is None:
        return None
    if isinstance(scenario, dict):
        selected = {
            key: scenario[key]
            for key in _WRAPUP_SCENARIO_KEYS
            if scenario.get(key)
        }
        if selected:
            return selected
        return {
            key: value
            for key, value in scenario.items()
            if key not in ("block_b", "chatgpt_url", "ok", "family", "warnings")
        }
    return str(scenario)


def _scenario_name(scenario) -> str:
    """El valor de ``session.scenario``: el nombre corto, no el escenario entero."""
    if isinstance(scenario, dict):
        for key in ("scenario", "topics", "name", "situation"):
            value = _text(scenario.get(key))
            if value:
                return value
        return ""
    return _text(scenario)


def _minutes_value(minutes):
    """Minutos enteros y no negativos; ``None`` si no hay un número usable."""
    if minutes is None or minutes == "":
        return None
    try:
        number = float(minutes)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return max(0, int(round(number)))


def _call_report_llm(llm, prompt: str) -> str:
    """Llama al proveedor del informe. Sin ``llm`` se usa el agente de siempre.

    El LLM del motor (``OpenAiCompatLlm``) solo habla ``stream``: se consume el
    stream entero y se une. Así el informe va con el mismo proveedor que los
    turnos, que es lo que pide M5g2.
    """
    if llm is None:
        return agent.call_agent(prompt)
    if callable(llm):
        return llm(prompt)
    complete = getattr(llm, "complete", None)
    if callable(complete):
        return complete(prompt)
    stream = getattr(llm, "stream", None)
    if callable(stream):
        chunks = stream(
            [{"role": "user", "content": prompt}], threading.Event()
        )
        return "".join(str(chunk) for chunk in chunks)
    raise TypeError("llm debe ser invocable o tener complete/stream")


def _saved_report(raw: str):
    """Documento guardado para este texto exacto (lo escribe ``process_report``)."""
    from . import session as report_session

    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    for doc in report_session._load_reports():
        if doc.get("raw_sha256") == sha:
            return doc
    return None


def _extract_session_yaml(raw) -> str:
    """Recorta la respuesta del modelo hasta el bloque que empieza en ``session:``."""
    body = _text(raw).lstrip("\ufeff")
    if not body:
        raise ValueError("respuesta vacía del modelo")

    # ```yaml ... ``` (también ``` a secas), con o sin prosa alrededor.
    for block in re.findall(r"```[ \t]*[A-Za-z]*[ \t]*\r?\n(.*?)```", body, re.S):
        match = re.search(r"(?m)^[ \t]*session[ \t]*:", block)
        if match:
            return block[match.start():].strip()

    match = re.search(r"(?m)^[ \t]*session[ \t]*:", body)
    if match:
        candidate = body[match.start():].strip()
        # Una valla de cierre sin abrir (raro, pero el modelo hace de todo).
        return candidate.split("```", 1)[0].rstrip()

    # Sin cabecera: si parece el mapa de dentro de session, se envuelve.
    if re.search(
        r"(?m)^[ \t]*(scores|corrections|new_vocab|next_goal|date|minutes)\s*:", body
    ):
        indented = "\n".join(
            ("  " + line) if line.strip() else line for line in body.splitlines()
        )
        return "session:\n" + indented

    raise ValueError("respuesta del modelo sin bloque 'session:'")


# M5g1b: `yamlmini` no entiende mapas en línea (`{a: 1, b: 2}`), y los modelos los
# usan a menudo. Antes de parsear se convierten a bloque; la normalización vive
# aquí para no tocar el parser que comparten el vault y los informes.
_INLINE_MAP_LINE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<dash>- )?(?P<key>[^:]+?):[ \t]*(?P<value>\{.*\})[ \t]*$"
)


def _split_flow_pair(part: str):
    """Separa `clave: valor` de un mapa en línea, sin partir comas entre comillas."""
    quote = None
    escaped = False
    for index, char in enumerate(part):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\" and quote == '"':
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char == ":":
            key = part[:index].strip()
            value = part[index + 1:].strip()
            if not key or not value:
                return None, None
            return key, value
    return None, None


def _split_flow_pairs(inner: str) -> list[tuple[str, str]]:
    """Pares de un mapa en línea; una coma dentro de comillas no separa pares."""
    parts: list[str] = []
    buffer: list[str] = []
    quote = None
    escaped = False
    for char in inner:
        if quote:
            buffer.append(char)
            if escaped:
                escaped = False
            elif char == "\\" and quote == '"':
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            buffer.append(char)
        elif char == ",":
            parts.append("".join(buffer))
            buffer = []
        else:
            buffer.append(char)
    if quote:
        raise ValueError("comilla sin cerrar en un mapa en línea")
    parts.append("".join(buffer))

    pairs: list[tuple[str, str]] = []
    for part in parts:
        if not part.strip():
            continue
        key, value = _split_flow_pair(part)
        if key is None:
            raise ValueError("mapa en línea ilegible: %r" % part.strip())
        pairs.append((key, value))
    return pairs


def _normalize_inline_maps(text: str) -> str:
    """Convierte `clave: {a: 1, b: 2}` en `clave:` y sus pares indentados.

    Vale para cualquier clave y también dentro de un item de secuencia. Los
    hijos van un nivel más adentro que la clave (lo exige YAML y, con la misma
    indentación, `yamlmini` los leería como claves hermanas).
    """
    out: list[str] = []
    for line in text.splitlines():
        match = _INLINE_MAP_LINE.match(line)
        if not match:
            out.append(line)
            continue

        indent = match.group("indent").expandtabs(4)
        dash = match.group("dash") or ""
        key = match.group("key")
        inner = match.group("value")[1:-1]
        out.append("%s%s%s:" % (indent, dash, key))
        child_indent = " " * (len(indent) + (2 if dash else 0) + 2)
        for pair_key, pair_value in _split_flow_pairs(inner):
            out.append("%s%s: %s" % (child_indent, pair_key, pair_value))
    return "\n".join(out)


def _parse_session_yaml(raw) -> dict:
    """Parsea el bloque recortado y tolera prosa pegada al final."""
    extracted = _normalize_inline_maps(_extract_session_yaml(raw))
    lines = extracted.splitlines()
    candidates = [extracted]
    for end in range(len(lines) - 1, 0, -1):
        candidate = "\n".join(lines[:end]).rstrip()
        if candidate:
            candidates.append(candidate)

    last_error = "sin YAML"
    for candidate in candidates:
        try:
            parsed = yamlmini.parse(candidate)
        except Exception as exc:  # noqa: BLE001 - cualquier fallo es "no es YAML"
            last_error = str(exc)
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("session"), dict):
            session = parsed["session"]
        elif isinstance(parsed, dict) and any(
            key in parsed for key in ("scores", "corrections", "new_vocab", "next_goal")
        ):
            session = parsed
        else:
            continue
        # Un `scores` que no es mapa se perdería en silencio al renderizar: se
        # dice, en vez de devolver un informe con las puntuaciones vacías.
        if "scores" in session and not isinstance(session["scores"], dict):
            raise ValueError(
                "YAML del modelo ilegible: scores no es un mapa (%r)"
                % (session["scores"],)
            )
        return session
    raise ValueError("YAML del modelo ilegible: %s" % last_error)


def _said_in_turn(said: str, turn_text: str) -> bool:
    """¿La corrección cita literalmente este turno?

    Se toleran mayúsculas y puntuación (el STT las cambia), pero no palabras
    distintas ni frases inventadas: ``contains`` compara la secuencia de tokens.
    """
    if not said or not turn_text:
        return False
    return said in turn_text or textutils.contains(said, turn_text)


def _clean_corrections(raw, learner_turns: list[str]):
    """Valida las correcciones y descarta las que no están en la transcripción."""
    warnings = []
    if raw is None:
        return [], warnings
    if not isinstance(raw, list):
        return [], ["corrections no es una lista; se ignora"]

    out = []
    for entry in raw:
        if not isinstance(entry, dict):
            warnings.append("corrección no es un mapa; se ignora")
            continue
        said = _text(entry.get("said"))
        better = _text(entry.get("better"))
        if not said or not better:
            warnings.append("corrección sin said/better descartada")
            continue
        if not any(_said_in_turn(said, turn) for turn in learner_turns):
            warnings.append(
                "corrección descartada: said no aparece literalmente en la "
                "transcripción: %r" % said
            )
            continue
        pattern, _warning = patterns.normalize(entry.get("pattern"))
        out.append({"said": said, "better": better, "pattern": pattern})
    return out, warnings


def _clean_vocab(raw) -> list[str]:
    if isinstance(raw, str):
        raw = [raw] if raw.strip() else []
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


_PLAIN_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/,;()'-]*$")
_YAML_WORDS = ("null", "none", "~", "true", "false", "yes", "no")


def _yaml_scalar(value) -> str:
    """Escalar YAML seguro: plano si no hay comillas, comillas dobles si hace falta."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return json.dumps(str(value))
        return repr(value)
    string = str(value)
    if (
        _PLAIN_SAFE.match(string)
        and string.lower() not in _YAML_WORDS
        and not re.fullmatch(r"-?\d+(\.\d+)?", string)
    ):
        return string
    return json.dumps(string, ensure_ascii=False)


def _yaml_lines(value, indent: int = 0) -> list[str]:
    """Serializa el subconjunto de YAML que usa el informe (mapas y secuencias)."""
    pad = "  " * indent
    lines: list[str] = []

    if isinstance(value, dict):
        for key, item in value.items():
            lines.extend(_yaml_entry(str(key), item, pad, indent + 1))
        return lines

    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict):
                if not item:
                    lines.append(pad + "- {}")
                    continue
                for pos, (key, sub) in enumerate(item.items()):
                    first = pos == 0
                    lines.extend(
                        _yaml_entry(
                            str(key),
                            sub,
                            pad if first else "  " * (indent + 1),
                            indent + 2,
                            "- " if first else "",
                        )
                    )
            elif isinstance(item, (list, tuple)):
                if not item:
                    lines.append(pad + "- []")
                else:
                    lines.append(pad + "-")
                    lines.extend(_yaml_lines(list(item), indent + 1))
            else:
                lines.append(pad + "- " + _yaml_scalar(item))
        return lines

    lines.append(pad + _yaml_scalar(value))
    return lines


def _yaml_entry(key: str, value, pad: str, child_indent: int,
                marker: str = "") -> list[str]:
    prefix = pad + marker
    if isinstance(value, dict):
        if not value:
            return ["%s%s: {}" % (prefix, key)]
        return ["%s%s:" % (prefix, key)] + _yaml_lines(value, child_indent)
    if isinstance(value, (list, tuple)):
        if not value:
            return ["%s%s: []" % (prefix, key)]
        return ["%s%s:" % (prefix, key)] + _yaml_lines(list(value), child_indent)
    return ["%s%s: %s" % (prefix, key, _yaml_scalar(value))]


def _dump_yaml(document: dict) -> str:
    return "\n".join(_yaml_lines(document)) + "\n"


def _render_session_block(session: dict, *, date_str: str, minutes, difficulty: str,
                          scenario: str, learner_turns: list[str]) -> str:
    """Fija los campos que manda Python y sanea correcciones/vocabulario."""
    corrections, warnings = _clean_corrections(
        session.get("corrections"), learner_turns
    )
    model_warnings = session.get("warnings")
    if isinstance(model_warnings, list):
        warnings = [str(w).strip() for w in model_warnings if str(w).strip()] + warnings

    ordered: dict = {
        "date": date_str,
        "minutes": minutes,
        "difficulty": difficulty,
        "scenario": scenario,
    }
    if "scores" in session:
        ordered["scores"] = session["scores"] if isinstance(session["scores"], dict) else {}
    ordered["corrections"] = corrections
    if "new_vocab" in session:
        ordered["new_vocab"] = _clean_vocab(session["new_vocab"])
    if "next_goal" in session:
        ordered["next_goal"] = session["next_goal"]
    for key, value in session.items():
        if key in ordered or key == "warnings":
            continue
        ordered[key] = value

    document = {"session": ordered}
    if warnings:
        # El contrato no fija dónde van los avisos: se publican al mismo nivel que
        # `session` (como en los informes guardados) para no ensuciar el esquema.
        document["warnings"] = warnings
    return _dump_yaml(document)


def build_session_block(turns, scenario, minutes, difficulty, llm=None) -> str:
    """Convierte una transcripción en el bloque ``session:`` del informe.

    ``date``, ``minutes``, ``difficulty`` y ``scenario`` los pone Python: se
    sobrescriben aunque el modelo se atreva a escribirlos. ``llm`` es inyectable
    para los tests (una función ``llm(prompt) -> str``); por defecto se usa el
    agente de informes de la app.
    """
    rows = _turn_rows(turns)
    learner_turns = [
        row["text"] for row in rows if row["role"].lower() in _LEARNER_ROLES
    ]
    if not learner_turns:
        # Sin roles reconocibles no se puede distinguir interlocutor y alumno:
        # se valida contra la transcripción entera en vez de descartar todo.
        learner_turns = [row["text"] for row in rows]

    today = date.today().isoformat()
    minutes_int = _minutes_value(minutes)
    difficulty_value = _text(difficulty).upper() or None
    scenario_value = _scenario_name(scenario)

    payload = {
        "today": today,
        "minutes": minutes_int,
        "difficulty": difficulty_value,
        "scenario": _scenario_input(scenario),
        "transcript": rows,
    }
    template = prompts.load("wrapup.md")
    prompt = prompts.fill(
        template,
        {"INPUT": json.dumps(payload, ensure_ascii=False, indent=2)},
    )
    raw = _call_report_llm(llm, prompt)
    session = _parse_session_yaml(raw)

    return _render_session_block(
        session,
        date_str=today,
        minutes=minutes_int,
        difficulty=difficulty_value,
        scenario=scenario_value,
        learner_turns=learner_turns,
    )


def _import_voicekit():
    """Importa el paquete del motor; cualquier fallo de importación es 503."""
    try:
        return importlib.import_module("voicekit")
    except Exception as exc:  # ImportError, o una instalación rota
        raise TalkUnavailable("voicekit no está disponible: %s" % exc) from exc


def available() -> bool:
    """¿Se puede importar ``voicekit`` en este intérprete?"""
    try:
        _import_voicekit()
        return True
    except TalkUnavailable:
        return False


def current() -> "TalkSession | None":
    """La sesión activa, o ``None``. La usan las rutas de ``app.py``."""
    return _ACTIVE


def _config_path():
    """El TOML del motor de voz, que vive en el equipo y no en el repo.

    Lleva la ruta del modelo de cada uno, así que se queda en
    ``~/.config/refire/voicekit.toml`` (o donde diga ``REFIRE_VOICEKIT``); el repo solo
    trae ``voicekit.example.toml`` como plantilla.
    """
    raw = os.environ.get("REFIRE_VOICEKIT")
    if raw:
        return Path(raw).expanduser()
    return Path("~/.config/refire/voicekit.toml").expanduser()


class _UnavailableLlm:
    """LLM que falla en el primer turno con el error de configuración de M3e."""

    def __init__(self, error):
        self._error = error

    def stream(self, messages, cancel):
        raise self._error


def _build_providers(config):
    """Fábrica de los tres proveedores reales; los tests la sustituyen por fakes."""
    from voicekit.providers.base import ProviderError
    from voicekit.providers.llm_openai import OpenAiCompatLlm
    from voicekit.providers.stt_whisper import WhisperStt
    from voicekit.providers.tts_piper import PiperTts

    stt = WhisperStt(model=config.stt_model, prompt=config.stt_prompt)
    try:
        llm = OpenAiCompatLlm(
            config.llm_base_url,
            config.llm_model,
            api_key_env=config.llm_api_key_env,
            timeout=config.llm_timeout,
            headers=config.llm_headers,
        )
    except ProviderError as exc:
        # Sin fichero de configuración no se puede construir el proveedor, pero la
        # sesión debe abrirse: el primer turno contará ``llm_unavailable``.
        llm = _UnavailableLlm(exc)
    tts = PiperTts(config.tts_model_path)
    return stt, llm, tts


class TalkSession:
    """Una sesión de conversación respaldada por ``voicekit.session.SessionStore``."""

    def __init__(self, store, engine, events, events_lock, scenario=None,
                 llm=None, difficulty=None):
        self._store = store
        self._engine = engine
        self._events = events
        self._events_lock = events_lock
        self._wavs: dict[int, str] = {}
        self.scenario = scenario
        # El mismo proveedor que usan los turnos manda también el informe (M5g2).
        self._llm = llm
        self._difficulty = _difficulty_value(difficulty, scenario)
        self._started_at = datetime.now()
        self._closed_at: datetime | None = None
        self._minutes: int | None = None
        self._closed_result: dict | None = None

    @classmethod
    def start(cls, config=None, system_prompt: str = "",
              scenario: dict | None = None,
              difficulty: str | None = None) -> "TalkSession":
        """Crea la sesión y el motor; si ya hay una activa lanza ``TalkBusy``."""
        global _ACTIVE

        with _LOCK:
            if _ACTIVE is not None:
                raise TalkBusy("ya hay una sesión activa")
            _import_voicekit()
            from voicekit.config import load_config
            from voicekit.engine import Engine
            from voicekit.session import SessionStore

            if config is None:
                from . import config as app_config

                cfg = load_config(
                    path=_config_path(),
                    data_dir=app_config.data_dir() / "talk",
                )
            else:
                cfg = config
            stt, llm, tts = _build_providers(cfg)
            store = SessionStore(cfg.data_dir)

            if scenario is not None and not system_prompt:
                system_prompt = build_system_prompt(scenario)

            events: list[dict] = []
            events_lock = threading.Lock()

            def sink(event) -> None:
                payload = event.to_dict()
                with events_lock:
                    events.append(payload)

            engine = Engine(
                stt,
                llm,
                tts,
                session=store.session,
                sink=sink,
                timeouts={
                    "stt": cfg.stt_timeout,
                    "llm_total": cfg.llm_timeout,
                    "tts": cfg.tts_timeout,
                },
                system_prompt=system_prompt,
                tts_dir=store.dir / "tts",
            )
            session = cls(
                store,
                engine,
                events,
                events_lock,
                scenario=scenario,
                llm=llm,
                difficulty=difficulty,
            )
            _ACTIVE = session
            return session

    @property
    def session(self) -> str:
        return self._store.session

    def turn(self, audio: bytes) -> int:
        """Abre un turno con audio, lanza el trabajo en un hilo y devuelve su número."""
        self._ensure_current()
        from voicekit.engine import TurnBusy as EngineTurnBusy

        try:
            number = self._engine.begin_turn()
        except EngineTurnBusy as exc:
            raise TalkBusy(str(exc)) from exc

        # El WAV del usuario es la fuente para reprocesar (H5). Si ffmpeg no puede
        # guardarlo, el turno sigue: el motor transcribe los bytes originales.
        try:
            path = self._store.save_turn_wav(number, audio)
            self._wavs[number] = path.name
        except Exception:  # noqa: BLE001
            pass

        # El trabajo sigue en su hilo: la transcripción y los errores llegan por
        # eventos, así que el turno se devuelve sin esperar al STT.
        self._engine.end_turn(number, audio)
        return number

    def say(self, text: str) -> int:
        """Igual que ``turn`` pero sin STT, para la entrada escrita."""
        self._ensure_current()
        from voicekit.engine import TurnBusy as EngineTurnBusy

        try:
            number = self._engine.begin_turn()
        except EngineTurnBusy as exc:
            raise TalkBusy(str(exc)) from exc
        self._engine.say(number, text)
        return number

    def events(self, since: int = 0) -> list[dict]:
        """Eventos con ``seq > since``, en orden de emisión."""
        with self._events_lock:
            return [
                {**event, "data": dict(event.get("data") or {})}
                for event in self._events
                if event["seq"] > since
            ]

    def wait(self, turn: int, timeout: float = 10.0) -> bool:
        """Espera al hilo de un turno; útil para pruebas y para el cierre."""
        return self._engine.wait(turn, timeout)

    def end(self, report: bool = False) -> dict:
        """Cierra la sesión y devuelve ``{session, state, turns, metrics}``.

        Con ``report=True`` además construye el informe con el LLM configurado
        y lo mete por el mismo camino que el pegado
        (``session.process_report``), añadiendo ``report: {row, drills, warnings}``.
        El cierre se hace antes: si el informe falla, la sesión ya está cerrada
        y sus turnos siguen recuperables con ``end(report=False)``.
        """
        global _ACTIVE

        with _LOCK:
            payload = self._closed_result
            if payload is None:
                self._ensure_current()
                from voicekit.session import TurnRecord

                turns = []
                for row in self._engine.transcript():
                    turns.append(
                        TurnRecord(
                            number=row["turn"],
                            role=row["role"],
                            text=row["text"],
                            heard=bool(row.get("heard", True)),
                            wav=self._wavs.get(row["turn"]) if row["role"] == "user" else None,
                        )
                    )

                metrics = []
                for event in self.events(0):
                    if event["type"] != "turn.done":
                        continue
                    data = event.get("data") or {}
                    row = {"turn": event["turn"]}
                    row.update(data.get("metrics") or {})
                    if data.get("cancelled"):
                        row["cancelled"] = True
                    if data.get("error"):
                        row["error"] = data["error"]
                    metrics.append(row)

                try:
                    result = self._store.close(turns, metrics)
                finally:
                    _ACTIVE = None

                self._closed_at = datetime.now()
                self._minutes = self._minutes_from_clock(self._closed_at)
                payload = self._closed_result = {
                    "session": result.session,
                    "state": result.state,
                    "turns": [
                        {
                            "number": turn.number,
                            "role": turn.role,
                            "text": turn.text,
                            "heard": turn.heard,
                            "wav": turn.wav,
                        }
                        for turn in result.turns
                    ],
                    "metrics": result.metrics,
                }

            out = dict(payload)
            if not report:
                return out
            out["report"] = self._build_report(payload)
            return out

    def _minutes_from_clock(self, closed_at: datetime) -> int:
        """Minutos de pared entre el arranque y el cierre, redondeados y mínimo 1."""
        elapsed = (closed_at - self._started_at).total_seconds()
        return max(1, int(round(elapsed / 60.0)))

    def _build_report(self, payload: dict) -> dict:
        """Genera el informe y lo entrega al pipeline existente de ``/api/report``."""
        from . import session as report_session

        try:
            raw = build_session_block(
                payload["turns"],
                self.scenario,
                self._minutes,
                self._difficulty,
                llm=self._llm,
            )
        except Exception as exc:  # noqa: BLE001 - cualquier fallo del modelo es 502
            raise TalkReportFailed(str(exc)) from exc

        try:
            result = report_session.process_report(raw)
        except report_session.DuplicateReport as exc:
            # El camino existente corta el duplicado; aquí es un aviso, no un 500.
            doc = _saved_report(raw) or {}
            warnings = list(doc.get("warnings") or [])
            warnings.append("informe ya procesado para %s" % exc)
            return {
                "row": doc.get("row"),
                "drills": list(doc.get("drills") or []),
                "warnings": warnings,
            }
        except Exception as exc:  # noqa: BLE001 - YAML inválido, vault, E/S...
            raise TalkReportFailed(str(exc)) from exc

        drills = result.get("drills")
        if drills is None:
            doc = _saved_report(raw) or {}
            drills = doc.get("drills") or []
        return {
            "row": result["row"],
            "drills": list(drills),
            "warnings": list(result.get("warnings") or []),
        }

    def _ensure_current(self) -> None:
        if _ACTIVE is not self:
            raise TalkBusy("la sesión ya está cerrada")
