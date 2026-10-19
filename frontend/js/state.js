export const STORAGE_KEY = "ai-dungeon-master.session-cache.v2";
export const LEGACY_STORAGE_KEY = "ai-dungeon-master.public-cache";

export function emptyPublicState(sessionId = "", sessionName = "", connection = "unverified") {
  return {
    connection, session_id: String(sessionId), session_name: String(sessionName),
    revision: 0, turn_number: 0, location: "", characters: [], log: [],
  };
}

export function emptySessionStore() { return { version: 2, activeSessionId: null, sessions: {} }; }

function copyCharacter(value) {
  const character = value && typeof value === "object" ? value : {};
  const stats = {};
  Object.entries(character.stats || {}).forEach(([name, statValue]) => {
    const numeric = Number(statValue);
    if (name.trim() && Number.isFinite(numeric)) stats[String(name)] = numeric;
  });
  return {
    id: String(character.id || ""), name: String(character.name || ""), concept: String(character.concept || ""),
    hp: Number.isFinite(Number(character.hp)) ? Number(character.hp) : 0,
    max_hp: Number.isFinite(Number(character.max_hp)) ? Number(character.max_hp) : 0,
    stats,
    inventory: Array.isArray(character.inventory)
      ? character.inventory.filter(Boolean).map((item) => ({
          item_id: String(item.item_id || ""), name: String(item.name || item.item_id || ""),
          quantity: Number.isFinite(Number(item.quantity)) ? Number(item.quantity) : 0,
        }))
      : [],
  };
}

export function normalizePublicState(value, fallbackId = "") {
  const source = value && typeof value === "object" ? value : {};
  const connection = ["server", "unverified", "legacy"].includes(source.connection) ? source.connection : "unverified";
  const revision = Number(source.revision);
  const turnNumber = Number(source.turn_number);
  return {
    connection, session_id: String(source.session_id || fallbackId), session_name: String(source.session_name || ""),
    revision: Number.isInteger(revision) && revision >= 0 ? revision : 0,
    turn_number: Number.isInteger(turnNumber) && turnNumber >= 0 ? turnNumber : 0,
    location: String(source.location || ""),
    characters: Array.isArray(source.characters) ? source.characters.map(copyCharacter) : [],
    log: Array.isArray(source.log)
      ? source.log.filter((entry) => entry && (entry.kind === "player" || entry.kind === "master")).map((entry) => ({
          kind: entry.kind, character_id: String(entry.character_id || ""), text: String(entry.text || ""),
        }))
      : [],
  };
}

export function normalizeSessionStore(value) {
  const source = value && typeof value === "object" ? value : {};
  const sessions = {};
  if (source.sessions && typeof source.sessions === "object") {
    Object.entries(source.sessions).forEach(([id, session]) => {
      const normalized = normalizePublicState(session, id);
      if (normalized.session_id) sessions[normalized.session_id] = normalized;
    });
  }
  const requestedActive = String(source.activeSessionId || "");
  const activeSessionId = sessions[requestedActive] ? requestedActive : Object.keys(sessions)[0] || null;
  return { version: 2, activeSessionId, sessions };
}

function legacyHasContent(state) {
  return Boolean(state.session_id || state.session_name || state.location || state.characters.length || state.log.length);
}

export function migrateLegacyState(value) {
  const legacy = normalizePublicState({ ...value, connection: "legacy" });
  if (!legacyHasContent(legacy)) return emptySessionStore();
  const sessionId = legacy.session_id || "legacy-local";
  const migrated = { ...legacy, session_id: sessionId, connection: "legacy" };
  return { version: 2, activeSessionId: sessionId, sessions: { [sessionId]: migrated } };
}

export function loadSessionStore(storage = globalThis.sessionStorage) {
  if (!storage) return emptySessionStore();
  try {
    const current = storage.getItem(STORAGE_KEY);
    if (current) return normalizeSessionStore(JSON.parse(current));
    const legacy = storage.getItem(LEGACY_STORAGE_KEY);
    if (!legacy) return emptySessionStore();
    const migrated = migrateLegacyState(JSON.parse(legacy));
    storage.setItem(STORAGE_KEY, JSON.stringify(migrated));
    return migrated;
  } catch { return emptySessionStore(); }
}

export function saveSessionStore(store, storage = globalThis.sessionStorage) {
  const normalized = normalizeSessionStore(store);
  storage?.setItem(STORAGE_KEY, JSON.stringify(normalized));
  return normalized;
}

export function activePublicState(store) {
  if (!store?.activeSessionId) return null;
  return store.sessions?.[store.activeSessionId] || null;
}

export function putSession(store, state, activate = true) {
  const normalizedStore = normalizeSessionStore(store);
  const normalizedState = normalizePublicState(state);
  if (!normalizedState.session_id) return normalizedStore;
  return { ...normalizedStore, activeSessionId: activate ? normalizedState.session_id : normalizedStore.activeSessionId, sessions: { ...normalizedStore.sessions, [normalizedState.session_id]: normalizedState } };
}

export function activateSession(store, sessionId) {
  const normalized = normalizeSessionStore(store);
  if (!normalized.sessions[sessionId]) return normalized;
  return { ...normalized, activeSessionId: sessionId };
}
