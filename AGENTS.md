# AGENTS.md — reglas duras de Refire

App local para practicar inglés hablado. Servidor `http.server` con **solo la biblioteca
estándar** (el venv existe para el audio). Léelo antes de escribir nada.

## Qué no se toca

- `lib/vault.py`, `lib/srs.py`, `prompts/analyze.md`, los drills y el shadowing funcionan: si los
  tocas, que sea por un motivo concreto y con sus tests delante.
- La nota del registro: no se escribe en ella fuera del flujo de `/api/report`.
- El servidor usa **solo la biblioteca estándar**. Las dependencias opcionales de audio van en
  `requirements.txt` y en el `.venv`; nada más se instala.

## Estilo del proyecto

- Python 3.12 o superior, `http.server` con `ThreadingHTTPServer`, sin frameworks, sin asyncio.
- Errores HTTP: `{"ok": false, "error": "<codigo>", "detail": "..."}`. Un código por causa.
- Los tests no llaman al modelo ni a la red: se sustituyen con fixtures o con dobles.
- Comentarios en castellano, nombres de código en inglés.
- Nada de datos personales en el repo: ni rutas de `/home`, ni notas de trabajo, ni audio, ni
  claves (`.env` está ignorado y el repo trae `.env.example` como plantilla).
- El material de trabajo del autor —notas de hito, análisis, semilla de una sesión real— no entra
  en el índice y no viaja: quien clone el repo no lo ve. Publicar es exportar `git ls-files` a un
  clon limpio, nunca `git add -A` sobre un árbol con notas dentro.

## Cómo se comprueba

```
python3 -m unittest discover -s tests -v
```

Y cuando el encargo toque `voicekit`, además, con el venv del proyecto:

```
.venv/bin/python -m unittest discover -s tests -v
```

**Cuando estén verdes, para y entrega.** No refactorices lo que ya pasa.

## Qué cuentas al terminar

1. Ficheros tocados, una línea cada uno.
2. Criterios del encargo: `cumple / no cumple` y el test que lo prueba.
3. **Spec notes**: contradicciones o huecos del encargo, sin resolverlos por tu cuenta.
