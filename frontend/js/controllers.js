import { ApiError } from "./api.js?v=20261124-1";
import { getLanguage, setLanguage, t } from "./i18n.js?v=20261124-1";
import { emptyPublicState } from "./state.js?v=20261124-1";
import {
  applyTurnResult, createTurnAttempt, failureMessageKey, mergeServerState,
  replaceServerState, stateChanges, turnReceipts, warningMessageKey,
} from "./turns.js?v=20261124-1";

function sessionErrorKey(error) {
  if (error?.code === "not_found") return "session.notFound";
  if (error?.code === "network_error") return "session.networkError";
  if (error?.code === "invalid_response") return "session.invalidResponse";
  return "session.loadError";
}

async function copyText(value) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(value);
  const input = document.createElement("textarea");
  input.value = value;
  input.setAttribute("readonly", "");
  input.style.position = "fixed";
  input.style.opacity = "0";
  document.body.append(input);
  input.select();
  const copied = document.execCommand("copy");
  input.remove();
  if (!copied) throw new Error("copy_failed");
}

function resultState(previous, result) {
  return {
    ...mergeServerState(previous, result.state, result),
    pending_turn: result.status === "awaiting_roll" ? result.pending_turn : null,
  };
}

function latestCheck(result, sequence) {
  const checks = result.status === "awaiting_roll"
    ? result.pending_turn.resolved_checks
    : result.events.filter((event) => event.type === "skill_check_resolved");
  return checks.find((check) => check.sequence === sequence) || checks.at(-1);
}

export class SessionController {
  constructor({ store, view, api, elements, diceView }) {
    this.store = store;
    this.view = view;
    this.api = api;
    this.e = elements;
    this.diceView = diceView;
  }

  bind() {
    this.e.createSessionForm.addEventListener("submit", (event) => this.create(event));
    this.e.joinSessionForm.addEventListener("submit", (event) => this.join(event));
    this.e.sessionList.addEventListener("click", (event) => this.onListClick(event));
    this.e.sessionDeleteCancel.addEventListener("click", () => this.closeDelete());
    this.e.sessionDeleteConfirm.addEventListener("click", () => this.confirmDelete());
    this.e.sessionDeleteDialog.addEventListener("cancel", (event) => {
      if (this.store.operation.deleting) event.preventDefault();
      else this.store.deleteTarget = null;
    });
    this.e.sessionDeleteDialog.addEventListener("close", () => {
      if (!this.store.operation.deleting) this.store.deleteTarget = null;
    });
  }

  async refresh(sessionId, fallbackName = "", { announce = false, activate = false } = {}) {
    try {
      const snapshot = await this.api.sessionState(sessionId);
      const previous = this.store.sessions.sessions[sessionId];
      const merged = replaceServerState(snapshot);
      const shouldActivate = activate || this.store.currentSessionId() === sessionId;
      this.store.putSession({
        ...merged,
        session_name: merged.session_name || fallbackName || previous?.session_name || "",
      }, shouldActivate);
      this.store.syncTimes.set(sessionId, new Date());
      return true;
    } catch (error) {
      if (error instanceof ApiError && error.code === "network_error") this.store.backend = "offline";
      if (announce) this.e.sessionResult.textContent = t(sessionErrorKey(error));
      this.store.notify();
      return false;
    }
  }

  async create(event) {
    event.preventDefault();
    const name = this.e.sessionName.value.trim();
    if (!name) {
      this.e.sessionResult.textContent = t("session.nameRequired");
      return;
    }
    if (this.store.backend !== "online" || this.store.operation.session) return;
    this.store.setOperation("session", "create");
    this.e.sessionResult.textContent = t("session.creating");
    try {
      const session = await this.api.createSession(name);
      this.store.putSession(emptyPublicState(session.id, session.name, "server"), true);
      const loaded = await this.refresh(session.id, session.name, { announce: true, activate: true });
      if (!loaded) return;
      this.e.sessionResult.textContent = t("session.created", { name: session.name });
      this.e.createSessionForm.reset();
      this.store.setSection("characters");
      this.e.characterName.focus();
    } catch (error) {
      if (error instanceof ApiError && error.code === "network_error") this.store.backend = "offline";
      this.e.sessionResult.textContent = t(sessionErrorKey(error));
    } finally {
      this.store.setOperation("session", null);
    }
  }

  async join(event) {
    event.preventDefault();
    const sessionId = this.e.joinSessionId.value.trim();
    if (!sessionId || this.store.backend !== "online" || this.store.operation.session) return;
    this.store.setOperation("session", "join");
    this.e.sessionResult.textContent = t("session.loading");
    try {
      const loaded = await this.refresh(sessionId, "", { announce: true, activate: true });
      if (!loaded) return;
      this.e.sessionResult.textContent = t("session.joined");
      this.e.joinSessionForm.reset();
      this.store.setSection(this.store.current()?.characters.length ? "game" : "characters");
    } finally {
      this.store.setOperation("session", null);
    }
  }

  async onListClick(event) {
    const button = event.target.closest("[data-session-action]");
    const item = button?.closest("[data-session-id]");
    if (!button || !item) return;
    const session = this.store.sessions.sessions[item.dataset.sessionId];
    if (!session) return;
    const action = button.dataset.sessionAction;
    if (action === "copy") {
      try {
        await copyText(session.session_id);
        this.view.showCopyFeedback("session.copied");
      } catch {
        this.view.showCopyFeedback("session.copyError");
      }
      return;
    }
    if (action === "delete") {
      if (this.store.isBusy()) return;
      this.store.deleteTarget = { ...session };
      this.view.openDeleteDialog(session);
      return;
    }
    if (action !== "open") return;
    if (this.store.isBusy()) return;
    this.store.activateSession(session.session_id);
    if (this.store.backend === "online" && session.connection === "server") {
      await this.refresh(session.session_id, session.session_name, { activate: true });
    }
    this.store.setSection(this.store.current()?.characters.length ? "game" : "characters");
  }

  closeDelete() {
    if (this.store.operation.deleting) return;
    this.store.deleteTarget = null;
    this.e.sessionDeleteDialog.close();
  }

  async confirmDelete() {
    const target = this.store.deleteTarget;
    if (!target || this.store.operation.deleting) return;
    const isActive = this.store.currentSessionId() === target.session_id;
    if (isActive && this.store.isBusy()) {
      this.e.sessionDeleteError.textContent = t("session.deleteBusy");
      return;
    }
    const server = target.connection === "server";
    this.store.setOperation("deleting", true);
    this.view.setDeleteBusy(true);
    this.e.sessionDeleteError.textContent = "";
    try {
      if (server) {
        if (this.store.backend !== "online") throw new ApiError("network_error");
        try { await this.api.deleteSession(target.session_id); }
        catch (error) {
          if (!(error instanceof ApiError) || error.code !== "not_found") throw error;
        }
      }
      this.store.removeSession(target.session_id);
      if (isActive) {
        this.e.actionText.value = "";
        this.diceView.hide();
      }
      this.e.sessionDeleteDialog.close();
      this.store.deleteTarget = null;
      this.store.setSection("session");
      this.e.sessionResult.textContent = t(server ? "session.deleted" : "session.removed", {
        name: target.session_name || t("session.untitled"),
      });
    } catch (error) {
      this.e.sessionDeleteError.textContent = t(
        error instanceof ApiError && error.code === "network_error"
          ? "session.deleteOffline" : "session.deleteError",
      );
    } finally {
      this.store.setOperation("deleting", false);
      this.view.setDeleteBusy(false);
    }
  }
}

export class ShellController {
  constructor({ store, view, api, elements, sessions }) {
    this.store = store;
    this.view = view;
    this.api = api;
    this.e = elements;
    this.sessions = sessions;
  }

  bind() {
    document.addEventListener("click", (event) => {
      const target = event.target.closest("[data-section-target], [data-navigate]");
      const section = target?.dataset.sectionTarget || target?.dataset.navigate;
      if (section) this.navigate(section);
    });
    document.querySelectorAll("[data-language]").forEach((button) => {
      button.addEventListener("click", () => { setLanguage(button.dataset.language); this.store.notify(); });
    });
    this.e.languageCompact.addEventListener("click", () => {
      setLanguage(getLanguage() === "ru" ? "en" : "ru");
      this.store.notify();
    });
    this.e.sidebarToggle.addEventListener("click", () => {
      this.store.setSidebarCollapsed(!this.store.sidebarCollapsed);
    });
    this.e.mobileMenu.addEventListener("click", () => this.openMobile());
    this.e.sidebarBackdrop.addEventListener("click", () => this.closeMobile());
    this.e.hudEventToggle.addEventListener("click", () => {
      this.store.hudExpanded = !this.store.hudExpanded;
      this.store.notify();
    });
    this.e.copySessionId.addEventListener("click", () => this.copyCurrentId());
    for (const button of [this.e.recheckHealth, this.e.serverMini, this.e.globalRetry]) {
      button.addEventListener("click", () => this.checkHealth());
    }
    this.e.syncSession.addEventListener("click", () => this.sync());
  }

  navigate(section) {
    this.store.setSection(section);
    this.closeMobile();
  }

  openMobile() {
    this.e.shell.classList.add("is-mobile-open");
    this.e.mobileMenu.setAttribute("aria-expanded", "true");
  }

  closeMobile() {
    this.e.shell.classList.remove("is-mobile-open");
    this.e.mobileMenu.setAttribute("aria-expanded", "false");
  }

  async copyCurrentId() {
    const id = this.store.currentSessionId();
    if (!id) return;
    try { await copyText(id); this.view.showCopyFeedback("session.copied"); }
    catch { this.view.showCopyFeedback("session.copyError"); }
  }

  async checkHealth() {
    this.store.backend = "checking";
    this.store.notify();
    const result = await this.api.health();
    this.store.backend = result.available ? "online" : "offline";
    const current = this.store.current();
    if (result.available && current?.connection === "server" && !this.store.isBusy()) {
      await this.sessions.refresh(current.session_id, current.session_name);
    }
    this.store.notify();
  }

  async sync() {
    const state = this.store.current();
    if (this.store.backend !== "online" || state?.connection !== "server" || this.store.isBusy()) return;
    this.store.setOperation("sync", true);
    this.store.setFeedback({ kind: "pending", key: "game.syncing" });
    const refreshed = await this.sessions.refresh(state.session_id, state.session_name);
    this.store.setFeedback({
      kind: refreshed ? "success" : "error",
      key: refreshed ? "game.syncComplete" : "game.syncFailed",
    });
    this.store.setOperation("sync", false);
  }
}

export class CharacterController {
  constructor({ store, api, elements, sessions }) {
    this.store = store;
    this.api = api;
    this.e = elements;
    this.sessions = sessions;
  }

  bind() {
    this.e.characterForm.addEventListener("submit", (event) => this.create(event));
    this.e.actingCharacter.addEventListener("change", () => this.store.setSelectedCharacter(this.e.actingCharacter.value));
    this.e.activeHero.addEventListener("click", (event) => this.selectFromCard(event));
    this.e.activeHero.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") this.selectFromCard(event);
    });
    this.e.addAnotherHero.addEventListener("click", () => {
      this.store.showCharacterNext = false;
      this.store.notify();
      this.e.characterName.focus();
    });
    this.e.goToGame.addEventListener("click", () => this.store.setSection("game"));
  }

  readStats() {
    return Object.fromEntries([...this.e.statRows.querySelectorAll("[data-ability]")].map((input) => [
      input.dataset.ability, Number.isInteger(Number(input.value)) ? Number(input.value) : 10,
    ]));
  }

  selectFromCard(event) {
    const card = event.target.closest("[data-character-id]");
    if (!card || this.store.operation.turn !== "idle" || this.store.current()?.pending_turn) return;
    if (event.type === "keydown") event.preventDefault();
    this.store.setSelectedCharacter(card.dataset.characterId);
    this.e.actionText.focus();
  }

  async create(event) {
    event.preventDefault();
    const state = this.store.current();
    const name = this.e.characterName.value.trim();
    const maxHp = Number(this.e.characterHp.value);
    if (!state || state.connection !== "server" || this.store.operation.character) return;
    if (!name || !Number.isInteger(maxHp) || maxHp < 1) {
      this.e.characterMessage.textContent = t("characters.invalid");
      return;
    }
    if (state.characters.some((character) => character.name.trim().toLocaleLowerCase() === name.toLocaleLowerCase())) {
      this.e.characterMessage.textContent = t("characters.duplicate");
      this.e.characterName.focus();
      return;
    }
    this.store.setOperation("character", state.session_id);
    this.e.characterMessage.textContent = t("characters.creating");
    try {
      const snapshot = await this.api.createCharacter(state.session_id, {
        expectedRevision: state.revision,
        name,
        concept: this.e.characterConcept.value.trim(),
        maxHp,
        stats: this.readStats(),
        startingItem: this.e.startingItem.value.trim(),
      });
      const oldIds = new Set(state.characters.map((character) => character.id));
      const created = snapshot.characters.find((character) => !oldIds.has(character.id));
      const activate = this.store.currentSessionId() === state.session_id;
      this.store.putSession(mergeServerState(state, snapshot), activate);
      if (activate && created) this.store.setSelectedCharacter(created.id);
      this.e.characterForm.reset();
      this.e.characterHp.value = "10";
      this.e.characterMessage.textContent = t("characters.created", { name });
      this.store.showCharacterNext = true;
    } catch (error) {
      if (error instanceof ApiError && error.code === "network_error") this.store.backend = "offline";
      if (error instanceof ApiError && error.code === "stale_revision") {
        await this.sessions.refresh(state.session_id, state.session_name);
        this.e.characterMessage.textContent = t("characters.stale");
      } else if (error instanceof ApiError && error.code === "DUPLICATE_CHARACTER_NAME") {
        this.e.characterMessage.textContent = t("characters.duplicate");
      } else {
        this.e.characterMessage.textContent = t("characters.createError");
      }
    } finally {
      this.store.setOperation("character", null);
    }
  }
}

export class TurnController {
  constructor({ store, view, api, elements, diceView, sessions }) {
    this.store = store;
    this.view = view;
    this.api = api;
    this.e = elements;
    this.diceView = diceView;
    this.sessions = sessions;
  }

  bind() {
    this.e.actionForm.addEventListener("submit", (event) => this.submit(event));
    this.e.actionText.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        this.e.actionForm.requestSubmit();
      }
    });
    this.e.rollD20.addEventListener("click", () => this.roll());
    this.e.cancelCheck.addEventListener("click", () => this.cancel());
  }

  canPlay(state) {
    return this.store.backend === "online" && state?.connection === "server" && state.characters.length > 0;
  }

  saveReceipt(sessionId, result, before, after) {
    const items = turnReceipts(result.events, before, after);
    this.store.receipts.set(sessionId, {
      turnNumber: result.turn_number,
      items: items.length ? items : [{ type: "no_change" }],
    });
  }

  finishResult(before, action, result) {
    const next = applyTurnResult(before, action, result);
    const changes = result.accepted ? stateChanges(before, next) : null;
    const activate = this.store.currentSessionId() === before.session_id;
    this.store.putSession({ ...next, pending_turn: null }, activate);
    this.store.syncTimes.set(before.session_id, new Date());
    if (result.accepted) {
      this.saveReceipt(before.session_id, result, before, next);
      const warning = warningMessageKey(result.warnings);
      this.store.setFeedback(warning
        ? { kind: "warning", key: warning }
        : { kind: "success", key: result.check_outcome === "failure" ? "check.turnFailed" : "game.turnAccepted" });
      if (activate) {
        this.e.actionText.value = "";
        queueMicrotask(() => this.view.playStateChanges(changes));
      }
    } else {
      this.store.setFeedback({ kind: "error", key: failureMessageKey({ errors: result.errors }) });
    }
  }

  async submit(event) {
    event.preventDefault();
    const state = this.store.current();
    const text = this.e.actionText.value.trim();
    const characterId = this.e.actingCharacter.value;
    if (!this.canPlay(state) || !characterId || !text || this.store.operation.turn !== "idle" || state.pending_turn) return;
    this.store.pendingAttempt = createTurnAttempt(this.store.pendingAttempt, state, { characterId, text });
    const attempt = this.store.pendingAttempt;
    this.store.setOperation("turn", "planning");
    this.store.setFeedback({ kind: "pending", key: "game.thinkingHint" });
    try {
      const result = await this.api.submitTurn(state.session_id, attempt);
      if (result.status === "awaiting_roll") {
        this.store.putSession(resultState(state, result), this.store.currentSessionId() === state.session_id);
        this.store.pendingAttempt = null;
        this.store.setFeedback({ kind: "pending", key: "check.ready" });
        queueMicrotask(() => this.e.rollD20.focus());
      } else {
        this.store.pendingAttempt = null;
        this.finishResult(state, { characterId, text }, result);
      }
    } catch (error) {
      if (error instanceof ApiError && error.code === "network_error") this.store.backend = "offline";
      if (error instanceof ApiError && error.code === "stale_revision") {
        this.store.pendingAttempt = null;
        const refreshed = await this.sessions.refresh(state.session_id, state.session_name);
        this.store.setFeedback({
          kind: refreshed ? "warning" : "error",
          key: refreshed ? "game.error.staleRefreshed" : "game.error.staleRevision",
        });
      } else {
        this.store.setFeedback({ kind: "error", key: failureMessageKey(error) });
      }
    } finally {
      this.store.setOperation("turn", "idle");
      this.e.log.lastElementChild?.scrollIntoView({ block: "nearest" });
    }
  }

  async roll() {
    const state = this.store.current();
    const pending = state?.pending_turn;
    if (!pending || this.store.operation.turn !== "idle") return;
    this.store.setOperation("turn", "rolling");
    this.store.setFeedback({ kind: "pending", key: "check.waitingForMaster" });
    this.diceView.start();
    try {
      const result = await this.api.rollTurn(state.session_id, pending.action_id, pending.sequence);
      const check = latestCheck(result, pending.sequence);
      if (!check) throw new ApiError("invalid_response");
      this.store.setOperation("turn", "resolving");
      await this.diceView.settle(check);
      if (result.status === "awaiting_roll") {
        this.store.putSession(resultState(state, result), this.store.currentSessionId() === state.session_id);
        this.store.setFeedback({ kind: "pending", key: "check.nextReady" });
        queueMicrotask(() => this.e.rollD20.focus());
      } else {
        this.finishResult(state, { characterId: pending.character_id, text: pending.text }, result);
      }
    } catch (error) {
      this.diceView.hide();
      if (error instanceof ApiError && error.code === "network_error") this.store.backend = "offline";
      this.store.setFeedback({ kind: "error", key: failureMessageKey(error) });
    } finally {
      this.store.setOperation("turn", "idle");
    }
  }

  async cancel() {
    const state = this.store.current();
    const pending = state?.pending_turn;
    if (!pending?.can_cancel || this.store.operation.turn !== "idle") return;
    this.store.setOperation("turn", "cancelling");
    try {
      await this.api.cancelTurn(state.session_id, pending.action_id);
      this.store.putSession({ ...state, pending_turn: null }, this.store.currentSessionId() === state.session_id);
      this.e.actionText.value = pending.text;
      this.store.setFeedback(null);
      this.e.actionText.focus();
    } catch (error) {
      this.store.setFeedback({ kind: "error", key: failureMessageKey(error) });
    } finally {
      this.store.setOperation("turn", "idle");
    }
  }
}
