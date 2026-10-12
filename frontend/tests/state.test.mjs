import assert from "node:assert/strict";
import test from "node:test";

import {
  LEGACY_STORAGE_KEY, STORAGE_KEY, activateSession, activePublicState, emptyPublicState,
  loadSessionStore, migrateLegacyState, putSession, saveSessionStore,
} from "../js/state.js";

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
  };
}

test("legacy cache with a session id is migrated without losing progress", () => {
  const storage = memoryStorage({
    [LEGACY_STORAGE_KEY]: JSON.stringify({
      session_id: "old-session", session_name: "Old Quest", location: "crypt",
      characters: [{ id: "hero", name: "Elias", hp: 7, max_hp: 10, stats: {}, inventory: [] }],
      log: [{ kind: "player", character_id: "hero", text: "I enter." }],
    }),
  });
  const store = loadSessionStore(storage);
  assert.equal(store.activeSessionId, "old-session");
  assert.equal(activePublicState(store).characters[0].name, "Elias");
  assert.equal(activePublicState(store).connection, "legacy");
  assert.ok(storage.getItem(STORAGE_KEY));
});

test("legacy progress without an id remains available as read-only local data", () => {
  const store = migrateLegacyState({ characters: [{ id: "c1", name: "Ira" }] });
  assert.equal(store.activeSessionId, "legacy-local");
  assert.equal(store.sessions["legacy-local"].connection, "legacy");
});

test("sessions keep independent heroes and logs", () => {
  let store = putSession(undefined, { ...emptyPublicState("one", "One", "server"), log: [{ kind: "player", character_id: "a", text: "First" }] });
  store = putSession(store, { ...emptyPublicState("two", "Two", "unverified"), characters: [{ id: "b", name: "B", hp: 3, max_hp: 3 }] });
  assert.equal(activePublicState(store).session_id, "two");
  store = activateSession(store, "one");
  assert.equal(activePublicState(store).log[0].text, "First");
  assert.equal(activePublicState(store).characters.length, 0);
});

test("saved store can be loaded again", () => {
  const storage = memoryStorage();
  const store = putSession(undefined, emptyPublicState("session", "Quest", "server"));
  saveSessionStore(store, storage);
  assert.equal(loadSessionStore(storage).sessions.session.session_name, "Quest");
});
