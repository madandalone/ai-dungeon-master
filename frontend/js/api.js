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
  if (!response.ok) throw new ApiError(errorCodeForStatus(response.status), response.status);
  try {
    return await response.json();
  } catch {
    throw new ApiError("invalid_response", response.status);
  }
}

function isObject(value) { return Boolean(value) && typeof value === "object" && !Array.isArray(value); }
function isCounter(value) { return Number.isInteger(value) && value >= 0; }
function isIssueList(value) {
  return Array.isArray(value) && value.every((issue) => isObject(issue) && typeof issue.code === "string");
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
    && isCounter(value.turn_number);
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

export async function createSession(name, fetcher = globalThis.fetch) {
  const body = await requestJson(`${API_BASE}/sessions`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
  }, fetcher);
  if (!body || typeof body.id !== "string" || typeof body.name !== "string") throw new ApiError("invalid_response");
  return { id: body.id, name: body.name };
}

export async function fetchSessionState(sessionId, fetcher = globalThis.fetch) {
  const body = await requestJson(`${API_BASE}/sessions/${encodeURIComponent(sessionId)}/state`, {}, fetcher);
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
  const valid = isObject(body)
    && typeof body.accepted === "boolean"
    && (body.narration === null || typeof body.narration === "string")
    && isPublicState(body.state)
    && Number.isInteger(body.revision) && body.revision >= 0
    && Number.isInteger(body.turn_number) && body.turn_number >= 0
    && isIssueList(body.errors)
    && (body.warnings === undefined || isIssueList(body.warnings));
  if (!valid) throw new ApiError("invalid_response");
  return { ...body, warnings: body.warnings || [] };
}
