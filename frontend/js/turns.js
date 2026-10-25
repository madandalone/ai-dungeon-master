import { normalizePublicState } from "./state.js";

function defaultActionId() {
  return globalThis.crypto.randomUUID();
}

export function createTurnAttempt(previous, state, action, createId = defaultActionId) {
  const sameAction = previous
    && previous.sessionId === state.session_id
    && previous.characterId === action.characterId
    && previous.text === action.text;
  if (sameAction) return previous;

  return {
    sessionId: state.session_id,
    actionId: createId(),
    expectedRevision: state.revision,
    characterId: action.characterId,
    text: action.text,
  };
}

export function mergeServerState(previous, snapshot, metadata = {}) {
  const normalized = normalizePublicState({
    ...snapshot,
    connection: "server",
    revision: metadata.revision ?? snapshot.revision,
    turn_number: metadata.turn_number ?? snapshot.turn_number,
  });
  return {
    ...normalized,
    log: previous?.log?.length ? previous.log : normalized.log,
  };
}

export function applyTurnResult(previous, action, result) {
  const state = mergeServerState(previous, result.state, result);
  if (!result.accepted) return state;
  const additions = [{ kind: "player", character_id: action.characterId, text: action.text }];
  if (result.narration?.trim()) additions.push({ kind: "master", character_id: "", text: result.narration.trim() });
  return { ...state, log: [...(previous?.log || []), ...additions] };
}

export function stateChanges(before, after) {
  const beforeCharacters = new Map((before?.characters || []).map((character) => [character.id, character]));
  const characters = {};
  (after?.characters || []).forEach((character) => {
    const previous = beforeCharacters.get(character.id);
    if (!previous) return;
    const beforeInventory = JSON.stringify(previous.inventory || []);
    const afterInventory = JSON.stringify(character.inventory || []);
    if (previous.hp !== character.hp || beforeInventory !== afterInventory) {
      characters[character.id] = {
        hp: previous.hp !== character.hp,
        inventory: beforeInventory !== afterInventory,
      };
    }
  });
  return { location: Boolean(before && before.location !== after?.location), characters };
}

export function failureMessageKey(error) {
  const code = error?.code || error?.errors?.[0]?.code || "unknown";
  const keys = {
    network_error: "game.error.network",
    not_found: "game.error.notFound",
    stale_revision: "game.error.staleRevision",
    master_unavailable: "game.error.masterUnavailable",
    invalid_response: "game.error.invalidResponse",
    INVALID_CHARACTER: "game.error.invalidCharacter",
    EMPTY_ACTION: "game.error.emptyAction",
    STALE_REVISION: "game.error.staleRevision",
    INVALID_ITEM: "game.error.invalidItem",
  };
  return keys[code] || "game.error.generic";
}

export function warningMessageKey(warnings = []) {
  const code = warnings.find((warning) => warning?.code)?.code;
  if (code === "RULES_UNAVAILABLE") return "game.warning.rulesUnavailable";
  return code ? "game.warning.generic" : null;
}

export function pinnedContext(state, selectedCharacterId = "") {
  const characters = state?.characters || [];
  const hero = characters.find((character) => character.id === selectedCharacterId) || characters[0] || null;
  const log = state?.log || [];
  return {
    sessionName: state?.session_name || "",
    hero,
    lastEvent: log[log.length - 1] || null,
  };
}
