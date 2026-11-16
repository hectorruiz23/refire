You are preparing a spoken-English role-play for one learner. Write in English.

LEARNER
- Spanish speaker who works in software: writes code, reviews it, argues about designs.
- Current level B2+, target C1 spoken. Fluency is fine; what breaks is accuracy (verb forms,
  prepositions, articles, collocations) and exact wording.
- No theory, grammar explanations, encouragement or praise. The learner wants a scene.

TASK
Invent ONE realistic professional/technical role-play that someone working in software could
plausibly have this week. It must force long turns of speech and at least a few conditional
sentences (hypotheticals, trade-offs, risk). No job-interview clichés, no "tell me about yourself".

PARAMETERS
- Difficulty: {{DIFFICULTY}}
- Focus patterns to provoke: {{FOCUS}}
- Target length: {{MINUTES}} minutes

SITUATION FAMILY FOR THIS ONE (use it; it is not a suggestion)
{{FAMILY}}

ALREADY USED — do NOT reuse these situations, nor a variation of them:
{{AVOID}}

Variety rules:
- The learner's role must change between sessions: not always the tech lead defending a rewrite,
  but sometimes the one asking, the one saying no, the one who broke it, or the one handing
  something over.
- Do not default to "patch it or rewrite it" or to a nightly job failing unless the family above
  asks for it. That scene is already used up.
- Change the stakes too: sometimes money, sometimes a person, sometimes the learner's own
  credibility, sometimes another team's trust.

RULES — complexity budget (lo importante; el exceso de datos hace la escena imposible de hablar)
- **UNA sola decisión**, dicha como pregunta clara ("patch it or rewrite it?"). Nada de tres opciones.
- **Máximo 3 hechos y máximo 2 números.** Un número de más obliga a justificarlo y se come la
  conversación.
- **Nada de terceros a los que convencer.** No hay CTO esperando fuera, ni comité, ni deadline
  legal además del que ya diga la escena. Solo él y su interlocutor.
- **Como mucho 2 nombres técnicos.** Nada de «Kafka + Postgres + Kubernetes» en la misma frase:
  eso es un examen de vocabulario, no una conversación. Prefiere palabras llanas
  ("the nightly job", "the rewrite", "the database").
- **Vocabulario B2**: sin idioms raros ni jerga de consultoría ("blameless", "psychological
  safety", "parallel workstream"). Si una palabra no la usaría un ingeniero en una llamada normal,
  fuera.
- El interlocutor tiene **una** actitud clara y **una** preocupación, no tres.
- La escena: una o dos frases, **en segunda persona** ("Your nightly job drops rows...", "You want
  a rewrite"): nunca en tercera persona con un "he" sin antecedente.
- Longitud: `scenario` ≤ 220 caracteres, `you_play` e `i_play` ≤ 160 cada uno.

RULES (forma)
- "you_play": el papel que hace ChatGPT (p. ej. "a sceptical engineer who owns the job").
- "i_play": el papel del alumno (p. ej. "the tech lead who wants to rewrite it").
- Nada de listas numeradas ni instrucciones de ejercicio.

OUTPUT
Return ONLY this JSON object, on one line or pretty-printed, with no text before or after, no
markdown fences:

{
  "scenario": "two sentences: the situation and the decision at stake",
  "you_play": "one sentence: who ChatGPT plays and what they want",
  "i_play": "one sentence: who the learner plays and what he wants",
  "brief": {
    "situation": "one sentence of hard context he can lean on",
    "my_position": "the position the learner holds, in one sentence",
    "stake": "what he personally loses or wins, one sentence"
  },
  "ammo": ["fact or number he can quote", "second fact", "third fact"],
  "objections": ["the strongest objection he will have to answer", "second objection"],
  "opener": "the first line ChatGPT says, in character, ending with a question",
  "useful": ["a phrase he will probably need, in English", "second", "third"]
}

WHY THESE EXTRA FIELDS
The learner is B2+ and can argue all day about things they know. What blocks them is facing an open
scene with no foothold: not knowing what to defend, with what facts, or what the other side will
throw at them. The brief, the ammo and the objections are that foothold. The opener exists so the
learner never starts from a blank page.

