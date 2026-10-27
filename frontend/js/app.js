import { ApiError, createSession, fetchHealth, fetchSessionState, submitTurn } from "./api.js";
import { SETTINGS_KEY, applyTranslations, getLanguage, initLanguage, setLanguage, t } from "./i18n.js";
import {
  activateSession, activePublicState, emptyPublicState, loadSessionStore, putSession, saveSessionStore,
} from "./state.js";
import { characterArchetype, healthPercent, healthTone, iconMarkup } from "./visuals.js";
import {
  applyTurnResult,
  createTurnAttempt,
  failureMessageKey,
  mergeServerState,
  pinnedContext,
  stateChanges,
  warningMessageKey,
} from "./turns.js";

const elements = {
  shell: document.querySelector("#app-shell"),
  sidebar: document.querySelector("#sidebar"),
  sidebarToggle: document.querySelector("#sidebar-toggle"),
  mobileMenu: document.querySelector("#mobile-menu"),
  sidebarBackdrop: document.querySelector("#sidebar-backdrop"),
  sectionTitle: document.querySelector("#section-title"),
  sectionDescription: document.querySelector("#section-description"),
  hudSession: document.querySelector("#hud-session"),
  hudHero: document.querySelector("#hud-hero"),
  hudHeroHp: document.querySelector("#hud-hero-hp"),
  hudEvent: document.querySelector("#hud-event"),
  offlineBanner: document.querySelector("#offline-banner"),
  globalRetry: document.querySelector("#global-retry"),
  serverMini: document.querySelector("#server-mini"),
  serverMiniText: document.querySelector("#server-mini-text"),
  connectionState: document.querySelector("#connection-state"),
  healthStatus: document.querySelector("#health-status"),
  healthDescription: document.querySelector("#health-description"),
  recheckHealth: document.querySelector("#recheck-health"),
  createSessionForm: document.querySelector("#create-session-form"),
  sessionName: document.querySelector("#session-name"),
  joinSessionForm: document.querySelector("#join-session-form"),
  joinSessionId: document.querySelector("#join-session-id"),
  sessionResult: document.querySelector("#session-result"),
  sessionList: document.querySelector("#session-list"),
  serviceGrid: document.querySelector("#service-grid"),
  characterForm: document.querySelector("#character-form"),
  characterName: document.querySelector("#character-name"),
  characterConcept: document.querySelector("#character-concept"),
  characterHp: document.querySelector("#character-hp"),
  startingItem: document.querySelector("#starting-item"),
  characterMessage: document.querySelector("#character-message"),
  characterFormNotice: document.querySelector("#character-form-notice"),
  statRows: document.querySelector("#stat-rows"),
  addStat: document.querySelector("#add-stat"),
  characterGrid: document.querySelector("#character-grid"),
  characterCount: document.querySelector("#character-count"),
  actionForm: document.querySelector("#action-form"),
  actingCharacter: document.querySelector("#acting-character"),
  actionText: document.querySelector("#action-text"),
  actionHint: document.querySelector("#action-hint"),
  actionSubmit: document.querySelector("#action-submit"),
  actionSubmitLabel: document.querySelector("#action-submit-label"),
  actionStatus: document.querySelector("#action-status"),
  log: document.querySelector("#log"),
  turnBadge: document.querySelector("#turn-badge"),
  activeHero: document.querySelector("#active-hero"),
  worldLocation: document.querySelector("#world-location"),
  worldSessionName: document.querySelector("#world-session-name"),
  worldSessionId: document.querySelector("#world-session-id"),
  worldCharacterCount: document.querySelector("#world-character-count"),
  worldLogCount: document.querySelector("#world-log-count"),
  worldParty: document.querySelector("#world-party"),
};

function readUiSettings() {
  try {
    return JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") || {};
  } catch {
    return {};
  }
}

function writeUiSettings(changes) {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify({ ...readUiSettings(), ...changes }));
}

initLanguage();
let store = loadSessionStore();
let currentSection = ["game", "characters", "world", "session"].includes(readUiSettings().activeSection)
  ? readUiSettings().activeSection : "game";
let backendState = "checking";
let selectedCharacterId = "";
let turnPending = false;
let pendingTurnAttempt = null;
let actionFeedback = null;
let componentStates = { backend: "checking", dm: "unknown", rules: "unknown", history: "unknown" };

function currentState() {
  return activePublicState(store);
}

function canReachBackend() {
  return backendState === "online";
}

function canPlay(state = currentState()) {
  return canReachBackend() && state?.connection === "server" && state.characters.length > 0;
}

function persistStore() {
  store = saveSessionStore(store);
}

function emptyMessage(text) {
  const message = document.createElement("div");
  message.className = "empty-state";
  message.textContent = text;
  return message;
}

function setFormDisabled(form, disabled) {
  form.querySelectorAll("input, textarea, select, button").forEach((control) => {
    control.disabled = disabled;
  });
}

function switchSection(section) {
  currentSection = section;
  writeUiSettings({ activeSection: section });
  document.querySelectorAll("[data-view]").forEach((view) => {
    const active = view.dataset.view === section;
    view.hidden = !active;
    view.classList.toggle("is-active", active);
  });
  document.querySelectorAll(".nav-item").forEach((item) => {
    const active = item.dataset.sectionTarget === section;
    item.classList.toggle("is-active", active);
    if (active) {
      item.setAttribute("aria-current", "page");
    } else {
      item.removeAttribute("aria-current");
    }
  });
  elements.sectionTitle.textContent = t(`section.${section}.title`);
  elements.sectionDescription.textContent = t(`section.${section}.description`);
  elements.shell.classList.remove("is-mobile-open");
  elements.mobileMenu.setAttribute("aria-expanded", "false");
}

function renderBackend() {
  const online = backendState === "online";
  const checking = backendState === "checking";
  const key = checking ? "checking" : online ? "online" : "offline";
  elements.serverMini.dataset.state = key;
  elements.serverMiniText.textContent = t(`backend.${key}`);
  elements.connectionState.dataset.state = key;
  elements.healthStatus.textContent = t(`backend.${key}`);
  elements.healthDescription.textContent = t(`backend.${key}Body`);
  elements.offlineBanner.hidden = checking || online;
}

function renderServices() {
  elements.serviceGrid.querySelectorAll("[data-service]").forEach((card) => {
    const state = componentStates[card.dataset.service] || "unknown";
    card.dataset.state = state;
    card.querySelector(".service-state").textContent = t(`services.status.${state}`);
  });
}

function renderPinnedContext() {
  const state = currentState();
  const context = pinnedContext(state, selectedCharacterId);
  elements.hudSession.textContent = context.sessionName || (state ? t("session.untitled") : t("session.none"));
  elements.hudHero.textContent = context.hero?.name || t("context.noHero");
  elements.hudHeroHp.textContent = context.hero ? `♥ ${context.hero.hp}/${context.hero.max_hp}` : "";
  if (!context.lastEvent) {
    elements.hudEvent.textContent = t("context.noEvent");
    elements.hudEvent.removeAttribute("title");
    return;
  }
  const speaker = context.lastEvent.kind === "master"
    ? t("game.master")
    : state.characters.find((character) => character.id === context.lastEvent.character_id)?.name || t("context.heroFallback");
  const summary = t("context.event", { speaker, text: context.lastEvent.text });
  elements.hudEvent.textContent = summary;
  elements.hudEvent.title = summary;
}

function renderControls() {
  const state = currentState();
  setFormDisabled(elements.createSessionForm, !canReachBackend());
  setFormDisabled(elements.joinSessionForm, !canReachBackend());
  setFormDisabled(elements.characterForm, true);
  setFormDisabled(elements.actionForm, !canPlay(state) || turnPending);
  elements.addStat.disabled = true;
  elements.characterFormNotice.textContent = state?.connection === "server"
    ? t("characters.serverManaged") : t("characters.localReadOnly");
  if (!state) elements.actionHint.textContent = t("game.noSession");
  else if (!state.characters.length) elements.actionHint.textContent = t("game.noCharacters");
  else if (!canReachBackend()) elements.actionHint.textContent = t("offline.body");
  else if (state.connection !== "server") elements.actionHint.textContent = t("game.localReadOnly");
  else if (turnPending) elements.actionHint.textContent = t("game.thinkingHint");
  else elements.actionHint.textContent = t("game.readyHint");
  elements.actionForm.setAttribute("aria-busy", String(turnPending));
  elements.actionSubmit.classList.toggle("is-pending", turnPending);
  const submitLabel = turnPending
    ? "game.thinking"
    : pendingTurnAttempt
      ? "game.retryTurn"
      : "game.send";
  elements.actionSubmitLabel.textContent = t(submitLabel);
}

function renderActionFeedback() {
  elements.actionStatus.hidden = !actionFeedback;
  elements.actionStatus.className = `turn-status${actionFeedback ? ` is-${actionFeedback.kind}` : ""}`;
  elements.actionStatus.textContent = actionFeedback ? t(actionFeedback.key) : "";
}

function characterName(characterId) {
  return currentState()?.characters.find((character) => character.id === characterId)?.name || "?";
}

function healthWidget(character) {
  const widget = document.createElement("div");
  widget.className = "health-widget";
  widget.dataset.tone = healthTone(character.hp, character.max_hp);
  widget.setAttribute("role", "progressbar");
  widget.setAttribute("aria-valuemin", "0");
  widget.setAttribute("aria-valuemax", String(character.max_hp));
  widget.setAttribute("aria-valuenow", String(character.hp));
  widget.setAttribute("aria-label", t("characters.hpLabel", { current: character.hp, maximum: character.max_hp }));
  const row = document.createElement("div");
  row.className = "health-row";

  const heart = document.createElement("span");
  heart.className = "heart";
  heart.textContent = "♥";
  heart.setAttribute("aria-hidden", "true");

  const track = document.createElement("span");
  track.className = "health-track";
  track.setAttribute("aria-hidden", "true");

  const fill = document.createElement("span");
  fill.className = "health-fill";
  fill.style.width = `${healthPercent(character.hp, character.max_hp)}%`;
  track.append(fill);

  const value = document.createElement("span");
  value.className = "health-value";
  value.textContent = `${character.hp}/${character.max_hp}`;

  row.append(heart, track, value);
  widget.append(row);
  return widget;
}

function characterCard(character, compact = false) {
  const archetype = characterArchetype(character.concept);
  const card = document.createElement("article");
  card.className = "character-card";
  card.dataset.archetype = archetype;
  card.dataset.characterId = character.id;

  const icon = document.createElement("div");
  icon.className = compact ? "mini-icon" : "character-icon";
  icon.innerHTML = iconMarkup(archetype);

  const body = document.createElement("div");

  const name = document.createElement("h3");
  name.className = "character-name";
  name.textContent = character.name;

  const concept = document.createElement("p");
  concept.className = "archetype";
  concept.textContent = character.concept || t(`archetype.${archetype}`);
  body.append(name, concept, healthWidget(character));

  if (!compact) {
    const stats = document.createElement("ul");
    stats.className = "stat-chips";
    const entries = Object.entries(character.stats);
    if (!entries.length) {
      const item = document.createElement("li");
      item.className = "stat-chip";
      item.textContent = t("characters.statsEmpty");
      stats.append(item);
    }
    entries.forEach(([stat, value]) => {
      const item = document.createElement("li");
      item.className = "stat-chip";
      item.textContent = `${stat}: ${value}`;
      stats.append(item);
    });

    const inventoryTitle = document.createElement("p");
    inventoryTitle.className = "inventory-title";
    inventoryTitle.textContent = t("characters.inventory");

    const inventory = document.createElement("ul");
    inventory.className = "inventory-list";
    const items = character.inventory.filter((item) => item.quantity > 0);
    if (!items.length) {
      const item = document.createElement("li");
      item.className = "inventory-item";
      item.textContent = t("characters.inventoryEmpty");
      inventory.append(item);
    }
    items.forEach((entry) => {
      const item = document.createElement("li");
      item.className = "inventory-item";
      item.textContent = `${entry.name} × ${entry.quantity}`;
      inventory.append(item);
    });
    body.append(stats, inventoryTitle, inventory);
  }
  card.append(icon, body);
  return card;
}

function renderCharacters() {
  const characters = currentState()?.characters || [];
  elements.characterCount.textContent = String(characters.length);
  elements.characterGrid.replaceChildren();
  if (!characters.length) {
    elements.characterGrid.append(emptyMessage(t("characters.empty")));
  } else {
    characters.forEach((character) => elements.characterGrid.append(characterCard(character)));
  }

  const previous = selectedCharacterId || elements.actingCharacter.value;
  elements.actingCharacter.replaceChildren();
  if (!characters.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = t("game.noCharacters");
    elements.actingCharacter.append(option);
    selectedCharacterId = "";
  } else {
    characters.forEach((character) => {
      const option = document.createElement("option");
      option.value = character.id;
      option.textContent = character.name;
      elements.actingCharacter.append(option);
    });
    selectedCharacterId = characters.some((character) => character.id === previous) ? previous : characters[0].id;
    elements.actingCharacter.value = selectedCharacterId;
  }
  renderActiveHero();
}

function renderActiveHero() {
  elements.activeHero.replaceChildren();
  const hero = currentState()?.characters.find((character) => character.id === selectedCharacterId);
  if (!hero) {
    elements.activeHero.append(emptyMessage(t("game.noCharacters")));
    return;
  }
  const wrapper = document.createElement("div");
  wrapper.className = "hero-focus";
  wrapper.append(characterCard(hero));
  elements.activeHero.append(wrapper);
}

function renderLog() {
  const state = currentState();
  const log = state?.log || [];
  const turnNumber = state?.turn_number || 0;
  elements.turnBadge.textContent = String(turnNumber);
  elements.turnBadge.setAttribute("aria-label", t("game.turns", { count: turnNumber }));
  elements.log.replaceChildren();
  if (!log.length) {
    elements.log.append(emptyMessage(t("game.noLog")));
    return;
  }
  log.forEach((entry) => {
    const item = document.createElement("li");
    item.dataset.kind = entry.kind;

    const who = document.createElement("span");
    who.className = "who";
    who.textContent = entry.kind === "master" ? t("game.master") : characterName(entry.character_id);

    const text = document.createElement("p");
    text.textContent = entry.text;
    item.append(who, text);
    elements.log.append(item);
  });
}

function renderWorld() {
  const state = currentState();
  elements.worldLocation.textContent = state?.location || t("world.unknown");
  elements.worldSessionName.textContent = state?.session_name || (state ? t("session.untitled") : t("session.none"));
  elements.worldSessionId.textContent = state?.session_id || "—";
  elements.worldCharacterCount.textContent = String(state?.characters.length || 0);
  elements.worldLogCount.textContent = String(state?.log.length || 0);
  elements.worldParty.replaceChildren();
  if (!state?.characters.length) {
    elements.worldParty.append(emptyMessage(t("world.empty")));
    return;
  }

  const list = document.createElement("div");
  list.className = "world-party-list";
  state.characters.forEach((character) => {
    const row = document.createElement("div");
    row.className = "world-party-item";
    const archetype = characterArchetype(character.concept);

    const icon = document.createElement("span");
    icon.className = "mini-icon";
    icon.innerHTML = iconMarkup(archetype);

    const name = document.createElement("strong");
    name.textContent = character.name;

    const hp = document.createElement("span");
    hp.textContent = `♥ ${character.hp}/${character.max_hp}`;
    hp.className = "health-value";

    row.append(icon, name, hp);
    list.append(row);
  });
  elements.worldParty.append(list);
}

function connectionLabel(connection) {
  return t(connection === "server" ? "session.server" : connection === "legacy" ? "session.legacyStatus" : "session.unverified");
}

function renderSessions() {
  const state = currentState();
  elements.sessionList.replaceChildren();
  const sessions = Object.values(store.sessions);
  if (!sessions.length) {
    elements.sessionList.append(emptyMessage(t("session.savedEmpty")));
    return;
  }
  sessions.forEach((session) => {
    const item = document.createElement("article");
    item.className = "session-item";
    item.classList.toggle("is-active", session.session_id === store.activeSessionId);

    const text = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = session.session_name
      || (session.connection === "legacy" ? t("session.legacy") : t("session.untitled"));

    const status = document.createElement("span");
    status.textContent = connectionLabel(session.connection);
    text.append(title, status);

    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary-button";
    button.textContent = session.session_id === store.activeSessionId
      ? t("session.active")
      : t("session.open");
    button.disabled = session.session_id === store.activeSessionId;
    button.addEventListener("click", async () => {
      store = activateSession(store, session.session_id);
      selectedCharacterId = "";
      pendingTurnAttempt = null;
      persistStore();
      renderAll();
      if (canReachBackend() && session.connection === "server") {
        await refreshSession(session.session_id, session.session_name);
      }
    });
    item.append(text, button);
    elements.sessionList.append(item);
  });
}

function renderAll() {
  applyTranslations();
  elements.statRows.querySelectorAll(".stat-row").forEach((row) => {
    const [nameInput, valueInput] = row.querySelectorAll("input");
    nameInput.placeholder = t("characters.statName");
    nameInput.setAttribute("aria-label", t("characters.statName"));
    valueInput.setAttribute("aria-label", t("characters.statValue"));
  });
  document.querySelectorAll("[data-language]").forEach((button) => {
    const active = button.dataset.language === getLanguage();
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  switchSection(currentSection);
  renderBackend();
  renderServices();
  renderSessions();
  renderCharacters();
  renderPinnedContext();
  renderLog();
  renderWorld();
  renderControls();
  renderActionFeedback();
}

function addStatRow(name = "", value = "") {
  const row = document.createElement("div");
  row.className = "stat-row";

  const nameInput = document.createElement("input");
  nameInput.type = "text";
  nameInput.maxLength = 24;
  nameInput.value = name;
  nameInput.placeholder = t("characters.statName");
  nameInput.setAttribute("aria-label", t("characters.statName"));

  const valueInput = document.createElement("input");
  valueInput.type = "number";
  valueInput.value = value;
  valueInput.placeholder = "0";
  valueInput.setAttribute("aria-label", t("characters.statValue"));

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "remove-stat";
  remove.dataset.i18n = "characters.removeStat";
  remove.textContent = t("characters.removeStat");
  remove.addEventListener("click", () => row.remove());

  row.append(nameInput, valueInput, remove);
  elements.statRows.append(row);
}

function readStats() {
  const stats = {};
  elements.statRows.querySelectorAll(".stat-row").forEach((row) => {
    const [nameInput, valueInput] = row.querySelectorAll("input");
    const name = nameInput.value.trim();
    const value = Number(valueInput.value);
    if (name) stats[name] = Number.isInteger(value) ? value : 0;
  });
  return stats;
}

function sessionErrorKey(error) {
  if (error?.code === "not_found") return "session.notFound";
  if (error?.code === "network_error") return "session.networkError";
  if (error?.code === "invalid_response") return "session.invalidResponse";
  return "session.loadError";
}

async function refreshSession(sessionId, fallbackName = "", announceError = false) {
  try {
    const snapshot = await fetchSessionState(sessionId);
    const previous = store.sessions[sessionId];
    const merged = mergeServerState(previous, snapshot);
    store = putSession(store, {
      ...merged,
      session_name: merged.session_name || fallbackName || previous?.session_name || "",
    });
    selectedCharacterId = merged.characters.some((character) => character.id === selectedCharacterId)
      ? selectedCharacterId : "";
    persistStore();
    renderAll();
    return true;
  } catch (error) {
    if (error instanceof ApiError && error.code === "network_error") {
      backendState = "offline";
      componentStates = { ...componentStates, backend: "offline" };
    }
    if (announceError) elements.sessionResult.textContent = t(sessionErrorKey(error));
    renderAll();
    return false;
  }
}

function playStateChanges(changes) {
  if (changes.location) {
    const locationCard = elements.worldLocation.closest(".location-card");
    locationCard?.classList.add("is-location-changed");
    setTimeout(() => locationCard?.classList.remove("is-location-changed"), 900);
  }
  document.querySelectorAll(".character-card[data-character-id]").forEach((card) => {
    const change = changes.characters[card.dataset.characterId];
    if (!change) return;
    if (change.hp) card.classList.add("is-hp-changed");
    if (change.inventory) card.classList.add("is-inventory-changed");
    setTimeout(() => card.classList.remove("is-hp-changed", "is-inventory-changed"), 900);
  });
}

async function checkHealth() {
  backendState = "checking";
  componentStates = { ...componentStates, backend: "checking" };
  renderBackend(); renderServices(); renderControls();
  const result = await fetchHealth(); backendState = result.available ? "online" : "offline";
  componentStates = result.components;
  const state = currentState();
  if (result.available && state?.connection === "server") {
    await refreshSession(state.session_id, state.session_name);
  } else {
    renderAll();
  }
}

document.querySelectorAll("[data-section-target]").forEach((button) => button.addEventListener("click", () => switchSection(button.dataset.sectionTarget)));
document.querySelectorAll("[data-language]").forEach((button) => {
  button.addEventListener("click", () => {
    setLanguage(button.dataset.language);
    renderAll();
  });
});

elements.sidebarToggle.addEventListener("click", () => {
  const collapsed = !elements.shell.classList.contains("is-collapsed");
  elements.shell.classList.toggle("is-collapsed", collapsed);
  elements.sidebarToggle.setAttribute("aria-expanded", String(!collapsed));
  elements.sidebarToggle.querySelector(".sr-only").textContent = t(collapsed ? "nav.expand" : "nav.collapse");
  writeUiSettings({ sidebarCollapsed: collapsed });
});
elements.mobileMenu.addEventListener("click", () => {
  elements.shell.classList.add("is-mobile-open");
  elements.mobileMenu.setAttribute("aria-expanded", "true");
});
elements.sidebarBackdrop.addEventListener("click", () => {
  elements.shell.classList.remove("is-mobile-open");
  elements.mobileMenu.setAttribute("aria-expanded", "false");
});
elements.recheckHealth.addEventListener("click", checkHealth);
elements.globalRetry.addEventListener("click", checkHealth);

elements.createSessionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!canReachBackend()) return;

  const name = elements.sessionName.value.trim();
  if (!name) {
    elements.sessionResult.textContent = t("session.nameRequired");
    return;
  }
  elements.sessionResult.textContent = t("session.creating");
  try {
    const session = await createSession(name);
    store = putSession(store, emptyPublicState(session.id, session.name, "server"));
    selectedCharacterId = "";
    pendingTurnAttempt = null;
    persistStore();
    const loaded = await refreshSession(session.id, session.name, true);
    if (!loaded) return;
    elements.sessionResult.textContent = t("session.created", { name: session.name });
    elements.createSessionForm.reset();
    currentSection = "game";
    renderAll();
  } catch (error) {
    if (error instanceof ApiError && error.code === "network_error") {
      backendState = "offline";
      componentStates = { ...componentStates, backend: "offline" };
    }
    elements.sessionResult.textContent = t(sessionErrorKey(error));
    renderAll();
  }
});

elements.joinSessionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!canReachBackend()) return;

  const id = elements.joinSessionId.value.trim();
  if (!id) return;

  elements.sessionResult.textContent = t("session.loading");
  const loaded = await refreshSession(id, "", true);
  if (!loaded) return;
  selectedCharacterId = "";
  pendingTurnAttempt = null;
  elements.sessionResult.textContent = t("session.joined");
  elements.joinSessionForm.reset();
  currentSection = "game";
  renderAll();
});

elements.addStat.addEventListener("click", () => addStatRow());
elements.characterForm.addEventListener("submit", (event) => {
  event.preventDefault();
  elements.characterMessage.textContent = t("characters.serverManaged");
});

elements.actingCharacter.addEventListener("change", () => {
  selectedCharacterId = elements.actingCharacter.value;
  renderActiveHero();
  renderPinnedContext();
});
elements.actionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const state = currentState();
  const text = elements.actionText.value.trim();
  const characterId = elements.actingCharacter.value;
  if (!canPlay(state) || !characterId || !text || turnPending) return;

  pendingTurnAttempt = createTurnAttempt(
    pendingTurnAttempt,
    state,
    { characterId, text },
  );
  turnPending = true;
  actionFeedback = { kind: "pending", key: "game.thinkingHint" };
  renderAll();
  let changes = null;
  let scrollToLatest = false;
  try {
    const result = await submitTurn(state.session_id, pendingTurnAttempt);
    const next = applyTurnResult(state, { characterId, text }, result);
    changes = result.accepted ? stateChanges(state, next) : null;
    store = putSession(store, next);
    pendingTurnAttempt = null;
    persistStore();
    if (result.accepted) {
      elements.actionText.value = "";
      const warningKey = warningMessageKey(result.warnings);
      actionFeedback = warningKey
        ? { kind: "warning", key: warningKey }
        : { kind: "success", key: "game.turnAccepted" };
      if (result.warnings.some((warning) => warning.code === "RULES_UNAVAILABLE")) {
        componentStates = { ...componentStates, rules: "degraded" };
      }
      scrollToLatest = true;
    } else {
      actionFeedback = { kind: "error", key: failureMessageKey({ errors: result.errors }) };
    }
  } catch (error) {
    if (error instanceof ApiError && error.code === "network_error") {
      backendState = "offline";
      componentStates = { ...componentStates, backend: "offline" };
    }
    if (error instanceof ApiError && error.code === "stale_revision") {
      pendingTurnAttempt = null;
      const refreshed = await refreshSession(state.session_id, state.session_name);
      actionFeedback = {
        kind: refreshed ? "warning" : "error",
        key: refreshed ? "game.error.staleRefreshed" : "game.error.staleRevision",
      };
    } else {
      actionFeedback = { kind: "error", key: failureMessageKey(error) };
    }
  } finally {
    turnPending = false;
    renderAll();
    if (changes) playStateChanges(changes);
    if (scrollToLatest) elements.log.lastElementChild?.scrollIntoView({ block: "nearest" });
  }
});

if (readUiSettings().sidebarCollapsed) elements.shell.classList.add("is-collapsed");
addStatRow();
renderAll();
checkHealth();
