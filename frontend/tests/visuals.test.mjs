import assert from "node:assert/strict";
import test from "node:test";

import { characterArchetype, healthPercent, healthTone, iconMarkup } from "../js/visuals.js";

test("character concepts map to bilingual archetypes", () => {
  assert.equal(characterArchetype("Лесной следопыт"), "ranger");
  assert.equal(characterArchetype("Battle mage"), "mage");
  assert.equal(characterArchetype("Странник без класса"), "adventurer");
  assert.match(iconMarkup("rogue"), /<svg/);
});

test("health tone uses readable threshold boundaries", () => {
  assert.equal(healthTone(8, 10), "healthy");
  assert.equal(healthTone(5, 10), "warning");
  assert.equal(healthTone(2.5, 10), "critical");
  assert.equal(healthTone(1, 0), "critical");
});

test("health percentage is clamped", () => {
  assert.equal(healthPercent(12, 10), 100);
  assert.equal(healthPercent(-2, 10), 0);
  assert.equal(healthPercent(3, 12), 25);
});
