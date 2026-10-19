import assert from "node:assert/strict";
import test from "node:test";

import { applyTurnResult, failureMessageKey, mergeServerState, pinnedContext, stateChanges, warningMessageKey } from "../js/turns.js";

function state(overrides = {}) {
  return {
    connection: "server", session_id: "s1", session_name: "Quest", revision: 0, turn_number: 0,
    location: "start", characters: [{ id: "hero", name: "Hero", hp: 10, max_hp: 10, stats: {}, inventory: [] }],
    log: [], ...overrides,
  };
}

test("server refresh preserves the tab chronicle and updates counters", () => {
  const previous = state({ log: [{ kind: "player", character_id: "hero", text: "Wait" }] });
  const merged = mergeServerState(previous, state({ log: [], revision: 2, turn_number: 2 }));
  assert.equal(merged.log[0].text, "Wait");
  assert.equal(merged.turn_number, 2);
});

test("accepted turn appends public player and master entries", () => {
  const previous = state();
  const next = applyTurnResult(previous, { characterId: "hero", text: "Open the door" }, {
    accepted: true,
    narration: "The door opens.",
    state: state({ characters: [{ id: "hero", name: "Hero", hp: 9, max_hp: 10, stats: {}, inventory: [] }] }),
    revision: 1, turn_number: 1, errors: [],
  });
  assert.equal(next.characters[0].hp, 9);
  assert.equal(next.turn_number, 1);
  assert.deepEqual(next.log.map((entry) => entry.kind), ["player", "master"]);
});

test("rejected turn updates the snapshot without adding chronicle entries", () => {
  const previous = state({ log: [{ kind: "player", character_id: "hero", text: "Wait" }] });
  const next = applyTurnResult(previous, { characterId: "missing", text: "Go" }, {
    accepted: false, narration: null, state: state(), revision: 0, turn_number: 0,
    errors: [{ code: "INVALID_CHARACTER", message: "hidden backend wording" }],
  });
  assert.equal(next.log.length, 1);
  assert.equal(failureMessageKey({ errors: [{ code: "INVALID_CHARACTER" }] }), "game.error.invalidCharacter");
});

test("state changes identify HP, inventory, and location updates", () => {
  const before = state();
  const after = state({
    location: "crypt",
    characters: [{ id: "hero", name: "Hero", hp: 8, max_hp: 10, stats: {}, inventory: [{ item_id: "key", name: "Key", quantity: 1 }] }],
  });
  assert.deepEqual(stateChanges(before, after), {
    location: true,
    characters: { hero: { hp: true, inventory: true } },
  });
});

test("failure messages never expose raw backend text", () => {
  assert.equal(failureMessageKey({ code: "master_unavailable", message: "secret" }), "game.error.masterUnavailable");
  assert.equal(failureMessageKey({ code: "something_new", message: "secret" }), "game.error.generic");
});

test("rules outages become safe non-fatal warnings", () => {
  assert.equal(warningMessageKey([{ code: "RULES_UNAVAILABLE", message: "private details" }]), "game.warning.rulesUnavailable");
  assert.equal(warningMessageKey([{ code: "NEW_WARNING" }]), "game.warning.generic");
  assert.equal(warningMessageKey([]), null);
});

test("pinned context follows the selected hero and latest event", () => {
  const current = state({
    session_name: "The Crypt",
    characters: [
      { id: "hero", name: "Hero", hp: 9, max_hp: 10, stats: {}, inventory: [] },
      { id: "mage", name: "Mage", hp: 6, max_hp: 8, stats: {}, inventory: [] },
    ],
    log: [
      { kind: "player", character_id: "hero", text: "Open the door" },
      { kind: "master", character_id: "", text: "Cold air fills the hall." },
    ],
  });
  const context = pinnedContext(current, "mage");
  assert.equal(context.sessionName, "The Crypt");
  assert.equal(context.hero.name, "Mage");
  assert.equal(context.lastEvent.text, "Cold air fills the hall.");
});
