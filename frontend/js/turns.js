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

function receiptFromEvent(event) {
  if (event.type === "health_changed") {
    return {
      type: event.type,
      characterId: event.character_id,
      delta: event.delta,
      value: event.new_hp,
    };
  }
  if (event.type === "item_added" || event.type === "item_removed") {
    return {
      type: event.type,
      characterId: event.character_id,
      item: event.item_id,
      quantity: event.quantity,
    };
  }
  if (event.type === "location_changed") {
    return { type: event.type, value: event.location };
  }
  if (event.type === "stat_changed") {
    return {
      type: event.type,
      characterId: event.character_id,
      stat: event.stat,
      delta: event.delta,
      value: event.new_value,
    };
  }
  if (event.type === "skill_check_resolved") {
    return {
      type: event.type,
      characterId: event.character_id,
      stat: event.stat,
      total: event.total,
      difficulty: event.difficulty,
      success: event.success,
    };
  }
  return null;
}

function inventoryQuantities(character) {
  return new Map((character?.inventory || []).map((item) => [
    item.item_id,
    { name: item.name || item.item_id, quantity: item.quantity },
  ]));
}

function snapshotReceipts(before, after) {
  if (!before || !after) return [];
  const receipts = [];
  const beforeCharacters = new Map((before.characters || []).map((character) => [character.id, character]));

  (after.characters || []).forEach((character) => {
    const previous = beforeCharacters.get(character.id);
    if (!previous) return;

    const hpDelta = character.hp - previous.hp;
    if (hpDelta !== 0) {
      receipts.push({
        type: "health_changed",
        characterId: character.id,
        delta: hpDelta,
        value: character.hp,
      });
    }

    const stats = new Set([...Object.keys(previous.stats || {}), ...Object.keys(character.stats || {})]);
    stats.forEach((stat) => {
      const oldValue = previous.stats?.[stat];
      const newValue = character.stats?.[stat];
      if (!Number.isInteger(oldValue) || !Number.isInteger(newValue) || oldValue === newValue) return;
      receipts.push({
        type: "stat_changed",
        characterId: character.id,
        stat,
        delta: newValue - oldValue,
        value: newValue,
      });
    });

    const oldItems = inventoryQuantities(previous);
    const newItems = inventoryQuantities(character);
    const itemIds = new Set([...oldItems.keys(), ...newItems.keys()]);
    itemIds.forEach((itemId) => {
      const oldQuantity = oldItems.get(itemId)?.quantity || 0;
      const newQuantity = newItems.get(itemId)?.quantity || 0;
      const delta = newQuantity - oldQuantity;
      if (delta === 0) return;
      receipts.push({
        type: delta > 0 ? "item_added" : "item_removed",
        characterId: character.id,
        item: newItems.get(itemId)?.name || oldItems.get(itemId)?.name || itemId,
        quantity: Math.abs(delta),
      });
    });
  });

  if (before.location !== after.location) {
    receipts.push({ type: "location_changed", value: after.location });
  }
  return receipts;
}

export function turnReceipts(events, before, after) {
  const projected = (events || []).map(receiptFromEvent).filter(Boolean);
  return projected.length ? projected : snapshotReceipts(before, after);
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
