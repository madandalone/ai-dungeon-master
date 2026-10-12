import assert from "node:assert/strict";
import test from "node:test";

import { createSession, fetchHealth, resolveApiBase } from "../js/api.js";

test("API base follows the frontend hostname", () => {
  assert.equal(resolveApiBase({ protocol: "http:", hostname: "localhost" }), "http://localhost:8000");
  assert.equal(resolveApiBase({ protocol: "https:", hostname: "game.example" }), "https://game.example:8000");
});

test("health result does not contain user-facing language", async () => {
  const result = await fetchHealth(async () => ({ ok: true, json: async () => ({ status: "ok" }) }));
  assert.deepEqual(result, { available: true, code: "ok" });
});

test("create session validates the response contract", async () => {
  await assert.rejects(() => createSession("Quest", async () => ({ ok: true, json: async () => ({ id: 12 }) })), /invalid_response/);
});
