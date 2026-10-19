import assert from "node:assert/strict";
import test from "node:test";

import {
  ApiError, createSession, fetchHealth, fetchSessionState, resolveApiBase, submitTurn,
} from "../js/api.js";

function response(body, { ok = true, status = 200 } = {}) {
  return { ok, status, json: async () => body };
}

function state(overrides = {}) {
  return {
    session_id: "session/1", session_name: "Quest", revision: 0, turn_number: 0,
    location: "start", characters: [], log: [], ...overrides,
  };
}

test("API base follows the frontend hostname", () => {
  assert.equal(resolveApiBase({ protocol: "http:", hostname: "localhost" }), "http://localhost:8000");
  assert.equal(resolveApiBase({ protocol: "https:", hostname: "game.example" }), "https://game.example:8000");
});

test("health result does not contain user-facing language", async () => {
  const result = await fetchHealth(async () => ({ ok: true, json: async () => ({ status: "ok" }) }));
  assert.deepEqual(result, {
    available: true,
    code: "ok",
    components: { backend: "online", dm: "unknown", rules: "unknown", history: "unknown" },
  });
  const detailed = await fetchHealth(async () => response({
    status: "ok", components: { dm: "online", rules: "degraded", history: "offline" },
  }));
  assert.deepEqual(detailed.components, { backend: "online", dm: "online", rules: "degraded", history: "offline" });
});

test("create session validates the response contract", async () => {
  await assert.rejects(() => createSession("Quest", async () => response({ id: 12 })), /invalid_response/);
});

test("session state uses an encoded id and validates public data", async () => {
  let requestedUrl = "";
  const result = await fetchSessionState("session/1", async (url) => {
    requestedUrl = url;
    return response(state());
  });
  assert.match(requestedUrl, /sessions\/session%2F1\/state$/);
  assert.equal(result.location, "start");
  await assert.rejects(() => fetchSessionState("bad", async () => response({ session_id: "bad" })), /invalid_response/);
  await assert.rejects(
    () => fetchSessionState("missing-turn", async () => response(state({ turn_number: undefined }))),
    /invalid_response/,
  );
});

test("turn request sends the backend contract", async () => {
  let request;
  const result = await submitTurn("s1", { characterId: "hero", text: "Open the door" }, async (url, options) => {
    request = { url, options };
    return response({ accepted: true, narration: "The door opens.", state: state({ session_id: "s1" }), revision: 1, turn_number: 1, errors: [] });
  });
  assert.match(request.url, /sessions\/s1\/turns$/);
  assert.deepEqual(JSON.parse(request.options.body), { character_id: "hero", text: "Open the door" });
  assert.equal(result.turn_number, 1);
  assert.deepEqual(result.warnings, []);
});

test("turn response accepts structured non-fatal warnings", async () => {
  const result = await submitTurn("s1", { characterId: "hero", text: "Look" }, async () => response({
    accepted: true, narration: "You look around.", state: state({ session_id: "s1" }),
    revision: 1, turn_number: 1, errors: [], warnings: [{ code: "RULES_UNAVAILABLE" }],
  }));
  assert.equal(result.warnings[0].code, "RULES_UNAVAILABLE");
});

test("API errors distinguish missing sessions, DM failures, and network failures", async () => {
  await assert.rejects(
    () => fetchSessionState("missing", async () => response({}, { ok: false, status: 404 })),
    (error) => error instanceof ApiError && error.code === "not_found" && error.status === 404,
  );
  await assert.rejects(
    () => submitTurn("s1", { characterId: "hero", text: "Go" }, async () => response({}, { ok: false, status: 502 })),
    (error) => error instanceof ApiError && error.code === "master_unavailable",
  );
  await assert.rejects(
    () => fetchSessionState("s1", async () => { throw new Error("offline"); }),
    (error) => error instanceof ApiError && error.code === "network_error",
  );
});
