import {
  activateSession, activePublicState, loadSessionStore, putSession,
  removeSession, saveSessionStore,
} from "./state.js?v=20261124-1";
import { SETTINGS_KEY } from "./i18n.js?v=20261124-1";

const SECTIONS = new Set(["game", "characters", "world", "session"]);

function readSettings(storage) {
  try {
    return JSON.parse(storage?.getItem(SETTINGS_KEY) || "{}") || {};
  } catch {
    return {};
  }
}

export class AppStore {
  constructor({ sessionStorage = globalThis.sessionStorage, localStorage = globalThis.localStorage } = {}) {
    this.sessionStorage = sessionStorage;
    this.localStorage = localStorage;
    const settings = readSettings(localStorage);
    this.sessions = loadSessionStore(sessionStorage);
    this.section = SECTIONS.has(settings.activeSection) ? settings.activeSection : "game";
    this.sidebarCollapsed = Boolean(settings.sidebarCollapsed);
    this.selectedBySession = { ...(settings.selectedBySession || {}) };
    this.backend = "checking";
    this.operation = { session: null, character: null, turn: "idle", sync: false, deleting: false };
    this.pendingAttempt = null;
    this.feedback = null;
    this.hudExpanded = false;
    this.copyFeedback = "";
    this.deleteTarget = null;
    this.showCharacterNext = false;
    this.syncTimes = new Map();
    this.receipts = new Map();
    this.listeners = new Set();
    this.renderScheduled = false;
  }

  subscribe(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  notify() {
    if (this.renderScheduled) return;
    this.renderScheduled = true;
    queueMicrotask(() => {
      this.renderScheduled = false;
      this.listeners.forEach((listener) => listener(this));
    });
  }

  current() { return activePublicState(this.sessions); }

  currentSessionId() { return this.sessions.activeSessionId; }

  selectedCharacterId(session = this.current()) {
    if (!session) return "";
    const requested = this.selectedBySession[session.session_id];
    return session.characters.some((character) => character.id === requested)
      ? requested
      : session.characters[0]?.id || "";
  }

  setSelectedCharacter(characterId) {
    const session = this.current();
    if (!session) return;
    this.selectedBySession[session.session_id] = characterId;
    this.persistSettings();
    this.notify();
  }

  setSection(section) {
    if (!SECTIONS.has(section)) return;
    this.section = section;
    this.persistSettings();
    this.notify();
  }

  setSidebarCollapsed(collapsed) {
    this.sidebarCollapsed = Boolean(collapsed);
    this.persistSettings();
    this.notify();
  }

  putSession(state, activate = true) {
    this.sessions = putSession(this.sessions, state, activate);
    this.persistSessions();
    this.notify();
  }

  activateSession(sessionId) {
    this.sessions = activateSession(this.sessions, sessionId);
    this.pendingAttempt = null;
    this.persistSessions();
    this.notify();
  }

  removeSession(sessionId) {
    this.sessions = removeSession(this.sessions, sessionId);
    delete this.selectedBySession[sessionId];
    this.syncTimes.delete(sessionId);
    this.receipts.delete(sessionId);
    this.pendingAttempt = null;
    this.feedback = null;
    this.persistSessions();
    this.persistSettings();
    this.notify();
  }

  setOperation(kind, value) {
    this.operation = { ...this.operation, [kind]: value };
    this.notify();
  }

  setFeedback(feedback) {
    this.feedback = feedback;
    this.notify();
  }

  isBusy() {
    return Boolean(
      this.operation.session || this.operation.character || this.operation.sync
      || this.operation.deleting || this.operation.turn !== "idle"
    );
  }

  persistSessions() {
    this.sessions = saveSessionStore(this.sessions, this.sessionStorage);
  }

  persistSettings() {
    const previous = readSettings(this.localStorage);
    this.localStorage?.setItem(SETTINGS_KEY, JSON.stringify({
      ...previous,
      activeSection: this.section,
      sidebarCollapsed: this.sidebarCollapsed,
      selectedBySession: this.selectedBySession,
    }));
  }
}
