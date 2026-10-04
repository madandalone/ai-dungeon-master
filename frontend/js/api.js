export const API_BASE = "http://127.0.0.1:8000";

export async function fetchHealth() {
  try {
    const response = await fetch(`${API_BASE}/health`);
    if (!response.ok) {
      return { available: false, status: "Backend unavailable" };
    }
    const body = await response.json();
    if (!body || body.status !== "ok") {
      return { available: false, status: "Backend unavailable" };
    }
    return { available: true, status: "Backend online" };
  } catch {
    return { available: false, status: "Backend unavailable" };
  }
}

export async function createSession(name) {
  let response;
  try {
    response = await fetch(`${API_BASE}/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
  } catch {
    throw new Error("Backend unavailable");
  }
  if (!response.ok) {
    throw new Error("Backend unavailable");
  }
  const body = await response.json();
  if (!body || typeof body.id !== "string" || typeof body.name !== "string") {
    throw new Error("Backend unavailable");
  }
  return { id: body.id, name: body.name };
}
