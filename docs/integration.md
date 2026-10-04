# Component Integration

This document defines the intended integration boundary around the code that
currently exists on `feature/frontend-engine`. It identifies contracts that
must be agreed before connecting the components. Proposed endpoints and JSON
examples below are not implemented.

The current FastAPI prototype is on `origin/feature/backend-setup`, not on the
current branch. It implements only `GET /health` and `POST /sessions`.

## Target flow

```text
Player
→ Frontend
→ Backend
→ AI Dungeon Master
→ GameCommand[]
→ Game Engine
→ GameEvent[] + GameState
→ Backend persistence
→ narration/public state
→ Frontend
```

### Player to Frontend

- The player selects a character and enters natural-language action text.
- The Frontend creates a transport request containing the session, character,
  text, last observed revision, and preferably a client-generated idempotency
  key/action ID.
- The Frontend must not interpret the action into game mechanics.
- The browser cache is not canonical state.

### Frontend to Backend

- The Frontend calls a future turn endpoint with a player action DTO.
- The Backend authenticates the player, verifies session membership and turn
  permission, and loads the canonical `GameState`.
- Transport/schema validation belongs at this boundary.
- The Frontend must not send `GameCommand[]` as authoritative commands.

### Backend to AI Dungeon Master

- The Backend builds AI context from the `PlayerAction`, relevant public and
  private campaign context, and retrieved rules.
- The Backend controls prompt construction and LLM provider calls.
- The Engine is not imported by the AI layer for state mutation.
- The AI receives enough state to interpret the action but is not the source
  of truth for that state.

### AI Dungeon Master to Backend

- The AI returns a closed, structured `GameCommand[]` plus narration or
  narration inputs according to a separate response schema.
- The Backend validates the JSON shape and maps command DTOs to the Engine's
  Python command dataclasses.
- LLM output is untrusted. A syntactically valid command may still be rejected
  by the Engine.
- The AI must not claim that state changed before the Engine accepts the
  commands.

### Backend to Game Engine

- The Backend calls `apply_action(state, action, commands, dice)`.
- `state` is the canonical state loaded for the expected revision.
- `PlayerAction` is passed for identity, audit correlation, non-empty text,
  session validation, and optimistic concurrency. The Engine does not parse
  its meaning.
- The command list is typed and ordered.
- The Backend supplies a controlled `Dice`; no global randomness is used.

### Game Engine to Backend

- The Engine returns `EngineResult`.
- On success, `EngineResult.state` is the only accepted next `GameState`;
  events are factual consequences in command order.
- On rejection, the original state is returned with structured errors and an
  `ActionRejected` event.
- The Engine must not return narration, prompts, retrieved text, or campaign
  secrets.

### Backend persistence

- The Backend persists the new state, action, factual events, random/replay
  data, and narration as appropriate.
- Persistence must use compare-and-swap on `revision` or an equivalent
  transaction. Engine validation alone cannot prevent two processes from
  saving competing revision `N + 1` states.
- The Backend must not save a rejected Engine state as a successful turn.
- The Backend should make `action_id` idempotent so a network retry does not
  consume a second turn or produce a second dice roll.

### Backend to Frontend

- The Backend returns an allowlisted public state, narration/chat data, factual
  events if the UI needs them, and concurrency metadata.
- It must never serialize the canonical state directly without a projection.
- Campaign secrets, prompts, retrieved private context, and internal flags
  stay server-side.

## Current domain interfaces

### `PlayerAction`

The Engine model requires:

```text
action_id: string
session_id: string
character_id: string
text: string
expected_revision: integer
```

The Backend should construct this object after transport validation. A
client-generated `action_id` is preferable for retry idempotency; alternatively
the Backend may generate it before any AI or Engine call and return it to the
client. It must remain stable across retries.

### `GameState`

The Engine's canonical mutation input contains:

```text
session_id
revision
turn_number
location_id
characters[]
world_flags
```

The Backend owns loading and durable storage. The Engine owns valid
transitions from one loaded state to the next. “Engine is the source of truth”
means application code must not independently apply command effects in SQL,
Frontend state, or AI output.

The frozen dataclasses are usable as an in-process domain model, but they are
not a database schema or JSON transport schema. A persistence adapter must
convert:

- character and inventory tuples to stored collections;
- `stats` and `world_flags` mappings to their chosen database representation;
- stored primitive values back to the declared types;
- records to a fully validated state before invoking the Engine.

### `GameCommand`

The Python union currently supports:

```text
AdjustHealth(character_id, delta)
AddItem(character_id, item_id, quantity)
RemoveItem(character_id, item_id, quantity)
SetLocation(location_id)
AdjustStat(character_id, stat, delta)
SetWorldFlag(key, value)
SkillCheck(character_id, stat, difficulty)
```

The dataclasses have no JSON discriminator. The transport contract should use
an explicit stable field such as `type`, for example:

```json
{
  "type": "adjust_health",
  "character_id": "character-1",
  "delta": -3
}
```

The Backend/AI schema must reject unknown fields and unsupported command
types before mapping. The Engine remains responsible for semantic checks:
target existence, available inventory, known stats, HP bounds, and any future
domain catalogs or limits.

For the current MVP the AI can propose all seven commands, using these
required fields:

- `adjust_health`: `character_id`, integer `delta`;
- `add_item`: `character_id`, non-empty `item_id`, positive integer
  `quantity`;
- `remove_item`: `character_id`, non-empty `item_id`, positive integer
  `quantity`;
- `set_location`: non-empty `location_id`;
- `adjust_stat`: `character_id`, existing `stat`, integer `delta`;
- `set_world_flag`: non-empty `key`, primitive `value`;
- `skill_check`: `character_id`, existing `stat`, integer `difficulty`.

The LLM must not be trusted to enforce inventory quantities, valid targets,
state revision, or stat existence. Those checks remain in the Engine.

### `GameEvent`

Events are typed factual outcomes. They are suitable as in-process return
values but do not yet define a versioned JSON envelope. Before persistence or
public exposure, agree on:

- a stable event `type` discriminator;
- whether events are public, private, or separately projected;
- session, resulting revision, and turn metadata;
- persistent event ID and ordering;
- whether `action_id + sequence` is sufficient as a uniqueness key.

Events contain no narration. `ActionRejected` duplicates the principal error
code from `EngineResult.errors` so consumers can treat rejection as a factual
outcome; detailed error information remains in `errors`.

### `EngineResult`

`EngineResult` separates:

- acceptance status;
- accepted or unchanged state;
- factual events;
- structured errors.

Its `events` field is currently annotated as objects rather than the
`GameEvent` union. This does not change runtime behavior, but should be
resolved before relying on static schema generation.

## Proposed `POST /turns` contract

The endpoint does not exist yet. A minimal transport contract is:

```text
POST /turns
Content-Type: application/json
```

Request:

```json
{
  "action_id": "client-stable-id",
  "session_id": "session-uuid",
  "character_id": "character-id",
  "text": "I inspect the locked gate.",
  "expected_revision": 7
}
```

Successful response:

```json
{
  "action_id": "client-stable-id",
  "accepted": true,
  "revision": 8,
  "turn_number": 5,
  "public_state": {
    "session_id": "session-uuid",
    "session_name": "Harbor Watch",
    "location": "gate",
    "characters": [],
    "log": []
  },
  "events": [],
  "narration": "..."
}
```

Rejected semantic response:

```json
{
  "action_id": "client-stable-id",
  "accepted": false,
  "revision": 7,
  "public_state": {},
  "events": [
    {
      "type": "action_rejected",
      "code": "STALE_REVISION"
    }
  ],
  "narration": null,
  "errors": [
    {
      "code": "STALE_REVISION",
      "message": "Action revision does not match the game state.",
      "command_index": null
    }
  ]
}
```

Recommended status behavior:

- `200` for an accepted turn;
- `409` for `STALE_REVISION`, including the latest revision/public snapshot or
  a clear instruction to refetch;
- `422` for malformed transport input;
- a documented 4xx response for authorization or session membership failure;
- a 5xx response with no committed state if LLM, dice infrastructure, or
  persistence fails.

The Backend should not expose raw LLM commands as accepted facts. Returning
projected events is optional for the first UI, but persistence should retain
the factual result needed for audit/replay.

## Session integration

The existing `POST /sessions` prototype:

- accepts `{ "name": string }`;
- creates a UUID string in the Backend;
- returns `{ "id": string, "name": string }`;
- stores only the session model in process memory.

This ID format is compatible with Engine `session_id: str` and Frontend
`session_id: string`.

Creating a session should ultimately also initialize and persist a
`GameState` at a documented starting `revision` and `turn_number`. The current
prototype does not do so. The Backend branch must be merged or rebased with
`feature/frontend-engine`; until then there is no branch containing both API
and Engine.

## Character integration

The current ownership is incomplete:

- Frontend creates a browser-local character with a local ID.
- Backend has no character model or endpoint.
- Engine has the canonical in-game `Character` domain model.

The Backend should provide a character creation/join workflow, assign stable
IDs, validate initial HP/stats/items, and create the Engine-domain character
through an adapter or initialization service. The Frontend must replace its
local character with the Backend response before submitting a turn.

Do not create a second Backend business model that can evolve independently
and directly mutate game mechanics. Backend request/response schemas may
differ from Engine dataclasses, but conversion must be explicit and tested.

## Revision and concurrent turns

The Frontend must send the revision it last received as
`expected_revision`. The Backend then:

1. loads session state at revision `N`;
2. ensures request `expected_revision == N`;
3. invokes AI and Engine;
4. atomically writes the returned state only if the stored revision is still
   `N`;
5. retries orchestration or returns `STALE_REVISION` if another transaction
   won.

Checking only inside the Engine is insufficient because storage can change
between load and save.

The current `to_public_dict` and Frontend normalizer omit revision. Therefore,
the Backend must return revision in an API envelope and the Frontend must
retain it before turn submission. This is an integration conflict to resolve
before implementing `POST /turns`; it does not require exposing other Engine
internals.

## Persistence boundary

Persist as canonical gameplay data:

- `session_id`;
- `revision`;
- `turn_number`;
- `location_id`;
- characters, HP, max HP, stats, and inventory;
- world flags;
- accepted `action_id` for idempotency;
- factual Engine events and their ordering if event audit/replay is required;
- controlled randomness information needed for reproducibility.

Persist outside `GameState`:

- session display name and membership;
- player/user identity and authorization;
- raw player action history;
- narration and chat messages;
- hidden campaign plot and secrets;
- prompts, provider metadata, and optional AI traces;
- rule books, chunks, embeddings, and retrieval metadata.

Frontend `sessionStorage` is a disposable projection cache and must never be
used to rebuild canonical state.

## AI Dungeon Master integration

The current Engine has no dependency on AI:

- it does not call an LLM;
- it does not import provider libraries;
- it does not know prompts;
- it does not generate or store narration;
- it does not retrieve rules;
- it does not interpret `PlayerAction.text`.

The intended AI flow is:

```text
Frontend request
→ Backend creates PlayerAction
→ Backend builds AI context
→ LLM returns typed command DTOs and narration
→ Backend maps DTOs to GameCommand[]
→ Engine validates and applies commands
```

Narration should be finalized only after the Engine result is known. If the
Engine rejects the proposed commands, the Backend must not return narration
that asserts those changes occurred. Options include asking the AI to narrate
from accepted events, generating narration in a second call, or constraining
the first response and repairing it after rejection. That orchestration choice
is outside the Engine.

Provider changes are isolated as long as every provider adapter produces the
same versioned command DTOs.

## Rules Service and RAG integration

### Rules Service / RAG responsibilities

- store and process the rule book;
- extract and chunk text;
- index and retrieve relevant rules;
- return text or structured rule context to the Backend/AI layer.

### AI Dungeon Master responsibilities

- interpret player actions;
- use retrieved rule context;
- propose typed `GameCommand[]`;
- generate narration consistent with accepted outcomes.

### Game Engine responsibilities

- validate commands against the current state and implemented mechanics;
- use injected dice;
- apply accepted mechanics;
- return factual `GameEvent[]` and a new `GameState`.

Rules Service and RAG must not write `GameState` or bypass the Engine.
Retrieved rule text must not be passed into `apply_action`. The Engine must not
know which rule chunk, retrieval strategy, embedding model, or LLM provider
influenced a command.

There is currently no Engine field, API call, import, or dependency that
couples it to Rules Service or RAG. The open design question is how a new
retrieved rule becomes an enforceable mechanic. Retrieval alone cannot make
the Engine understand a new rule: the mechanic still requires a versioned
command/state change in Engine code or a deliberately designed structured
rules interpreter in a later phase.

## Potential integration conflicts

Classification:

- **SAFE** — the current boundary is compatible.
- **WARNING** — agree on the contract before implementation.
- **CONFLICT** — current components cannot complete the next integration
  without a small code or branch change.

| Area | Current implementation | Future component | Potential conflict | Recommendation |
| --- | --- | --- | --- | --- |
| `GameState` | Frozen Engine dataclass with mappings and tuples | Backend persistence | **WARNING:** no serializer, schema version, construction validation, or deep immutability | Keep it as the domain model; add tested persistence adapters and state schema versioning |
| `Character` | Engine model plus a similar browser-local object | Backend character API | **CONFLICT:** no authoritative character endpoint/ID; local IDs cannot safely enter Engine state | Backend assigns stable IDs and maps creation DTOs to the Engine model |
| `PlayerAction` | Engine requires `action_id` and `expected_revision`; Frontend sends no turn request | Backend/Frontend | **CONFLICT:** current Frontend cannot construct the complete turn flow | Agree on `POST /turns`, stable action IDs, revision metadata, and idempotency |
| `GameCommand` | Closed Python union without transport discriminator | AI/Backend | **WARNING:** LLM JSON cannot be mapped unambiguously by an agreed schema yet | Define versioned tagged DTOs and strict Backend parsing |
| `GameEvent` | Typed Python objects with action ID and sequence | Backend persistence/API | **WARNING:** no event envelope, type tag, session/revision, or visibility policy | Define persisted and public event DTOs separately |
| `session_id` | Backend returns UUID string; Engine and Frontend use strings | All components | **SAFE:** representations are compatible | Preserve opaque string semantics and validate session membership in Backend |
| Backend branch | FastAPI prototype is on a divergent feature branch | Backend/Engine integration | **CONFLICT:** no branch currently contains Backend and Engine together | Merge/rebase deliberately; remove tracked bytecode from Backend branch during integration |
| CORS | Frontend uses port 8000; Backend prototype has no CORS middleware | Browser/Backend | **CONFLICT:** separate-origin browser requests are blocked | Configure explicit development/production allowed origins in Backend |
| `revision` | Engine enforces it; projection and Frontend cache omit it | Turn API/concurrency | **CONFLICT:** Frontend cannot send `expected_revision` | Return revision as response metadata and retain it in Frontend state |
| `location` | Engine uses `location_id`; public state uses `location` | Backend/API | **WARNING:** naming differs and only non-empty IDs are validated | Keep an explicit projection mapping and define a location catalog later |
| `inventory` | Engine uses tuples of `InventoryItem`; Frontend uses arrays; new item name equals ID | Backend/item catalog | **WARNING:** serialization is straightforward, but ID/name semantics are underspecified | Define item DTO/catalog and keep display name separate from stable ID |
| `stats` | Arbitrary integer mapping; Frontend lets users invent names | Rules/character creation | **WARNING:** AI and Engine need a shared allowed-stat vocabulary | Backend validates initial stats; Engine/rules version defines allowed stat IDs |
| `world_flags` | Arbitrary keys with primitive values; entirely hidden from public projection | AI/Backend/rules | **WARNING:** no key namespace, visibility, or semantic ownership | Namespace/allowlist flags and define private versus public derived facts |
| Public projection | Publishes session, location, characters, supplied log | Backend/Frontend | **CONFLICT:** missing revision blocks optimistic turn submission | Add revision to the API envelope; do not expose canonical state wholesale |
| Narration | Fixed Frontend placeholder; absent from Engine | AI/Backend | **SAFE:** separation is correct | Persist and return narration outside Engine after accepted events are known |
| Rules | Absent from Engine and API | Rules Service | **SAFE:** no coupling exists | Pass retrieved context only to AI orchestration |
| Hidden plot | No dedicated Engine field; all flags are hidden | Backend/AI | **WARNING:** arbitrary `world_flags` could be misused for secrets | Store campaign secrets separately and prohibit hidden plot in Engine flags |
| Persistence | Backend sessions are in memory; no state/event storage | Backend/database | **CONFLICT:** canonical state and revision cannot survive or coordinate processes | Add transactional state persistence with compare-and-swap before multiplayer |
| Dice/retry | Random source is injected; unseeded local source is available | Backend orchestration | **WARNING:** retry can produce a different roll; source failures raise exceptions | Persist action id plus seed/roll result and handle random-source failure atomically |
| Domain validation | Non-empty locations/items are accepted; no catalogs or bounds for most values | Rules/Backend/AI | **WARNING:** structured does not yet mean rules-valid | Add minimal catalogs/constraints when mechanics are agreed; never trust LLM validation |
| Error transport | Typed Engine errors, no HTTP mapping | Backend API | **WARNING:** clients need stable status and error DTO conventions | Version error codes and map stale revision to 409 |

## Model duplication

Three representations already exist or are likely:

1. Engine dataclasses represent canonical in-process gameplay state.
2. Frontend JavaScript objects represent a public projection/cache.
3. Future Backend schemas will represent HTTP and persistence data.

These should not be forced into one implementation type:

- Engine models should remain domain-internal and independent of FastAPI or a
  database ORM.
- Backend transport models should validate untrusted JSON and map explicitly
  to/from Engine models.
- Frontend models should contain only public fields and API metadata needed by
  the UI.

What should be shared is the contract, not necessarily the source class.
Before adding more endpoints, the team should define versioned schemas for:

- player action request;
- command DTOs;
- event DTOs;
- structured errors;
- public game state;
- character creation and response;
- turn response envelope.

A future `contracts` or `shared` layer may hold Python transport DTOs, JSON
Schema/OpenAPI definitions, and discriminator constants. It should not make
the Engine import FastAPI/Pydantic or make the Frontend mirror private
`GameState`.

The highest duplication risk is `Character`: the Frontend currently creates a
shape close to the Engine model, while Backend has no model. Treat the current
Frontend character as a temporary draft DTO, not a third source of truth.

## Public projection decision

The exact current function is:

```python
to_public_dict(state, session_name="", log=())
```

It publishes:

- `session_id`;
- supplied `session_name`;
- `location`;
- public character fields;
- supplied log entries reduced to kind, character ID, and text.

It hides:

- `revision` and `turn_number`;
- `world_flags`;
- Engine errors and event internals;
- unknown log-entry fields;
- any campaign secret because secrets are not part of `GameState`.

`world_flags` are hidden because their names and visibility have not been
classified and they may contain internal puzzle/world facts. If the UI later
needs weather, quest markers, or other facts, create an allowlisted public
projection rather than exposing all flags.

`revision` is not secret, and the Frontend needs it for concurrency. The
minimal integration fix is to include it in the Backend turn/public-state
response envelope. It does not need to become a visible UI field or expose the
rest of `GameState`.

`turn_number` may also be useful UI metadata but is not required to construct
the first turn request. Decide separately whether it belongs in the API
envelope.

`session_name` and `log` are parameters because they are Backend/application
data, not canonical Engine state. This is compatible with the responsibility
boundary, provided the Backend supplies authoritative values.

## Versioning and independent evolution

Backend, AI, and Rules/RAG can evolve independently if they depend on
versioned transport contracts rather than concrete provider or persistence
implementations.

Minimum compatibility rules:

1. Add an API/contract version before external clients depend on turn DTOs.
2. Every command and event DTO has a stable `type` discriminator.
3. Adding an optional response field is backward compatible; removing,
   renaming, or changing a field's meaning requires a version change.
4. New command types are not sent until the receiving Backend and Engine
   advertise/support the corresponding contract version.
5. Unknown command types are rejected, never silently ignored.
6. Events may gain optional fields, but existing event meanings remain stable.
7. `GameState` persistence has its own schema version and migration path.
8. New private state fields are not automatically added to public projection.
9. New rules or RAG data can change AI context without changing Engine, but
   any new enforceable mechanic requires an Engine command/state/event update.
10. LLM provider adapters may change freely while producing the same command
    DTO and narration contracts.
11. Accepted `action_id` is idempotent across Backend retries.
12. Dice/replay policy is versioned with the mechanic that consumes it.

# Integration Readiness

### Ready

- Backend can import the Engine without LLM, network, database, or Rules
  dependencies.
- Backend can construct `GameState`, `PlayerAction`, commands, and injected
  dice and call `apply_action`.
- Existing session UUID strings fit Engine and Frontend session fields.
- AI can be developed behind a command-generation adapter without changing
  Engine dependencies.
- Rules Service and RAG can retrieve context for AI without importing or
  modifying the Engine.
- Factual events and public projection provide a usable starting point for
  orchestration and UI responses.

### Requires Contract

- tagged JSON schemas for commands, events, errors, and the turn envelope;
- authoritative character creation, IDs, and initial values;
- revision metadata in API responses and compare-and-swap persistence;
- `action_id` ownership and retry/idempotency semantics;
- event persistence envelope and visibility;
- item, location, stat, and world-flag identifiers and constraints;
- dice seed/roll persistence and failure policy;
- narration timing relative to Engine acceptance;
- which world facts are public.

### Future Changes

- merge/rebase Backend and Engine work;
- configure Backend CORS;
- add durable session and `GameState` persistence;
- add character and turn endpoints;
- retain revision in the Frontend API state;
- add strict Backend command DTO parsing;
- add state serialization/migrations;
- add allowlisted public world facts as product requirements emerge;
- extend commands/events only when new mechanics are implemented.

### Risks

- Missing revision in the Frontend contract currently blocks safe turn
  submission.
- Local Frontend characters can be mistaken for authoritative characters.
- Unversioned Python dataclasses are not sufficient as cross-component JSON
  contracts.
- Arbitrary flag/stat/item/location identifiers can let malformed LLM output
  pass shallow validation.
- Unseeded randomness and absent idempotency can make retries produce different
  results.
- Injected dice failures currently raise rather than produce structured
  `EngineResult`.
- Mutable mapping instances inside frozen dataclasses can be changed by
  callers outside Engine control.
- Storing hidden plot in `world_flags` would violate the boundary even though
  the public projection currently hides it.

There are integration **CONFLICT** items to resolve before an end-to-end turn:
branch integration, CORS, authoritative character creation, revision transport,
turn submission, and durable transactional persistence. None requires
rewriting the Engine architecture now; they require small component-specific
changes and agreed contracts before Backend integration proceeds.
