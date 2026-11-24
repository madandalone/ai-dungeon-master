export function resolveApiBase(locationLike = globalThis.location) {
  const protocol = locationLike?.protocol === "https:" ? "https:" : "http:";
  const hostname = locationLike?.hostname || "127.0.0.1";
  return `${protocol}//${hostname}:8000`;
}

export const API_BASE = resolveApiBase();

export class ApiError extends Error {
  constructor(code, status = 0) {
    super(code);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

function errorCodeForStatus(status) {
  if (status === 404) return "not_found";
  if (status === 409) return "stale_revision";
  if (status === 502) return "master_unavailable";
  return "http_error";
}

async function requestJson(url, options = {}, fetcher = globalThis.fetch) {
  let response;
  try {
    response = await fetcher(url, options);
  } catch {
    throw new ApiError("network_error");
  }
  if (!response.ok) {
    let body = null;
    try {
      body = await response.json();
    } catch {
      // Some upstream failures have no JSON body.
    }
    const code = body?.detail?.code || errorCodeForStatus(response.status);
    throw new ApiError(code, response.status);
  }
  try {
    return await response.json();
  } catch {
    throw new ApiError("invalid_response", response.status);
  }
}

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isCounter(value) {
  return Number.isInteger(value) && value >= 0;
}

function isIssueList(value) {
  return Array.isArray(value) && value.every((issue) => isObject(issue) && typeof issue.code === "string");
}

function isPendingTurn(value) {
  return isObject(value)
    && value.status === "awaiting_roll"
    && typeof value.action_id === "string"
    && typeof value.character_id === "string"
    && typeof value.text === "string"
    && Number.isInteger(value.sequence) && value.sequence >= 1 && value.sequence <= 3
    && Number.isInteger(value.maximum) && value.maximum === 3
    && typeof value.ability === "string"
    && Number.isInteger(value.score) && value.score >= 1 && value.score <= 20
    && Number.isInteger(value.modifier)
    && [5, 10, 15, 20, 25, 30].includes(value.difficulty)
    && Number.isInteger(value.chance) && value.chance >= 0 && value.chance <= 100
    && typeof value.reason === "string"
    && Array.isArray(value.resolved_checks)
    && typeof value.can_cancel === "boolean";
}

function isCampaign(value) {
  return isObject(value)
    && typeof value.started === "boolean"
    && typeof value.title === "string"
    && typeof value.theme === "string"
    && typeof value.opening_hook === "string";
}

function eventBase(event) {
  if (!isObject(event) || typeof event.action_id !== "string" || !isCounter(event.sequence)) return null;
  return { type: event.type, action_id: event.action_id, sequence: event.sequence };
}

function normalizePublicEvent(event) {
  const base = eventBase(event);
  if (!base) return null;
  if (event.type === "health_changed"
    && typeof event.character_id === "string"
    && Number.isInteger(event.previous_hp)
    && Number.isInteger(event.new_hp)
    && Number.isInteger(event.delta)) {
    return {
      ...base,
      character_id: event.character_id,
      previous_hp: event.previous_hp,
      new_hp: event.new_hp,
      delta: event.delta,
    };
  }
  if (["item_added", "item_removed"].includes(event.type)
    && typeof event.character_id === "string"
    && typeof event.item_id === "string"
    && Number.isInteger(event.quantity)
    && Number.isInteger(event.new_quantity)) {
    return {
      ...base,
      character_id: event.character_id,
      item_id: event.item_id,
      quantity: event.quantity,
      new_quantity: event.new_quantity,
    };
  }
  if (event.type === "location_changed"
    && typeof event.previous_location === "string"
    && typeof event.location === "string") {
    return { ...base, previous_location: event.previous_location, location: event.location };
  }
  if (event.type === "stat_changed"
    && typeof event.character_id === "string"
    && typeof event.stat === "string"
    && Number.isInteger(event.previous_value)
    && Number.isInteger(event.new_value)
    && Number.isInteger(event.delta)) {
    return {
      ...base,
      character_id: event.character_id,
      stat: event.stat,
      previous_value: event.previous_value,
      new_value: event.new_value,
      delta: event.delta,
    };
  }
  if (event.type === "skill_check_resolved"
    && typeof event.character_id === "string"
    && typeof event.ability === "string"
    && Number.isInteger(event.roll)
    && Number.isInteger(event.modifier)
    && Number.isInteger(event.total)
    && Number.isInteger(event.difficulty)
    && typeof event.success === "boolean") {
    return {
      ...base,
      character_id: event.character_id,
      ability: event.ability,
      roll: event.roll,
      modifier: event.modifier,
      total: event.total,
      difficulty: event.difficulty,
      success: event.success,
    };
  }
  if (event.type === "action_rejected" && typeof event.code === "string") {
    return { ...base, code: event.code };
  }
  return null;
}

export function normalizePublicEvents(value) {
  if (value === undefined) return [];
  if (!Array.isArray(value)) return null;
  return value.map(normalizePublicEvent).filter(Boolean);
}

function componentStatus(value) {
  return ["online", "degraded", "offline"].includes(value) ? value : "unknown";
}

function healthComponents(body, backend = "online") {
  const components = isObject(body?.components) ? body.components : {};
  return {
    backend,
    dm: componentStatus(components.dm),
    rules: componentStatus(components.rules),
    history: componentStatus(components.history),
  };
}

export function isPublicState(value) {
  return isObject(value)
    && typeof value.session_id === "string"
    && typeof value.session_name === "string"
    && typeof value.location === "string"
    && Array.isArray(value.characters)
    && Array.isArray(value.log)
    && isCounter(value.revision)
    && isCounter(value.turn_number)
    && (value.campaign === undefined || isCampaign(value.campaign))
    && (value.pending_turn === undefined || value.pending_turn === null || isPendingTurn(value.pending_turn));
}

export async function fetchHealth(fetcher = globalThis.fetch) {
  try {
    const response = await fetcher(`${API_BASE}/health`);
    if (!response.ok) return { available: false, code: "http_error", components: healthComponents(null, "offline") };
    const body = await response.json();
    return body?.status === "ok"
      ? { available: true, code: "ok", components: healthComponents(body) }
      : { available: false, code: "invalid_response", components: healthComponents(null, "offline") };
  } catch {
    return { available: false, code: "network_error", components: healthComponents(null, "offline") };
  }
}

export async function createSession(session, fetcher = globalThis.fetch) {
  const payload = typeof session === "string" ? { name: session } : {
    name: session.name,
    theme: session.theme,
    opening_hook: session.openingHook,
  };
  const body = await requestJson(`${API_BASE}/sessions`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  }, fetcher);
  if (!body || typeof body.id !== "string" || typeof body.name !== "string") throw new ApiError("invalid_response");
  return { id: body.id, name: body.name };
}

export async function startCampaign(sessionId, fetcher = globalThis.fetch) {
  const body = await requestJson(`${API_BASE}/sessions/${encodeURIComponent(sessionId)}/campaign`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  }, fetcher);
  if (typeof body?.created !== "boolean" || typeof body.title !== "string" || !isPublicState(body.state)) {
    throw new ApiError("invalid_response");
  }
  return body;
}

export async function deleteSession(sessionId, fetcher = globalThis.fetch) {
  const body = await requestJson(`${API_BASE}/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  }, fetcher);
  if (body?.status !== "deleted" || body.session_id !== sessionId) {
    throw new ApiError("invalid_response");
  }
  return body;
}

export async function fetchSessionState(sessionId, fetcher = globalThis.fetch) {
  const body = await requestJson(`${API_BASE}/sessions/${encodeURIComponent(sessionId)}/state`, {}, fetcher);
  if (!isPublicState(body)) throw new ApiError("invalid_response");
  return body;
}

export async function createCharacter(sessionId, character, fetcher = globalThis.fetch) {
  const body = await requestJson(`${API_BASE}/sessions/${encodeURIComponent(sessionId)}/characters`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      expected_revision: character.expectedRevision,
      name: character.name,
      concept: character.concept,
      max_hp: character.maxHp,
      stats: character.stats,
      starting_item: character.startingItem,
    }),
  }, fetcher);
  if (!isPublicState(body)) throw new ApiError("invalid_response");
  return body;
}

export async function submitTurn(sessionId, turn, fetcher = globalThis.fetch) {
  const body = await requestJson(`${API_BASE}/sessions/${encodeURIComponent(sessionId)}/turns`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      action_id: turn.actionId,
      expected_revision: turn.expectedRevision,
      character_id: turn.characterId,
      text: turn.text,
    }),
  }, fetcher);
  if (body?.status === "awaiting_roll") {
    if (!isPendingTurn(body.pending_turn) || !isPublicState(body.state)) {
      throw new ApiError("invalid_response");
    }
    return body;
  }
  const events = normalizePublicEvents(body?.events);
  const valid = isObject(body)
    && typeof body.accepted === "boolean"
    && (body.narration === null || typeof body.narration === "string")
    && isPublicState(body.state)
    && Number.isInteger(body.revision) && body.revision >= 0
    && Number.isInteger(body.turn_number) && body.turn_number >= 0
    && isIssueList(body.errors)
    && (body.warnings === undefined || isIssueList(body.warnings))
    && events !== null;
  if (!valid) throw new ApiError("invalid_response");
  return { ...body, status: body.status || "resolved", warnings: body.warnings || [], events };
}

export async function rollTurn(sessionId, actionId, sequence, fetcher = globalThis.fetch) {
  const body = await requestJson(
    `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(actionId)}/roll`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sequence }),
    },
    fetcher,
  );
  if (body?.status === "awaiting_roll") {
    if (!isPendingTurn(body.pending_turn) || !isPublicState(body.state)) {
      throw new ApiError("invalid_response");
    }
    return body;
  }
  const events = normalizePublicEvents(body?.events);
  if (!isObject(body) || body.status !== "resolved" || typeof body.accepted !== "boolean"
    || !isPublicState(body.state) || events === null || !isIssueList(body.errors)
    || (body.warnings !== undefined && !isIssueList(body.warnings))) {
    throw new ApiError("invalid_response");
  }
  return { ...body, warnings: body.warnings || [], events };
}

export async function cancelTurn(sessionId, actionId, fetcher = globalThis.fetch) {
  const body = await requestJson(
    `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(actionId)}/cancel`,
    { method: "POST" },
    fetcher,
  );
  if (body?.status !== "cancelled" || body.action_id !== actionId) throw new ApiError("invalid_response");
  return body;
}

export class ApiClient {
  constructor(fetcher = globalThis.fetch) {
    this.fetcher = fetcher;
  }

  health() { return fetchHealth(this.fetcher); }
  createSession(session) { return createSession(session, this.fetcher); }
  startCampaign(sessionId) { return startCampaign(sessionId, this.fetcher); }
  deleteSession(sessionId) { return deleteSession(sessionId, this.fetcher); }
  sessionState(sessionId) { return fetchSessionState(sessionId, this.fetcher); }
  createCharacter(sessionId, character) { return createCharacter(sessionId, character, this.fetcher); }
  submitTurn(sessionId, turn) { return submitTurn(sessionId, turn, this.fetcher); }
  rollTurn(sessionId, actionId, sequence) { return rollTurn(sessionId, actionId, sequence, this.fetcher); }
  cancelTurn(sessionId, actionId) { return cancelTurn(sessionId, actionId, this.fetcher); }
}
