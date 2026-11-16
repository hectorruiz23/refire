/* GENERADO desde static/mock/*.json — no editar a mano.
   Copia inline de los fixtures para que ?mock=1 funcione también abriendo
   index.html con file://, donde el navegador bloquea fetch() de JSON.
   Si cambias un fixture, regenera este fichero con:
     python3 static/mock/build.py
   (build.py no se ejecuta en el navegador; es solo para desarrollo). */
window.__MOCK__ = {
  "state": {
    "sessions": [
      {
        "date": "2026-10-27",
        "minutes": 11,
        "difficulty": "B1",
        "scenario": "daily standup at a fintech",
        "scores": {
          "fluency": 3,
          "grammar": 2,
          "lexis": 3,
          "organisation": 3,
          "interaction": 3
        },
        "corrections": [
          {
            "said": "I didn't went to the meeting",
            "better": "I didn't go to the meeting",
            "pattern": "verb-form"
          },
          {
            "said": "We need more informations about the release",
            "better": "We need more information about the release",
            "pattern": "word-choice"
          },
          {
            "said": "The deploy was made by the on-call engineer",
            "better": "The deploy was done by the on-call engineer",
            "pattern": "collocations"
          }
        ],
        "new_vocab": [
          "on-call",
          "rollback"
        ],
        "fillers_per_minute": null,
        "next_goal": "speak for two minutes without switching to Spanish",
        "previously_corrected_used_right": []
      },
      {
        "date": "2026-10-30",
        "minutes": 13,
        "difficulty": "B2",
        "scenario": "explaining a rollback to a product owner",
        "scores": {
          "fluency": 3,
          "grammar": 3,
          "lexis": 3,
          "organisation": 2,
          "interaction": 3
        },
        "corrections": [
          {
            "said": "It depends of the queue",
            "better": "It depends on the queue",
            "pattern": "prepositions"
          },
          {
            "said": "We did a mistake in the migration",
            "better": "We made a mistake in the migration",
            "pattern": "collocations"
          },
          {
            "said": "I will explain you the timeline",
            "better": "I will explain the timeline to you",
            "pattern": "verb-form"
          },
          {
            "said": "The latency is very big",
            "better": "The latency is very high",
            "pattern": "word-choice"
          }
        ],
        "new_vocab": [
          "blast radius",
          "to page someone"
        ],
        "fillers_per_minute": null,
        "next_goal": "structure the answer before speaking: cause, impact, next step",
        "previously_corrected_used_right": [
          "conditionals"
        ]
      },
      {
        "date": "2026-11-02",
        "minutes": 12,
        "difficulty": "B2",
        "scenario": "architecture review",
        "scores": {
          "fluency": 4,
          "grammar": 3,
          "lexis": 3,
          "organisation": 3,
          "interaction": 4
        },
        "corrections": [
          {
            "said": "Even it have side effects",
            "better": "Even if it has side effects",
            "pattern": "conditionals"
          },
          {
            "said": "If I would have known, I would raise it earlier",
            "better": "If I had known, I would have raised it earlier",
            "pattern": "conditionals"
          },
          {
            "said": "I found issue in the config",
            "better": "I found an issue in the config",
            "pattern": "articles"
          },
          {
            "said": "He is senior engineer in the payments team",
            "better": "He is a senior engineer in the payments team",
            "pattern": "articles"
          },
          {
            "said": "We did a mistake in the deploy",
            "better": "We made a mistake in the deploy",
            "pattern": "collocations"
          },
          {
            "said": "Let's discuss about the retry policy",
            "better": "Let's discuss the retry policy",
            "pattern": "prepositions"
          },
          {
            "said": "The latency is very big during the batch",
            "better": "The latency is very high during the batch",
            "pattern": "word-choice"
          }
        ],
        "new_vocab": [
          "follow up on a decision"
        ],
        "fillers_per_minute": null,
        "next_goal": "hold the same level on a non-work topic",
        "previously_corrected_used_right": [
          "conditionals"
        ]
      }
    ],
    "patterns": [
      {
        "name": "conditionals",
        "attempts": 5,
        "correct": 3,
        "pass_rate": 0.6,
        "streak": 1,
        "due": 2
      },
      {
        "name": "articles",
        "attempts": 4,
        "correct": 4,
        "pass_rate": 1.0,
        "streak": 3,
        "due": 0
      },
      {
        "name": "collocations",
        "attempts": 6,
        "correct": 3,
        "pass_rate": 0.5,
        "streak": 0,
        "due": 2
      },
      {
        "name": "prepositions",
        "attempts": 5,
        "correct": 2,
        "pass_rate": 0.4,
        "streak": 0,
        "due": 2
      },
      {
        "name": "word-choice",
        "attempts": 4,
        "correct": 1,
        "pass_rate": 0.25,
        "streak": 0,
        "due": 1
      }
    ],
    "drills": {
      "due_now": 7,
      "total": 12,
      "retired": 1,
      "items": [
        {
          "id": "d-0001",
          "pattern": "conditionals",
          "prompt": "If I ___ (know) it was that urgent, I would have called you.",
          "answer": "had known",
          "alt_answers": [
            "'d known"
          ],
          "source": {
            "said": "If I would have known it was urgent",
            "better": "If I had known it was urgent"
          },
          "created": "2026-11-02",
          "streak": 0,
          "wrong": 1,
          "last_seen": "2026-11-02",
          "due": "2026-11-02",
          "retired": false
        },
        {
          "id": "d-0002",
          "pattern": "conditionals",
          "prompt": "Even if it ___ side effects, we should ship it.",
          "answer": "has",
          "alt_answers": [
            "does have"
          ],
          "source": {
            "said": "Even it have side effects",
            "better": "Even if it has side effects"
          },
          "created": "2026-11-02",
          "streak": 2,
          "wrong": 0,
          "last_seen": "2026-11-02",
          "due": "2026-11-02",
          "retired": false
        },
        {
          "id": "d-0003",
          "pattern": "conditionals",
          "prompt": "___ we had more time, we would rewrite the parser.",
          "answer": "If",
          "alt_answers": [],
          "source": {
            "said": "Whether we had more time",
            "better": "If we had more time"
          },
          "created": "2026-11-01",
          "streak": 0,
          "wrong": 2,
          "last_seen": "2026-11-01",
          "due": "2026-11-02",
          "retired": false
        },
        {
          "id": "d-0004",
          "pattern": "articles",
          "prompt": "He is ___ senior engineer in the payments team.",
          "answer": "a",
          "alt_answers": [],
          "source": {
            "said": "He is senior engineer in the payments team",
            "better": "He is a senior engineer in the payments team"
          },
          "created": "2026-11-02",
          "streak": 0,
          "wrong": 1,
          "last_seen": "2026-11-02",
          "due": "2026-11-02",
          "retired": false
        },
        {
          "id": "d-0005",
          "pattern": "articles",
          "prompt": "I found ___ issue in the config on Monday.",
          "answer": "an",
          "alt_answers": [],
          "source": {
            "said": "I found issue in the config",
            "better": "I found an issue in the config"
          },
          "created": "2026-11-02",
          "streak": 1,
          "wrong": 0,
          "last_seen": "2026-11-02",
          "due": "2026-11-05",
          "retired": false
        },
        {
          "id": "d-0006",
          "pattern": "collocations",
          "prompt": "We ___ a mistake in the deploy.",
          "answer": "made",
          "alt_answers": [
            "have made"
          ],
          "source": {
            "said": "We did a mistake in the deploy",
            "better": "We made a mistake in the deploy"
          },
          "created": "2026-11-02",
          "streak": 0,
          "wrong": 2,
          "last_seen": "2026-11-02",
          "due": "2026-11-02",
          "retired": false
        },
        {
          "id": "d-0007",
          "pattern": "collocations",
          "prompt": "Let's ___ a decision about the migration today.",
          "answer": "make",
          "alt_answers": [],
          "source": {
            "said": "Let's take a decision about the migration",
            "better": "Let's make a decision about the migration"
          },
          "created": "2026-10-30",
          "streak": 1,
          "wrong": 0,
          "last_seen": "2026-11-01",
          "due": "2026-11-03",
          "retired": false
        },
        {
          "id": "d-0008",
          "pattern": "collocations",
          "prompt": "We need to ___ the issue before Friday.",
          "answer": "raise",
          "alt_answers": [
            "bring up"
          ],
          "source": {
            "said": "We need to rise the issue before Friday",
            "better": "We need to raise the issue before Friday"
          },
          "created": "2026-10-30",
          "streak": 0,
          "wrong": 1,
          "last_seen": "2026-10-30",
          "due": "2026-11-04",
          "retired": false
        },
        {
          "id": "d-0009",
          "pattern": "prepositions",
          "prompt": "It depends ___ the queue depth.",
          "answer": "on",
          "alt_answers": [
            "upon"
          ],
          "source": {
            "said": "It depends of the queue",
            "better": "It depends on the queue"
          },
          "created": "2026-10-30",
          "streak": 0,
          "wrong": 1,
          "last_seen": "2026-11-02",
          "due": "2026-11-02",
          "retired": false
        },
        {
          "id": "d-0010",
          "pattern": "prepositions",
          "prompt": "I'm responsible ___ the billing service.",
          "answer": "for",
          "alt_answers": [],
          "source": {
            "said": "I'm responsible of the billing service",
            "better": "I'm responsible for the billing service"
          },
          "created": "2026-10-28",
          "streak": 1,
          "wrong": 0,
          "last_seen": "2026-11-01",
          "due": "2026-11-06",
          "retired": false
        },
        {
          "id": "d-0011",
          "pattern": "word-choice",
          "prompt": "The latency is very ___ during the batch window.",
          "answer": "high",
          "alt_answers": [],
          "source": {
            "said": "The latency is very big",
            "better": "The latency is very high"
          },
          "created": "2026-11-02",
          "streak": 0,
          "wrong": 2,
          "last_seen": "2026-11-02",
          "due": "2026-11-02",
          "retired": false
        },
        {
          "id": "d-0012",
          "pattern": "conditionals",
          "prompt": "I would have caught it if I ___ (look) at the logs.",
          "answer": "had looked",
          "alt_answers": [
            "'d looked"
          ],
          "source": {
            "said": "I would caught it if I looked at the logs",
            "better": "I would have caught it if I had looked at the logs"
          },
          "created": "2026-10-27",
          "streak": 3,
          "wrong": 0,
          "last_seen": "2026-10-31",
          "due": null,
          "retired": true
        },
        {
          "id": "d-0013",
          "kind": "model",
          "pattern": "articles",
          "prompt": "I am the responsible for the deploy",
          "answer": "I am responsible for the deploy",
          "alt_answers": [],
          "source": {
            "said": "I am the responsible for the deploy",
            "better": "I am responsible for the deploy"
          },
          "created": "2026-10-30",
          "streak": 0,
          "wrong": 0,
          "last_seen": null,
          "due": "2026-11-01",
          "retired": false
        },
        {
          "id": "d-0014",
          "kind": "model",
          "pattern": "prepositions",
          "prompt": "In the other side, the plan is risky",
          "answer": "On the one hand, the migration is risky",
          "alt_answers": [],
          "source": {
            "said": "In the other side, the plan is risky",
            "better": "On the one hand, the migration is risky"
          },
          "created": "2026-10-30",
          "streak": 1,
          "wrong": 0,
          "last_seen": "2026-10-31",
          "due": "2026-11-02",
          "retired": false
        }
      ],
      "models": {
        "due_now": 2,
        "total": 2,
        "retired": 0,
        "seen": 5,
        "to_review": 2
      }
    },
    "stats": {
      "sessions": 3,
      "measured": {
        "fillers_per_minute": null
      },
      "models": {
        "seen": 5,
        "to_review": 2
      }
    }
  },
  "state-long": {
    "sessions": [
      {
        "date": "2026-10-12",
        "minutes": 10,
        "difficulty": "B1",
        "scenario": "introducing myself to a new team",
        "scores": {
          "fluency": 2,
          "grammar": 2,
          "lexis": 2,
          "organisation": 2,
          "interaction": 3
        },
        "corrections": [
          {
            "said": "example sentence with an error",
            "better": "example sentence without the error",
            "pattern": "word-choice"
          }
        ],
        "new_vocab": [],
        "fillers_per_minute": null,
        "next_goal": "keep the pace",
        "previously_corrected_used_right": []
      },
      {
        "date": "2026-10-15",
        "minutes": 10,
        "difficulty": "B1",
        "scenario": "describing a bug to a colleague",
        "scores": {
          "fluency": 3,
          "grammar": 2,
          "lexis": 2,
          "organisation": 3,
          "interaction": 3
        },
        "corrections": [
          {
            "said": "example sentence with an error",
            "better": "example sentence without the error",
            "pattern": "word-choice"
          }
        ],
        "new_vocab": [],
        "fillers_per_minute": null,
        "next_goal": "keep the pace",
        "previously_corrected_used_right": []
      },
      {
        "date": "2026-10-19",
        "minutes": 12,
        "difficulty": "B1",
        "scenario": "retrospective: what went wrong last sprint",
        "scores": {
          "fluency": 3,
          "grammar": 2,
          "lexis": 3,
          "organisation": 3,
          "interaction": 3
        },
        "corrections": [
          {
            "said": "example sentence with an error",
            "better": "example sentence without the error",
            "pattern": "word-choice"
          }
        ],
        "new_vocab": [],
        "fillers_per_minute": null,
        "next_goal": "keep the pace",
        "previously_corrected_used_right": []
      },
      {
        "date": "2026-10-22",
        "minutes": 10,
        "difficulty": "B2",
        "scenario": "negotiating scope with a product owner",
        "scores": {
          "fluency": 3,
          "grammar": 2,
          "lexis": 3,
          "organisation": 3,
          "interaction": 4
        },
        "corrections": [
          {
            "said": "example sentence with an error",
            "better": "example sentence without the error",
            "pattern": "word-choice"
          }
        ],
        "new_vocab": [],
        "fillers_per_minute": null,
        "next_goal": "keep the pace",
        "previously_corrected_used_right": []
      },
      {
        "date": "2026-10-24",
        "minutes": 14,
        "difficulty": "B2",
        "scenario": "walking a new hire through the deploy pipeline",
        "scores": {
          "fluency": 3,
          "grammar": 3,
          "lexis": 3,
          "organisation": 3,
          "interaction": 3
        },
        "corrections": [
          {
            "said": "example sentence with an error",
            "better": "example sentence without the error",
            "pattern": "word-choice"
          }
        ],
        "new_vocab": [],
        "fillers_per_minute": null,
        "next_goal": "keep the pace",
        "previously_corrected_used_right": []
      },
      {
        "date": "2026-10-25",
        "minutes": 12,
        "difficulty": "B2",
        "scenario": "on-call handover",
        "scores": {
          "fluency": 4,
          "grammar": 3,
          "lexis": 3,
          "organisation": 3,
          "interaction": 3
        },
        "corrections": [
          {
            "said": "example sentence with an error",
            "better": "example sentence without the error",
            "pattern": "word-choice"
          }
        ],
        "new_vocab": [],
        "fillers_per_minute": null,
        "next_goal": "keep the pace",
        "previously_corrected_used_right": []
      },
      {
        "date": "2026-10-27",
        "minutes": 11,
        "difficulty": "B1",
        "scenario": "daily standup at a fintech",
        "scores": {
          "fluency": 3,
          "grammar": 2,
          "lexis": 3,
          "organisation": 3,
          "interaction": 3
        },
        "corrections": [
          {
            "said": "I didn't went to the meeting",
            "better": "I didn't go to the meeting",
            "pattern": "verb-form"
          },
          {
            "said": "We need more informations about the release",
            "better": "We need more information about the release",
            "pattern": "word-choice"
          },
          {
            "said": "The deploy was made by the on-call engineer",
            "better": "The deploy was done by the on-call engineer",
            "pattern": "collocations"
          }
        ],
        "new_vocab": [
          "on-call",
          "rollback"
        ],
        "fillers_per_minute": null,
        "next_goal": "speak for two minutes without switching to Spanish",
        "previously_corrected_used_right": []
      },
      {
        "date": "2026-10-30",
        "minutes": 13,
        "difficulty": "B2",
        "scenario": "explaining a rollback to a product owner",
        "scores": {
          "fluency": 3,
          "grammar": 3,
          "lexis": 3,
          "organisation": 2,
          "interaction": 3
        },
        "corrections": [
          {
            "said": "It depends of the queue",
            "better": "It depends on the queue",
            "pattern": "prepositions"
          },
          {
            "said": "We did a mistake in the migration",
            "better": "We made a mistake in the migration",
            "pattern": "collocations"
          },
          {
            "said": "I will explain you the timeline",
            "better": "I will explain the timeline to you",
            "pattern": "verb-form"
          },
          {
            "said": "The latency is very big",
            "better": "The latency is very high",
            "pattern": "word-choice"
          }
        ],
        "new_vocab": [
          "blast radius",
          "to page someone"
        ],
        "fillers_per_minute": null,
        "next_goal": "structure the answer before speaking: cause, impact, next step",
        "previously_corrected_used_right": [
          "conditionals"
        ]
      },
      {
        "date": "2026-11-02",
        "minutes": 12,
        "difficulty": "B2",
        "scenario": "architecture review",
        "scores": {
          "fluency": 4,
          "grammar": 3,
          "lexis": 3,
          "organisation": 3,
          "interaction": 4
        },
        "corrections": [
          {
            "said": "Even it have side effects",
            "better": "Even if it has side effects",
            "pattern": "conditionals"
          },
          {
            "said": "If I would have known, I would raise it earlier",
            "better": "If I had known, I would have raised it earlier",
            "pattern": "conditionals"
          },
          {
            "said": "I found issue in the config",
            "better": "I found an issue in the config",
            "pattern": "articles"
          },
          {
            "said": "He is senior engineer in the payments team",
            "better": "He is a senior engineer in the payments team",
            "pattern": "articles"
          },
          {
            "said": "We did a mistake in the deploy",
            "better": "We made a mistake in the deploy",
            "pattern": "collocations"
          },
          {
            "said": "Let's discuss about the retry policy",
            "better": "Let's discuss the retry policy",
            "pattern": "prepositions"
          },
          {
            "said": "The latency is very big during the batch",
            "better": "The latency is very high during the batch",
            "pattern": "word-choice"
          }
        ],
        "new_vocab": [
          "follow up on a decision"
        ],
        "fillers_per_minute": null,
        "next_goal": "hold the same level on a non-work topic",
        "previously_corrected_used_right": [
          "conditionals"
        ]
      }
    ],
    "patterns": [
      {
        "name": "conditionals",
        "attempts": 5,
        "correct": 3,
        "pass_rate": 0.6,
        "streak": 1,
        "due": 2
      },
      {
        "name": "articles",
        "attempts": 4,
        "correct": 4,
        "pass_rate": 1.0,
        "streak": 3,
        "due": 0
      },
      {
        "name": "collocations",
        "attempts": 6,
        "correct": 3,
        "pass_rate": 0.5,
        "streak": 0,
        "due": 2
      },
      {
        "name": "prepositions",
        "attempts": 5,
        "correct": 2,
        "pass_rate": 0.4,
        "streak": 0,
        "due": 2
      },
      {
        "name": "word-choice",
        "attempts": 4,
        "correct": 1,
        "pass_rate": 0.25,
        "streak": 0,
        "due": 1
      }
    ],
    "drills": {
      "due_now": 7,
      "total": 12,
      "retired": 1,
      "items": [
        {
          "id": "d-0001",
          "pattern": "conditionals",
          "prompt": "If I ___ (know) it was that urgent, I would have called you.",
          "answer": "had known",
          "alt_answers": [
            "'d known"
          ],
          "source": {
            "said": "If I would have known it was urgent",
            "better": "If I had known it was urgent"
          },
          "created": "2026-11-02",
          "streak": 0,
          "wrong": 1,
          "last_seen": "2026-11-02",
          "due": "2026-11-02",
          "retired": false
        },
        {
          "id": "d-0002",
          "pattern": "conditionals",
          "prompt": "Even if it ___ side effects, we should ship it.",
          "answer": "has",
          "alt_answers": [
            "does have"
          ],
          "source": {
            "said": "Even it have side effects",
            "better": "Even if it has side effects"
          },
          "created": "2026-11-02",
          "streak": 2,
          "wrong": 0,
          "last_seen": "2026-11-02",
          "due": "2026-11-02",
          "retired": false
        },
        {
          "id": "d-0003",
          "pattern": "conditionals",
          "prompt": "___ we had more time, we would rewrite the parser.",
          "answer": "If",
          "alt_answers": [],
          "source": {
            "said": "Whether we had more time",
            "better": "If we had more time"
          },
          "created": "2026-11-01",
          "streak": 0,
          "wrong": 2,
          "last_seen": "2026-11-01",
          "due": "2026-11-02",
          "retired": false
        },
        {
          "id": "d-0004",
          "pattern": "articles",
          "prompt": "He is ___ senior engineer in the payments team.",
          "answer": "a",
          "alt_answers": [],
          "source": {
            "said": "He is senior engineer in the payments team",
            "better": "He is a senior engineer in the payments team"
          },
          "created": "2026-11-02",
          "streak": 0,
          "wrong": 1,
          "last_seen": "2026-11-02",
          "due": "2026-11-02",
          "retired": false
        },
        {
          "id": "d-0005",
          "pattern": "articles",
          "prompt": "I found ___ issue in the config on Monday.",
          "answer": "an",
          "alt_answers": [],
          "source": {
            "said": "I found issue in the config",
            "better": "I found an issue in the config"
          },
          "created": "2026-11-02",
          "streak": 1,
          "wrong": 0,
          "last_seen": "2026-11-02",
          "due": "2026-11-05",
          "retired": false
        },
        {
          "id": "d-0006",
          "pattern": "collocations",
          "prompt": "We ___ a mistake in the deploy.",
          "answer": "made",
          "alt_answers": [
            "have made"
          ],
          "source": {
            "said": "We did a mistake in the deploy",
            "better": "We made a mistake in the deploy"
          },
          "created": "2026-11-02",
          "streak": 0,
          "wrong": 2,
          "last_seen": "2026-11-02",
          "due": "2026-11-02",
          "retired": false
        },
        {
          "id": "d-0007",
          "pattern": "collocations",
          "prompt": "Let's ___ a decision about the migration today.",
          "answer": "make",
          "alt_answers": [],
          "source": {
            "said": "Let's take a decision about the migration",
            "better": "Let's make a decision about the migration"
          },
          "created": "2026-10-30",
          "streak": 1,
          "wrong": 0,
          "last_seen": "2026-11-01",
          "due": "2026-11-03",
          "retired": false
        },
        {
          "id": "d-0008",
          "pattern": "collocations",
          "prompt": "We need to ___ the issue before Friday.",
          "answer": "raise",
          "alt_answers": [
            "bring up"
          ],
          "source": {
            "said": "We need to rise the issue before Friday",
            "better": "We need to raise the issue before Friday"
          },
          "created": "2026-10-30",
          "streak": 0,
          "wrong": 1,
          "last_seen": "2026-10-30",
          "due": "2026-11-04",
          "retired": false
        },
        {
          "id": "d-0009",
          "pattern": "prepositions",
          "prompt": "It depends ___ the queue depth.",
          "answer": "on",
          "alt_answers": [
            "upon"
          ],
          "source": {
            "said": "It depends of the queue",
            "better": "It depends on the queue"
          },
          "created": "2026-10-30",
          "streak": 0,
          "wrong": 1,
          "last_seen": "2026-11-02",
          "due": "2026-11-02",
          "retired": false
        },
        {
          "id": "d-0010",
          "pattern": "prepositions",
          "prompt": "I'm responsible ___ the billing service.",
          "answer": "for",
          "alt_answers": [],
          "source": {
            "said": "I'm responsible of the billing service",
            "better": "I'm responsible for the billing service"
          },
          "created": "2026-10-28",
          "streak": 1,
          "wrong": 0,
          "last_seen": "2026-11-01",
          "due": "2026-11-06",
          "retired": false
        },
        {
          "id": "d-0011",
          "pattern": "word-choice",
          "prompt": "The latency is very ___ during the batch window.",
          "answer": "high",
          "alt_answers": [],
          "source": {
            "said": "The latency is very big",
            "better": "The latency is very high"
          },
          "created": "2026-11-02",
          "streak": 0,
          "wrong": 2,
          "last_seen": "2026-11-02",
          "due": "2026-11-02",
          "retired": false
        },
        {
          "id": "d-0012",
          "pattern": "conditionals",
          "prompt": "I would have caught it if I ___ (look) at the logs.",
          "answer": "had looked",
          "alt_answers": [
            "'d looked"
          ],
          "source": {
            "said": "I would caught it if I looked at the logs",
            "better": "I would have caught it if I had looked at the logs"
          },
          "created": "2026-10-27",
          "streak": 3,
          "wrong": 0,
          "last_seen": "2026-10-31",
          "due": null,
          "retired": true
        },
        {
          "id": "d-0013",
          "kind": "model",
          "pattern": "articles",
          "prompt": "I am the responsible for the deploy",
          "answer": "I am responsible for the deploy",
          "alt_answers": [],
          "source": {
            "said": "I am the responsible for the deploy",
            "better": "I am responsible for the deploy"
          },
          "created": "2026-10-30",
          "streak": 0,
          "wrong": 0,
          "last_seen": null,
          "due": "2026-11-01",
          "retired": false
        },
        {
          "id": "d-0014",
          "kind": "model",
          "pattern": "prepositions",
          "prompt": "In the other side, the plan is risky",
          "answer": "On the one hand, the migration is risky",
          "alt_answers": [],
          "source": {
            "said": "In the other side, the plan is risky",
            "better": "On the one hand, the migration is risky"
          },
          "created": "2026-10-30",
          "streak": 1,
          "wrong": 0,
          "last_seen": "2026-10-31",
          "due": "2026-11-02",
          "retired": false
        }
      ],
      "models": {
        "due_now": 2,
        "total": 2,
        "retired": 0,
        "seen": 5,
        "to_review": 2
      }
    },
    "stats": {
      "sessions": 9,
      "measured": {
        "fillers_per_minute": 4
      },
      "models": {
        "seen": 5,
        "to_review": 2
      }
    }
  },
  "scenario": {
    "ok": true,
    "scenario": "architecture review: the new cache layer is serving stale prices and the team is split between rolling back and fixing forward",
    "block_b": "SCENARIO\nArchitecture review. A cache layer went live yesterday and some users are seeing stale prices. Two engineers want to roll back; one wants to fix forward. I am the tech lead running the review. You are the colleague who owns the cache service and you are pushing back on the rollback.\n\nYOU PLAY\nThe engineer who built the cache. You defend the design, you are tired, and you only accept a rollback if there is evidence of user impact. You ask short, direct questions. Do not make it easy for me: interrupt once, and ask me to repeat myself once.\n\nI PLAY\nThe tech lead: I have to summarise the incident, weigh both options and decide. I have 10 minutes.\n\nDIFFICULTY\nB2. Speak at a normal pace, with some contractions and some incomplete sentences. Do not simplify your vocabulary for me, but do not use idioms I would never hear at work.\n\nHOW IT ENDS\nWhen I say \"wrap up\", stop the roleplay and answer with ONE fenced YAML block and nothing else, using exactly this shape:\n\nsession:\n  date: <YYYY-MM-DD>\n  minutes: <int>\n  difficulty: <B1|B2|C1>\n  scenario: <short title>\n  scores:\n    fluency: <1-5>\n    grammar: <1-5>\n    lexis: <1-5>\n    organisation: <1-5>\n    interaction: <1-5>\n  corrections:\n    - said: \"<what I actually said>\"\n      better: \"<the corrected sentence>\"\n      pattern: <conditionals|articles|prepositions|collocations|verb-form|word-choice|discourse>\n  new_vocab: [\"...\"]\n  fillers_per_minute: <number or null>\n  next_goal: \"<one line>\"\n  previously_corrected_used_right: []\n\nScores use the fixed anchors: 1 = short and halting, 3 = long sentences with some pauses, 5 = long ideas without effort. Quote my real sentences in \"said\"; never invent corrections I did not make. Maximum 8 corrections.",
    "chatgpt_url": "https://chatgpt.com/",
    "brief": {
      "situation": "The nightly invoice job has been failing for a week: it drops rows when the database is busy.",
      "my_position": "You think a small patch is enough; a rewrite is two weeks you don't have.",
      "stake": "If December goes wrong on your watch, it lands on your review."
    },
    "ammo": [
      "The job only fails when the database is busy - that's a timeout, not a design flaw.",
      "The rewrite has never been tested under load.",
      "Two weeks now means no buffer before peak season."
    ],
    "objections": [
      "A timeout you have patched twice already - why is this time different?",
      "What happens if the patch holds for a month and then fails in December?"
    ],
    "useful": [
      "The way I see it, ...",
      "That's fair, but ...",
      "What I'm worried about is ..."
    ],
    "opener": "So - patch it or rewrite it? Tell me why I should believe the patch holds this time."
  },
  "report": {
    "ok": true,
    "session": {
      "date": "2026-11-02",
      "minutes": 11,
      "difficulty": "B2",
      "scenario": "incident review: stale prices after the cache rollout",
      "scores": {
        "fluency": 4,
        "grammar": 3,
        "lexis": 4,
        "organisation": 3,
        "interaction": 4
      },
      "corrections": [
        {
          "said": "If we would have rolled back, we would lose the fix",
          "better": "If we had rolled back, we would have lost the fix",
          "pattern": "conditionals"
        },
        {
          "said": "We took a decision after the standup",
          "better": "We made a decision after the standup",
          "pattern": "collocations"
        },
        {
          "said": "It depends of the cache key",
          "better": "It depends on the cache key",
          "pattern": "prepositions"
        },
        {
          "said": "The impact is very big for checkout",
          "better": "The impact is very high for checkout",
          "pattern": "word-choice"
        }
      ],
      "new_vocab": [
        "stale read",
        "to fix forward"
      ],
      "fillers_per_minute": 4,
      "next_goal": "decide out loud instead of listing options",
      "previously_corrected_used_right": [
        "articles"
      ]
    },
    "drills_added": 4,
    "row_written": true,
    "warnings": []
  },
  "drills-new": {
    "ok": true,
    "items": [
      {
        "id": "d-0013",
        "pattern": "conditionals",
        "prompt": "If we ___ (roll) back, we would have lost the fix.",
        "answer": "had rolled",
        "alt_answers": [
          "'d rolled"
        ],
        "source": {
          "said": "If we would have rolled back",
          "better": "If we had rolled back"
        },
        "created": "2026-11-02",
        "streak": 0,
        "wrong": 0,
        "last_seen": null,
        "due": "2026-11-02",
        "retired": false
      },
      {
        "id": "d-0014",
        "pattern": "collocations",
        "prompt": "We ___ a decision after the standup.",
        "answer": "made",
        "alt_answers": [
          "have made"
        ],
        "source": {
          "said": "We took a decision",
          "better": "We made a decision"
        },
        "created": "2026-11-02",
        "streak": 0,
        "wrong": 0,
        "last_seen": null,
        "due": "2026-11-02",
        "retired": false
      },
      {
        "id": "d-0015",
        "pattern": "prepositions",
        "prompt": "It depends ___ the cache key.",
        "answer": "on",
        "alt_answers": [
          "upon"
        ],
        "source": {
          "said": "It depends of the cache key",
          "better": "It depends on the cache key"
        },
        "created": "2026-11-02",
        "streak": 0,
        "wrong": 0,
        "last_seen": null,
        "due": "2026-11-02",
        "retired": false
      },
      {
        "id": "d-0016",
        "pattern": "word-choice",
        "prompt": "The impact is very ___ for checkout.",
        "answer": "high",
        "alt_answers": [],
        "source": {
          "said": "The impact is very big",
          "better": "The impact is very high"
        },
        "created": "2026-11-02",
        "streak": 0,
        "wrong": 0,
        "last_seen": null,
        "due": "2026-11-02",
        "retired": false
      }
    ]
  },
  "audio": {
    "ok": true,
    "transcript": "even if it have side effects",
    "expected": "even if it has side effects",
    "match": false,
    "words": [
      {
        "expected": "even",
        "heard": "even",
        "ok": true
      },
      {
        "expected": "if",
        "heard": "if",
        "ok": true
      },
      {
        "expected": "it",
        "heard": "it",
        "ok": true
      },
      {
        "expected": "has",
        "heard": "have",
        "ok": false
      },
      {
        "expected": "side",
        "heard": "side",
        "ok": true
      },
      {
        "expected": "effects",
        "heard": "effects",
        "ok": true
      }
    ],
    "duration_s": 3.4,
    "pauses": 1,
    "engine": "faster-whisper:small"
  },
  "audio-501": {
    "ok": false,
    "error": "audio_unavailable",
    "detail": "faster-whisper no está instalado en este entorno. Usa el modo «me lo sé / no me lo sé»."
  },
  "samples": {
    "ok": true,
    "report": "Aquí va el wrap up de la sesión.\n\n```yaml\nsession:\n  date: 2026-11-02\n  minutes: 11\n  difficulty: B2\n  scenario: incident review: stale prices after the cache rollout\n  scores:\n    fluency: 4\n    grammar: 3\n    lexis: 4\n    organisation: 3\n    interaction: 4\n  corrections:\n    - said: \"If we would have rolled back, we would lose the fix\"\n      better: \"If we had rolled back, we would have lost the fix\"\n      pattern: conditionals\n    - said: \"We took a decision after the standup\"\n      better: \"We made a decision after the standup\"\n      pattern: collocations\n    - said: \"It depends of the cache key\"\n      better: \"It depends on the cache key\"\n      pattern: prepositions\n    - said: \"The impact is very big for checkout\"\n      better: \"The impact is very high for checkout\"\n      pattern: word-choice\n  new_vocab: [\"stale read\", \"to fix forward\"]\n  fillers_per_minute: 4\n  next_goal: \"decide out loud instead of listing options\"\n  previously_corrected_used_right: [\"articles\"]\n```\n",
    "broken": "Wrap up:\n\nsession\n  date: 2026-11-02\n  minutes: 11\n   difficulty B2\n  scores fluency: four, grammar 3\n corrections: [unclosed\n"
  }
};
