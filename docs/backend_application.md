# Backend and Application Architecture

## Purpose

AI Dungeon Master is a browser-based D&D-inspired game. The Backend is the application entry point and coordinates sessions, player actions, the Rules Service, AI Dungeon Master, and the deterministic Game Engine.

This document describes the implementation and integration work as of October 2026. The real LLM integration is wired into the local Backend branch but is not yet verified end-to-end with a successful provider response.

## Components and responsibilities

| Component | Responsibility |
| --- | --- |
| Frontend (`frontend/`) | Browser UI, session selection, player input, display of public state and narration |
| Backend (`backend/main.py`) | HTTP API, session lifecycle, state/history ownership, coordination and error responses |
| Turn orchestration (`integration/`) | Rules retrieval, DM invocation, response-to-command mapping, Engine execution |
| Rules Service (`rules_service/`) | Retrieve relevant rules from a local text/PDF source; keyword, RAG, or hybrid mode |
| AI Dungeon Master (`ai_dm/`) | Call an external LLM, generate narrative and structured state updates |
| Game Engine (`engine/`) | Validate and atomically apply structured commands; authoritative game rules for state changes |

The Frontend does not modify authoritative game state. The LLM proposes changes, while the Game Engine validates and applies them.

## Turn processing

```text
Player (Frontend)
    |
    | POST /sessions/{id}/turns
    v
Backend: load session, GameState and history
    |
    v
TurnOrchestrator
    |-- Validate action against current state/revision
    |-- Rules Service: retrieve relevant rule excerpts
    |-- AI Dungeon Master: narrative + structured state updates
    |-- Map DM updates to Engine commands
    `-- Game Engine: validate and apply commands atomically
    |
    v
Backend: persist accepted state and private history in memory
    |
    `-- Return accepted, narration, public state, revision,
        turn_number and structured errors to Frontend
```

If rules retrieval fails, the orchestrator can continue using a fallback rules summary and records a degradation flag. If the DM/provider fails, the turn is not committed; the Backend returns an error (currently HTTP 502 for `IntegrationError`). A rejected Engine action leaves the state and revision unchanged.

## Backend API

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/health` | Basic Backend health check; returns `{"status":"ok"}` |
| POST | `/sessions` | Create a named session and initial GameState |
| GET | `/sessions/{session_id}/state` | Return the public state projection |
| POST | `/sessions/{session_id}/actions` | Direct Engine action with explicit `health_delta` (development/test path) |
| POST | `/sessions/{session_id}/turns` | Natural-language turn through Rules Service, AI DM and Engine |

`POST /sessions` currently creates a hardcoded `Hero` character (10/10 HP, strength 2) at location `start`, revision 0, turn 0. There is no user authentication or character-creation endpoint yet.

The `/turns` request body is:

```json
{"character_id":"hero","text":"Я осматриваю комнату"}
```

The successful response includes `accepted`, `narration`, `state`, `revision`, `turn_number`, and `errors`. A missing session returns HTTP 404. The direct `/actions` endpoint is for exercising Engine behavior and does not use an LLM.

## State, history and privacy

- The Backend currently stores sessions, GameState and turn history in process-memory dictionaries. Restarting the server deletes them. The browser may still hold a cached session ID; a request for it then returns HTTP 404.
- `GameState` is the authoritative source for characters, HP, inventory, location, revision and turn number.
- The Engine increments revision and turn number once for an accepted action; rejected actions do not change them.
- The DM response can include private `dm_thoughts`; Backend history can retain them as `dm_notes`. These must never be included in public API projections or the Frontend log.
- The public projection is created with `to_public_dict(...)`, which exposes an allowlisted subset of state.
- There is no persistent database, cross-process transaction, public history endpoint or durable replay yet.

## Local development

Run from the repository root with the project Python environment activated:

```bash
python -m pip install -r requirements.txt
./run.sh
```

Local URLs:

- Frontend: http://127.0.0.1:5500
- Backend: http://127.0.0.1:8000
- OpenAPI: http://127.0.0.1:8000/docs

The launcher starts the Backend and a static Frontend HTTP server. Stop with `Ctrl+C`.

### Environment configuration

Use a local `.env` file (never commit real credentials):

```dotenv
OPENAI_API_KEY=hf_your_token_here
OPENAI_BASE_URL=https://router.huggingface.co/v1
MODEL_NAME=Qwen/Qwen3.8-27B
RULES_BOOK_PATH=data/srd.md
RULES_MODE=keyword
```

`RULES_BOOK_PATH` must point to an existing local rules file. `keyword` mode is suitable for lightweight development. RAG/hybrid modes require embeddings and additional dependencies/model downloads. The chosen LLM model and provider availability must be checked independently.

Python runtime dependencies include FastAPI, Uvicorn, Pydantic, HTTPX, OpenAI SDK, python-dotenv, pypdf, NumPy and sentence-transformers. Ensure the requirements file names the packages `httpx` (not `httpx2`), `openai` and `python-dotenv`.

## Verification status (October 2026)

Verified locally:

- Frontend can reach the Backend health endpoint and create sessions.
- Backend can return public session state.
- Direct Engine action can update HP and revision.
- Rules Service keyword lookup returns matching rule text.
- The real DM integration is invoked from the `/turns` path.
- Missing sessions return 404; provider failures return 502 rather than committing a turn.

Not yet verified:

- Successful real LLM-generated turn. A direct Hugging Face Router call returned **HTTP 402 (Payment Required)**, and the integrated request returned HTTP 502 (`ProviderError`). This is a provider/quota issue, not evidence of successful gameplay.
- Persistence across restarts, concurrent turn serialization, and public history synchronization.

## Known limitations and next steps

1. Improve startup/configuration checks and return actionable, sanitized provider errors without exposing tokens or private prompts.
2. Make Frontend gracefully handle stale session IDs after Backend restarts.
3. Add or extend integration tests using fake DM and Rules Service; these should run without external API credits.
4. Add durable session/history storage and concurrency-safe state updates.
5. Provide public history without exposing `dm_notes`; extend component health reporting when readiness checks are implemented.
6. Verify a complete real LLM turn when provider access is available.

Related documentation: `docs/implementation.md`, `docs/turn_orchestration.md`, `docs/rules_service.md`.
