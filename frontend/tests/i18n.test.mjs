import assert from "node:assert/strict";
import test from "node:test";

import { getLanguage, setLanguage, t, translations } from "../js/i18n.js";

function memoryStorage() {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
}

test("Russian and English dictionaries expose the same keys", () => {
  assert.deepEqual(Object.keys(translations.ru).sort(), Object.keys(translations.en).sort());
});

test("language selection is normalized, persisted, and interpolated", () => {
  const storage = memoryStorage();
  setLanguage("en", storage);
  assert.equal(getLanguage(), "en");
  assert.equal(t("game.turns", { count: 4 }), "Entries: 4");
  setLanguage("unknown", storage);
  assert.equal(getLanguage(), "ru");
  assert.match(storage.getItem("ai-dungeon-master.ui-settings"), /"language":"ru"/);
});
