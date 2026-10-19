# Current Implementation

This document describes the current Frontend and Game Engine contracts. It
records implemented behavior separately from work that still belongs to the
Backend, AI Dungeon Master, and Rules Service owners.

## Frontend

### Purpose

The Frontend is a responsive browser client for server sessions, live turns,
the adventure log, and a projected world summary. It uses HTML, CSS, and
vanilla JavaScript modules, has no build step, and contains no Game Engine
business logic.

The visual language is an original colour pixel-fantasy theme. It uses crisp
CSS and inline SVG shapes; no game-specific third-party artwork is bundled.

### Navigation and language

The application is split into four sections: **Game**, **Heroes**, **World**,
and **Session**. A desktop sidebar can collapse to icons. Below 1100 px it
becomes an overlay drawer so a narrow desktop window does not squeeze game
cards. The active section and collapsed state are saved as UI preferences.

All static and runtime UI messages are available in Russian and English through
`frontend/js/i18n.js`. Russian is the default. The language selector persists
its choice and updates the document language and accessible labels.

### Frontend state

`frontend/js/state.js` stores a versioned envelope under
`ai-dungeon-master.session-cache.v2`:

```text
version: 2
activeSessionId: string | null
sessions:
  <session-id>:
    connection: "server" | "unverified" | "legacy"
    session_id: string
    session_name: string
    revision: integer
    turn_number: integer
    location: string
    characters: Character[]
    log: LogEntry[]
```

Each session has independent heroes, world state, and log entries. The previous
`ai-dungeon-master.public-cache` value is migrated once. Existing data without
a session ID remains visible as the read-only `legacy-local` session.

Loading malformed data falls back to an empty store. Normalization copies only
allowlisted public fields. A fresh server snapshot replaces HP, inventory, and
location while preserving the tab-local public chronicle until Backend exposes
public history.

### Implemented experience

- **Game** submits a live turn, shows player and Dungeon Master messages, and
  refreshes the active hero HUD from the returned server state.
- **Heroes** renders server characters with an automatically selected
  archetype icon, accessible HP progress, stats, and inventory. The creation
  form is visibly disabled until Backend provides a character endpoint.
- **World** summarizes the current location, session, party, and log size.
- **Session** checks Backend health, creates a session, validates a session ID
  through Backend, and switches between cached server snapshots.

A compact sticky HUD keeps the active session, selected hero with current HP,
and latest public event visible while any section is scrolled. Long names and
events are truncated visually but retain the full event in the element title.
On narrow screens the latest event moves to a second row.

When the Backend is unavailable, the application enters an explicit read-only
mode: saved data and navigation remain available, while mutation forms are
disabled with an actionable explanation.

### Current Backend API usage

The API base follows the Frontend hostname on port 8000. The client implements:

- `GET /health`, healthy only for a successful `{ "status": "ok" }` response;
- `POST /sessions` with `{ "name": string }`, expecting string `id` and `name`;
- `GET /sessions/{id}/state`, expecting the public Engine projection;
- `POST /sessions/{id}/turns` with `character_id` and `text`, expecting
  acceptance, public narration, current state, revision, turn number, and
  structured errors. A structured `warnings` array is optional for backward
  compatibility and defaults to an empty list.

Network, missing-session, DM-unavailable, and malformed-response failures are
kept separate. Failed turns retain the action text and do not append a false
chronicle entry. Raw Backend error messages are not shown to the player.

Browser use still depends on Backend CORS configuration. The current turn
branch allows port 5500; the team must also allow the documented development
origin on port 4173.

### Tests and intentional limits

`npm run test:frontend` uses Node's built-in test runner and covers translation
parity, session migration and isolation, API response validation, turn state
merging, safe failure messages, archetype selection, and HP thresholds.

The Frontend does not implement authoritative character creation,
authentication, cross-device persistence, or multiplayer updates. Public
history is tab-local because Backend history currently includes private DM
notes and has no separate public endpoint.

### Integration-ready UI contract

The Session screen contains a module-readiness panel for Backend, AI Master,
Rules Service, and public history. It never guesses that a component is ready.
The current `{ "status": "ok" }` health response marks only Backend as online
and leaves the other modules as `unknown`.

Backend can activate the remaining indicators without a Frontend layout change
by extending `GET /health`:

```json
{
  "status": "ok",
  "components": {
    "dm": "online",
    "rules": "degraded",
    "history": "online"
  }
}
```

Allowed component values are `online`, `degraded`, and `offline`. Missing or
unknown values are rendered as “No data”. Internal diagnostics must not be
included in this public response.

Turn responses may include non-fatal warnings:

```json
{
  "accepted": true,
  "warnings": [{ "code": "RULES_UNAVAILABLE" }]
}
```

`RULES_UNAVAILABLE` keeps the accepted turn, shows a yellow in-game notice, and
marks Rules Service as degraded. Warning messages from Backend are deliberately
ignored so provider details cannot leak into the UI. Errors remain rejection
signals and use the red frame.

Future character creation should add a dedicated API client function and only
then enable the existing form. Future public history should populate the
allowlisted `log` entries in the public state or expose a separate public
endpoint; it must never reuse history objects containing `dm_notes`.

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
- safe `revision` and `turn_number` counters;
- `location`, mapped from `state.location_id`;
- character ID, name, concept, HP, max HP, stats, and inventory;
- externally supplied log entries reduced to `kind`, `character_id`, and
  `text`.

It intentionally omits:

- all `world_flags`;
- errors and Engine internals;
- narration except text explicitly supplied through `log`;
- unknown fields on supplied log entries.

Hidden campaign secrets are not part of `GameState` and must not be stored in
`world_flags`. The projection test uses a `hidden_plot`-named test flag only
to prove that all flags are omitted; it is not a supported storage convention.

Omitting `world_flags` prevents accidental exposure of internal world facts,
but a future product may need a separately allowlisted public-world
projection. Revision and turn number are safe synchronization metadata and do
not reveal campaign content.

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

Frontend tests use Node's built-in test runner. Backend and integration suites
cover the current turn orchestration separately.

## Team Follow-up

Backend ownership:

- merge the turn endpoint and allow the Frontend development origins on ports
  4173 and 5500 through CORS;
- extend `GET /health` with the public `components.dm`, `components.rules`, and
  `components.history` statuses described above;
- build the real orchestrator once at application startup instead of creating
  fake DM and rules providers per request;
- pass a sanitized public chronicle into `to_public_dict(..., log=...)`; every
  entry must contain only `kind`, `character_id`, and `text`;
- add `POST /sessions/{id}/characters` for the existing form and return the
  updated public state after creation;
- return stable public `errors` and `warnings` without raw provider messages.

Rules and integration ownership:

- wire `build_orchestrator()` into Backend after the rules-wiring branch is
  merged;
- surface a rules outage only as a safe `RULES_UNAVAILABLE` warning;
- keep the turn accepted when Rules Service is optional and unavailable, so
  the Frontend can render the yellow limited-mode notice;
- keep retrieved rule text, embeddings, and internal logs outside the public
  response.

AI Dungeon Master ownership:

- keep public narration in Russian and preserve existing character and item
  names;
- keep `dm_thoughts` private and return a validated `TurnResponse`;
- verify consecutive turns with history, HP, inventory, and location changes.

## Current Limitations

- Backend session state and private turn history are process-local and
  disappear on restart.
- Backend still uses fake DM and rules providers until the integration factory
  is wired in.
- The current CORS list includes port 5500 but not the documented port 4173.
- There is no authoritative character-creation endpoint.
- Public turn history is not available from Backend, so the browser chronicle
  survives only in the current tab.
- Combat is limited to generic HP adjustment and skill checks; there is no
  initiative, action economy, conditions, armor, or encounter model.
- There is no multiplayer identity, authorization, turn ownership, locking,
  push update, or reconnect protocol.
- There is no item/location/stat rules catalog.
- Command, event, error, and public-state JSON schemas are not versioned.
- Retry/idempotency behavior for `action_id` is not implemented by the
  Backend.
