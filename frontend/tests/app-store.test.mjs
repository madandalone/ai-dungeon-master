import assert from "node:assert/strict";
import test from "node:test";

import { AppStore } from "../js/app-store.js";

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
  };
}

function session(id, heroId) {
  return {
    session_id: id,
    session_name: id,
    connection: "server",
    revision: 0,
    turn_number: 0,
    location: "start",
    characters: [{
      id: heroId, name: heroId, concept: "", hp: 10, max_hp: 10,
      stats: {}, inventory: [],
    }],
    log: [],
    pending_turn: null,
  };
}

test("store coalesces several changes into one render", async () => {
  const store = new AppStore({ sessionStorage: memoryStorage(), localStorage: memoryStorage() });
  let renders = 0;
  store.subscribe(() => { renders += 1; });

  store.backend = "online";
  store.notify();
  store.setOperation("sync", true);
  store.setFeedback({ kind: "pending", key: "game.syncing" });
  await Promise.resolve();

  assert.equal(renders, 1);
});

test("each session keeps its own selected hero", async () => {
  const store = new AppStore({ sessionStorage: memoryStorage(), localStorage: memoryStorage() });
  store.putSession(session("one", "hero-one"));
  store.setSelectedCharacter("hero-one");
  store.putSession(session("two", "hero-two"));
  store.setSelectedCharacter("hero-two");
  await Promise.resolve();

  store.activateSession("one");
  assert.equal(store.selectedCharacterId(), "hero-one");
  store.activateSession("two");
  assert.equal(store.selectedCharacterId(), "hero-two");
});

test("background response updates its session without changing the active one", async () => {
  const store = new AppStore({ sessionStorage: memoryStorage(), localStorage: memoryStorage() });
  store.putSession(session("one", "hero-one"));
  store.putSession(session("two", "hero-two"));
  store.putSession({ ...session("one", "hero-one"), revision: 4 }, false);
  await Promise.resolve();

  assert.equal(store.currentSessionId(), "two");
  assert.equal(store.sessions.sessions.one.revision, 4);
});
