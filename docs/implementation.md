# Current Implementation

This document describes the code that exists in commits `6311acb` and
`7419552` on `feature/frontend-engine`. It does not describe planned endpoints
or components as if they were already implemented.

The FastAPI prototype is currently on the separate
`origin/feature/backend-setup` branch. It is not part of
`feature/frontend-engine`.

## Frontend

### Purpose

The Frontend is a browser-only shell for trying the initial session,
character, chat, and public-state user flows. It uses HTML, CSS, and vanilla
JavaScript modules. It has no build step and contains no Game Engine business
logic.

The header explicitly identifies the displayed data as a local cache rather
than authoritative game state.

### File structure

- `frontend/index.html` defines the three-column page and its forms.
- `frontend/styles.css` provides the dark responsive layout. The three columns
  collapse into one column below 900 px.
- `frontend/js/api.js` contains the two implemented HTTP calls and fixes the
  Backend base URL at `http://127.0.0.1:8000`.
- `frontend/js/state.js` defines and normalizes the local public-state shape
  and stores it in `sessionStorage`.
- `frontend/js/app.js` owns DOM rendering and form event handlers.

### Implemented screen

The single page has three areas:

1. **Session and character controls**
   - checks Backend health;
   - retries the health check;
   - creates a session through the Backend;
   - accepts a session ID for an unverified local connection;
   - creates a browser-local character with name, concept, HP, arbitrary
     integer stats, and one optional starting item.
2. **Play**
   - selects a browser-local character;
   - records the player's text in the local log;
   - appends the fixed placeholder `Dungeon Master is not connected yet.`;
   - does not submit the action to a Backend or Engine.
3. **Public state**
   - displays session name and ID;
   - displays location;
   - displays character concept, HP, stats, and inventory.

### Frontend state

`frontend/js/state.js` uses the storage key
`ai-dungeon-master.public-cache`. The normalized cache shape is:

```text
session_id: string
session_name: string
location: string
characters:
  - id: string
  - name: string
  - concept: string
  - hp: number
  - max_hp: number
  - stats: object of numeric values
  - inventory:
      - item_id: string
      - name: string
      - quantity: number
log:
  - kind: "player" | "master"
  - character_id: string
  - text: string
```

Loading malformed JSON falls back to an empty cache. Normalization copies only
the listed fields. The cache is saved during every render.

The current character IDs use the browser-local format
`local-character-<number>`. A starting item's text is used as both its
`item_id` and display `name`. These are development conventions, not Backend
contracts.

### Current Backend API usage

`frontend/js/api.js` implements:

- `GET http://127.0.0.1:8000/health`
  - considered healthy only when the response is successful and exactly
    contains `status: "ok"`;
  - otherwise displays `Backend unavailable`.
- `POST http://127.0.0.1:8000/sessions`
  - sends `{ "name": string }`;
  - expects `{ "id": string, "name": string }`;
  - stores the returned values in the local public cache.

These shapes match the FastAPI prototype on
`origin/feature/backend-setup`. That prototype stores sessions in an in-memory
dictionary and creates IDs with `uuid4()`.

The prototype currently has no CORS middleware. Therefore, when the Frontend
is served from a different origin, a normal browser cannot call it even though
the request and response shapes match.

The “Use session id” form does not call the Backend. Its UI states that the ID
is only cached and has not been confirmed.

### Placeholders

- The Dungeon Master response is fixed UI text.
- Character creation is browser-local because there is no character endpoint.
- Joining by session ID is browser-local because there is no session lookup or
  join endpoint.
- Actions and the chat log stay in `sessionStorage`.
- The location remains empty/unknown unless a future Backend public-state
  response supplies it.

### Intentionally not implemented

- Backend action or turn requests;
- Engine execution in the browser;
- AI or generated narration;
- authentication or player identity;
- authoritative character creation;
- session lookup or membership;
- persistence beyond one browser tab/session;
- reconnect, synchronization, conflict handling, or multiplayer updates;
- frontend automated tests.

## Game Engine

### Purpose

The Engine is an importable Python package that applies a closed set of
structured commands to a `GameState`. It does not call an LLM, parse natural
language, use a network or database, load rules, or generate narration.

The public package exports:

```python
apply_action(state, action, commands, dice)
to_public_dict(state, session_name="", log=())
```

### File structure

- `engine/models.py` contains state, action, result, and error models.
- `engine/commands.py` contains command dataclasses and the closed command
  union.
- `engine/events.py` contains factual event dataclasses and the event union.
- `engine/dice.py` contains the injected randomness abstraction.
- `engine/core.py` validates and applies one action atomically.
- `engine/projection.py` builds the current Frontend public-state shape.
- `engine/__init__.py` exports `apply_action` and `to_public_dict`.
- `engine/tests/` contains `unittest` coverage for the Engine, dice, and
  projection.

No external dependency is used by the Engine.

### Models

All Engine models are frozen dataclasses.

`InventoryItem` contains:

- `item_id`;
- `name`;
- `quantity`.

`Character` contains:

- `id`;
- `name`;
- `concept`;
- `hp`;
- `max_hp`;
- `stats` as a string-to-integer mapping;
- `inventory` as a tuple of items.

`GameState` contains:

- `session_id`;
- `revision`;
- `turn_number`;
- `location_id`;
- a tuple of characters;
- `world_flags` with `bool`, `int`, or `str` values.

It does not contain prompts, rules, chat history, narration, timestamps, LLM
metadata, or an explicit hidden-plot field.

`PlayerAction` contains:

- `action_id`;
- `session_id`;
- `character_id`;
- natural-language `text`;
- `expected_revision`.

The Engine checks the text only for emptiness. It never interprets it.

`EngineError` contains a typed `code`, human-readable `message`, and optional
`command_index`. The currently declared codes are:

- `INVALID_CHARACTER`;
- `SESSION_MISMATCH`;
- `STALE_REVISION`;
- `EMPTY_ACTION`;
- `UNKNOWN_COMMAND`;
- `INVALID_TARGET`;
- `INVALID_STAT`;
- `INVALID_ITEM`;
- `INVALID_LOCATION`.

`EngineResult` contains:

- `accepted`;
- `state`;
- `events`;
- `errors`.

The runtime events are typed dataclasses, although the `events` annotation in
`EngineResult` is currently `tuple[object, ...]`.

### Commands

The closed MVP set is:

- `AdjustHealth(character_id, delta)`;
- `AddItem(character_id, item_id, quantity)`;
- `RemoveItem(character_id, item_id, quantity)`;
- `SetLocation(location_id)`;
- `AdjustStat(character_id, stat, delta)`;
- `SetWorldFlag(key, value)`;
- `SkillCheck(character_id, stat, difficulty)`.

The Python command models do not contain a transport discriminator such as
`type`. A future Backend must define how JSON/LLM output is mapped to these
dataclasses.

`AddItem` creates a new item's display name from `item_id`; there is currently
no separate item catalog or item-name command field.

### Events

Accepted commands produce factual events:

- `HealthChanged`;
- `ItemAdded`;
- `ItemRemoved`;
- `LocationChanged`;
- `StatChanged`;
- `WorldFlagChanged`;
- `SkillCheckResolved`.

Rejected actions produce `ActionRejected`. Events carry `action_id` and their
zero-based command `sequence`. Command-specific events include before/after or
resolution data as appropriate. They contain no narration.

Events do not currently carry `session_id`, resulting `revision`,
`turn_number`, timestamps, or persistent event IDs. A Backend can derive the
session and revision from the action and result, but the persisted event
envelope still needs an agreed contract.

### `apply_action(...)`

The implemented signature is:

```python
apply_action(
    state: GameState,
    action: PlayerAction,
    commands: tuple[object, ...] | list[object],
    dice: Dice,
) -> EngineResult
```

Execution has two passes:

1. Validate the action and preview every command against a newly derived
   state. Preview does not consume dice rolls.
2. If all validation succeeds, apply the commands again, emit events, and
   increment `revision` and `turn_number`.

One accepted `PlayerAction` is one turn. Both counters increase exactly once,
including when the command list is empty.

### Validation

Action validation checks:

- session equality;
- expected revision equality;
- non-empty text;
- membership of the acting character in the state's character tuple.

Command validation currently checks:

- supported command class;
- target character existence;
- integer health/stat deltas;
- item ID and positive quantity;
- sufficient quantity for removal;
- non-empty location;
- existence of the named stat;
- world-flag key and primitive value type;
- integer skill-check difficulty.

HP is clamped to `0..max_hp`. Adding and removing items and sequential command
dependencies are evaluated against the preview state.

The current Engine does not have catalogs of valid locations or item IDs,
stat bounds, flag-name allowlists, or difficulty bounds. Consequently,
“valid location” currently means “non-empty location ID”, and a new non-empty
item ID is accepted.

### Atomicity and immutability

If action or command validation fails:

- `accepted` is false;
- the exact input state object is returned;
- counters are unchanged;
- the only event is `ActionRejected`;
- structured errors identify the cause and, for command errors, its index.

The Engine uses `dataclasses.replace`, tuples, and copied mappings for its own
updates and does not mutate the supplied state during normal command
execution.

Frozen dataclasses do not deeply freeze `Mapping` instances supplied by a
caller. Callers must not mutate `stats` or `world_flags` dictionaries after
constructing a state.

An invalid or exhausted injected `RandomSource` currently raises
`RuntimeError` during the commit pass rather than returning `EngineResult`.
No state has been persisted by the Engine in that situation, but a Backend
must treat the operation as failed. This is an integration risk, not a
successful partial turn.

### Revision and turn number

- `revision` is an optimistic-concurrency version for the entire `GameState`.
- `expected_revision` must equal the loaded state's `revision`.
- `turn_number` counts accepted player actions.
- Rejected actions change neither field.
- The Engine does not provide cross-process locking. The Backend must combine
  load, Engine execution, and compare-and-swap persistence in a transaction.

### Dice

`RandomSource` is a protocol with inclusive `randint(low, high)`.

- `SequenceSource` supplies fixed values for tests and replay.
- `LocalRandomSource` owns a `random.Random` instance and does not call module
  level random functions.
- `Dice.roll()` defaults to a d20 and rejects dice with fewer than two sides.

`SkillCheck` rolls d20, adds the current stat value, and succeeds when
`roll + modifier >= difficulty`. It does not change `GameState`; it emits a
`SkillCheckResolved` event.

To make retries and replays deterministic, a future Backend must own and
persist the random seed, roll stream, or resolved events. Constructing a fresh
unseeded `LocalRandomSource` for every retry would not provide replay
determinism.

### Public projection

`to_public_dict(state, session_name="", log=())` publishes exactly:

- `session_id`;
- externally supplied `session_name`;
- `location`, mapped from `state.location_id`;
- character ID, name, concept, HP, max HP, stats, and inventory;
- externally supplied log entries reduced to `kind`, `character_id`, and
  `text`.

It intentionally omits:

- `revision`;
- `turn_number`;
- all `world_flags`;
- errors and Engine internals;
- narration except text explicitly supplied through `log`;
- unknown fields on supplied log entries.

Hidden campaign secrets are not part of `GameState` and must not be stored in
`world_flags`. The projection test uses a `hidden_plot`-named test flag only
to prove that all flags are omitted; it is not a supported storage convention.

Omitting `world_flags` prevents accidental exposure of internal world facts,
but a future product may need a separately allowlisted public-world
projection. Omitting `revision` is incompatible with a Frontend that must
submit `expected_revision` unless the Backend returns revision metadata
outside this projection.

### Unit tests

The current suite contains 14 `unittest` tests and covers:

- input state preservation and creation of a new accepted state;
- exactly one revision/turn increment;
- successful empty command lists;
- typed action and command errors;
- HP damage, healing, and clamping;
- item addition, removal, insufficient quantity, and zero-quantity removal;
- location, stat, and world-flag updates;
- deterministic successful and failed skill checks;
- command ordering within one turn;
- rollback when a later command is invalid;
- no random-source consumption during failed preview;
- fixed and exhausted dice sources;
- filtering of Engine-internal fields from the public projection.

There are no Backend integration tests or Frontend automated tests yet.

## Current Limitations

- The AI Dungeon Master and LLM command generation are not implemented.
- Backend orchestration of actions, commands, Engine execution, and narration
  is not implemented.
- The Backend prototype and these two commits are on separate branches.
- Session storage in the Backend prototype is process-local and non-durable.
- No GameState, character, event, action, or narration persistence exists.
- No character, join-session, get-session, public-state, or turn endpoint
  exists.
- The Backend prototype has no CORS configuration for the separate Frontend.
- Rules Service and rule ingestion are not implemented.
- RAG, chunking, and retrieval are not implemented.
- Narration is only a fixed Frontend placeholder.
- Combat is limited to generic HP adjustment and skill checks; there is no
  initiative, action economy, conditions, armor, or encounter model.
- There is no multiplayer identity, authorization, turn ownership, locking,
  push update, or reconnect protocol.
- There is no item/location/stat rules catalog.
- Command, event, error, and public-state JSON schemas are not versioned.
- Retry/idempotency behavior for `action_id` is not implemented by the
  Backend.
