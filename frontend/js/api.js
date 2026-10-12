export function resolveApiBase(locationLike = globalThis.location) {
  const protocol = locationLike?.protocol === "https:" ? "https:" : "http:";
  const hostname = locationLike?.hostname || "127.0.0.1";
  return `${protocol}//${hostname}:8000`;
}

export const API_BASE = resolveApiBase();

export async function fetchHealth(fetcher = globalThis.fetch) {
  try {
    const response = await fetcher(`${API_BASE}/health`);
    if (!response.ok) return { available: false, code: "http_error" };
    const body = await response.json();
    return body?.status === "ok" ? { available: true, code: "ok" } : { available: false, code: "invalid_response" };
  } catch {
    return { available: false, code: "network_error" };
  }
}

export async function createSession(name, fetcher = globalThis.fetch) {
  let response;
  try {
    response = await fetcher(`${API_BASE}/sessions`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
    });
  } catch {
    throw new Error("network_error");
  }
  if (!response.ok) throw new Error("http_error");
  const body = await response.json();
  if (!body || typeof body.id !== "string" || typeof body.name !== "string") throw new Error("invalid_response");
  return { id: body.id, name: body.name };
}
