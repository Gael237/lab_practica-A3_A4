# Laboratorio A3 + A4 · Producto real con Gemini

Convierte el andamio en un **producto**: una interfaz web que inicia sesión, llama a un **servicio con token**, que a su vez usa **Gemini** (API de Google) y un **agente** cuyas acciones sensibles esperan tu autorización.

```
Streamlit (app/cliente_api.py)
   │  Authorization: Bearer <token>
   ▼
FastAPI (api/main.py) ── /auth/registro · /auth/login  (api/auth.py)
   │                    ├─ /v1/preguntas   caso guía (RAG + Gemini)
   │                    ├─ /v1/variante    TU motor (Avance 1)          ← A3
   │                    └─ /v1/agente → pendiente → /v1/agente/confirmar ← A4
   ▼
Gemini (src/finnorte/llm.py) + herramientas + validador (src/finnorte/agente_servicio.py)
```

## Instalación (en el repositorio de tu equipo)
1. Copia el contenido de este kit en la raíz del repositorio (reemplaza `api/main.py`, `src/finnorte/herramientas.py` y `tests/test_s4_api.py`; los demás archivos son nuevos).
2. Agrega a `.gitignore` la línea `bitacora/usuarios.db`.
3. `pip install -r requirements.txt`
4. En `.env` (nunca se sube):
   ```
   GEMINI_API_KEY=<tu llave de https://aistudio.google.com/apikey>
   LLM_PROVIDER=gemini
   LLM_MODEL=gemini-flash-lite-latest
   EMB_PROVIDER=gemini
   TOKEN_SECRET=<python -c "import secrets; print(secrets.token_hex(32))">
   API_URL=http://127.0.0.1:8000
   ```
5. `python -m finnorte info` debe mostrar `gemini`.

## Cómo se corre (dos terminales)
```
uvicorn api.main:app --reload          # terminal 1 → http://127.0.0.1:8000/docs
streamlit run app/cliente_api.py       # terminal 2 → http://localhost:8501
```

## Qué completas en el laboratorio
| Archivo | Qué falta | Prueba que se enciende |
|---|---|---|
| `api/auth.py` | 5 funciones marcadas `TODO(LAB)` | `pytest tests/test_lab_producto.py` |
| `src/finnorte/herramientas.py` | `ejecutar_agente` (si aún no la tienes) | `pytest tests/test_a4_agente.py` |

Las pruebas que dependen de algo pendiente aparecen como **SKIP** (no como error).

## Sin red o sin cuota
`LLM_PROVIDER=fake EMB_PROVIDER=hash`. Las pruebas corren; las respuestas no son representativas.
