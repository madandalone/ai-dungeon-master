import {
  ApiError, cancelTurn, createCharacter, createSession, fetchHealth, fetchSessionState,
  rollTurn, submitTurn,
} from "./api.js";
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
  turnReceipts,
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
  revisionBadge: document.querySelector("#revision-badge"),
  syncSession: document.querySelector("#sync-session"),
  syncStatus: document.querySelector("#sync-status"),
  turnReceipt: document.querySelector("#turn-receipt"),
  receiptTurn: document.querySelector("#receipt-turn"),
  receiptList: document.querySelector("#receipt-list"),
  skillCheckCard: document.querySelector("#skill-check-card"),
  skillCheckTitle: document.querySelector("#skill-check-title"),
  skillCheckReason: document.querySelector("#skill-check-reason"),
  skillCheckFormula: document.querySelector("#skill-check-formula"),
  skillCheckChance: document.querySelector("#skill-check-chance"),
  rollD20: document.querySelector("#roll-d20"),
  cancelCheck: document.querySelector("#cancel-check"),
  diceOverlay: document.querySelector("#dice-overlay"),
  diceTitle: document.querySelector("#dice-title"),
  d20: document.querySelector("#d20"),
  d20Value: document.querySelector("#d20-value"),
  diceFormula: document.querySelector("#dice-formula"),
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
let characterPending = false;
let turnPending = false;
let syncPending = false;
let pendingTurnAttempt = null;
let actionFeedback = null;
let componentStates = { backend: "checking", dm: "unknown", rules: "unknown", history: "unknown" };
const syncTimes = new Map();
const receiptsBySession = new Map();
const ABILITY_KEYS = new Set([
  "strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma",
]);

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
  const canCreateCharacter = canReachBackend() && state?.connection === "server" && !characterPending;
  setFormDisabled(elements.characterForm, !canCreateCharacter);
  setFormDisabled(elements.actionForm, !canPlay(state) || turnPending || Boolean(state?.pending_turn));
  elements.characterFormNotice.textContent = characterPending
    ? t("characters.creating")
    : canCreateCharacter
      ? t("characters.ready")
      : t("characters.localReadOnly");
  if (!state) elements.actionHint.textContent = t("game.noSession");
  else if (!state.characters.length) elements.actionHint.textContent = t("game.noCharacters");
  else if (!canReachBackend()) elements.actionHint.textContent = t("offline.body");
  else if (state.connection !== "server") elements.actionHint.textContent = t("game.localReadOnly");
  else if (turnPending) elements.actionHint.textContent = t("game.thinkingHint");
  else if (state.pending_turn) elements.actionHint.textContent = t("check.pendingHint");
  else elements.actionHint.textContent = t("game.readyHint");
  elements.actionForm.setAttribute("aria-busy", String(turnPending));
  elements.actionSubmit.classList.toggle("is-pending", turnPending);
  elements.syncSession.disabled = !canReachBackend()
    || state?.connection !== "server"
    || turnPending
    || syncPending;
  elements.syncSession.setAttribute("aria-busy", String(syncPending));
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
      item.textContent = `${ABILITY_KEYS.has(stat) ? abilityLabel(stat) : stat}: ${value}`;
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
  elements.revisionBadge.textContent = state ? `R${state.revision}` : "R—";
  elements.revisionBadge.setAttribute("aria-label", t("game.revision", { count: state?.revision ?? 0 }));
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

function renderSyncStatus() {
  const state = currentState();
  if (syncPending) {
    elements.syncStatus.textContent = t("game.syncing");
    return;
  }
  if (!state || state.connection !== "server") {
    elements.syncStatus.textContent = t("game.syncUnavailable");
    return;
  }
  const syncedAt = syncTimes.get(state.session_id);
  if (!syncedAt) {
    elements.syncStatus.textContent = t("game.syncNeeded");
    return;
  }
  const locale = getLanguage() === "ru" ? "ru-RU" : "en-US";
  const time = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit" }).format(syncedAt);
  elements.syncStatus.textContent = t("game.syncedAt", { time });
}

function signed(value) {
  return value > 0 ? `+${value}` : String(value);
}

function receiptText(receipt) {
  const hero = receipt.characterId ? characterName(receipt.characterId) : "";
  if (receipt.type === "health_changed") {
    return t("game.receipt.health", { hero, delta: signed(receipt.delta), value: receipt.value });
  }
  if (receipt.type === "item_added") {
    return t("game.receipt.itemAdded", { hero, item: receipt.item, quantity: receipt.quantity });
  }
  if (receipt.type === "item_removed") {
    return t("game.receipt.itemRemoved", { hero, item: receipt.item, quantity: receipt.quantity });
  }
  if (receipt.type === "location_changed") {
    return t("game.receipt.location", { location: receipt.value });
  }
  if (receipt.type === "stat_changed") {
    return t("game.receipt.stat", {
      hero,
      stat: receipt.stat,
      delta: signed(receipt.delta),
      value: receipt.value,
    });
  }
  if (receipt.type === "skill_check_resolved") {
    const result = t(receipt.success ? "game.receipt.success" : "game.receipt.failure");
    return t("game.receipt.check", {
      hero,
      stat: abilityLabel(receipt.ability),
      total: receipt.total,
      difficulty: receipt.difficulty,
      result,
    });
  }
  return t("game.receipt.noChange");
}

function receiptIcon(type) {
  const icons = {
    health_changed: "♥",
    item_added: "+",
    item_removed: "−",
    location_changed: "✦",
    stat_changed: "↑",
    skill_check_resolved: "◆",
    no_change: "·",
  };
  return icons[type] || "·";
}

function renderTurnReceipt() {
  const state = currentState();
  const receipt = state ? receiptsBySession.get(state.session_id) : null;
  elements.turnReceipt.hidden = !receipt;
  elements.receiptList.replaceChildren();
  if (!receipt) return;

  elements.receiptTurn.textContent = t("game.turns", { count: receipt.turnNumber });
  receipt.items.forEach((entry) => {
    const item = document.createElement("li");
    item.dataset.type = entry.type;

    const icon = document.createElement("span");
    icon.className = "receipt-icon";
    icon.textContent = receiptIcon(entry.type);
    icon.setAttribute("aria-hidden", "true");

    const text = document.createElement("span");
    text.textContent = receiptText(entry);
    item.append(icon, text);
    elements.receiptList.append(item);
  });
}

function abilityLabel(ability) {
  return t(`ability.${ability}`);
}

function signedModifier(value) {
  return value >= 0 ? `+${value}` : String(value);
}

function renderPendingCheck() {
  const pending = currentState()?.pending_turn;
  elements.skillCheckCard.hidden = !pending;
  if (!pending) return;
  elements.skillCheckTitle.textContent = t("check.title", {
    ability: abilityLabel(pending.ability),
    current: pending.sequence,
    maximum: pending.maximum,
  });
  elements.skillCheckReason.textContent = pending.reason;
  elements.skillCheckFormula.textContent = `d20 ${signedModifier(pending.modifier)} vs DC ${pending.difficulty}`;
  elements.skillCheckChance.textContent = t("check.chance", { chance: pending.chance });
  elements.rollD20.disabled = turnPending;
  elements.cancelCheck.hidden = !pending.can_cancel;
  elements.cancelCheck.disabled = turnPending;
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
  renderSyncStatus();
  renderTurnReceipt();
  renderPendingCheck();
  renderWorld();
  renderControls();
  renderActionFeedback();
}

function readStats() {
  const stats = {};
  elements.statRows.querySelectorAll("[data-ability]").forEach((input) => {
    const value = Number(input.value);
    stats[input.dataset.ability] = Number.isInteger(value) ? value : 10;
  });
  return stats;
}

const PIXEL_DIGITS = {
  "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
  "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
  "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
  "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
  "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
  "5": ["11111", "10000", "10000", "11110", "00001", "00001", "11110"],
  "6": ["01110", "10000", "10000", "11110", "10001", "10001", "01110"],
  "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
  "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
  "9": ["01110", "10001", "10001", "01111", "00001", "00001", "01110"],
  "?": ["01110", "10001", "00001", "00010", "00100", "00000", "00100"],
};

function renderDiceValue(value) {
  const text = String(value);
  const digits = [...text].map((character) => {
    const digit = document.createElement("span");
    digit.className = "pixel-digit";
    (PIXEL_DIGITS[character] || PIXEL_DIGITS["?"]).join("").split("").forEach((bit) => {
      const pixel = document.createElement("span");
      pixel.className = `pixel-cell${bit === "1" ? " is-lit" : ""}`;
      digit.append(pixel);
    });
    return digit;
  });
  elements.d20Value.setAttribute("aria-label", text);
  elements.d20Value.replaceChildren(...digits);
}

let diceTicker = null;

function startDiceAnimation() {
  const reduced = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
  elements.diceOverlay.hidden = false;
  elements.diceFormula.textContent = t("check.waitingForMaster");
  elements.diceTitle.textContent = t("check.rolling");
  elements.d20.className = `d20${reduced ? "" : " is-rolling"}`;
  renderDiceValue("?");
  if (!reduced) {
    diceTicker = globalThis.setInterval(() => {
      renderDiceValue(1 + Math.floor(Math.random() * 20));
    }, 90);
  }
}

function stopDiceAnimation() {
  if (diceTicker !== null) globalThis.clearInterval(diceTicker);
  diceTicker = null;
}

async function settleDiceAnimation(check) {
  stopDiceAnimation();
  renderDiceValue(check.roll);
  elements.d20.className = `d20 is-settling ${check.success ? "is-success" : "is-failure"}`;
  elements.diceTitle.textContent = t(check.success ? "check.success" : "check.failure");
  elements.diceFormula.textContent = `${check.roll} ${signedModifier(check.modifier)} = ${check.total} vs DC ${check.difficulty}`;
  await new Promise((resolve) => globalThis.setTimeout(resolve, 850));
  elements.diceOverlay.hidden = true;
  elements.d20.className = "d20";
}

function latestCheck(result, sequence) {
  const checks = result.status === "awaiting_roll"
    ? result.pending_turn.resolved_checks
    : result.events.filter((event) => event.type === "skill_check_resolved");
  return checks.find((check) => check.sequence === sequence - 1) || checks.at(-1);
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
    syncTimes.set(sessionId, new Date());
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
elements.syncSession.addEventListener("click", async () => {
  const state = currentState();
  if (!canReachBackend() || state?.connection !== "server" || turnPending || syncPending) return;

  syncPending = true;
  actionFeedback = { kind: "pending", key: "game.syncing" };
  renderAll();
  const refreshed = await refreshSession(state.session_id, state.session_name);
  syncPending = false;
  actionFeedback = refreshed
    ? { kind: "success", key: "game.syncComplete" }
    : { kind: "error", key: "game.syncFailed" };
  renderAll();
});

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

elements.characterForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const state = currentState();
  const name = elements.characterName.value.trim();
  const maxHp = Number(elements.characterHp.value);
  if (!state || state.connection !== "server" || characterPending) return;
  if (!name || !Number.isInteger(maxHp) || maxHp < 1) {
    elements.characterMessage.textContent = t("characters.invalid");
    return;
  }

  characterPending = true;
  elements.characterMessage.textContent = t("characters.creating");
  renderControls();
  try {
    const snapshot = await createCharacter(state.session_id, {
      expectedRevision: state.revision,
      name,
      concept: elements.characterConcept.value.trim(),
      maxHp,
      stats: readStats(),
      startingItem: elements.startingItem.value.trim(),
    });
    const previousIds = new Set(state.characters.map((character) => character.id));
    const created = snapshot.characters.find((character) => !previousIds.has(character.id));
    store = putSession(store, mergeServerState(state, snapshot));
    selectedCharacterId = created?.id || selectedCharacterId;
    persistStore();
    elements.characterForm.reset();
    elements.characterHp.value = "10";
    elements.characterMessage.textContent = t("characters.created", { name });
  } catch (error) {
    if (error instanceof ApiError && error.code === "network_error") {
      backendState = "offline";
      componentStates = { ...componentStates, backend: "offline" };
    }
    if (error instanceof ApiError && error.code === "stale_revision") {
      await refreshSession(state.session_id, state.session_name);
      elements.characterMessage.textContent = t("characters.stale");
    } else {
      elements.characterMessage.textContent = t("characters.createError");
    }
  } finally {
    characterPending = false;
    renderAll();
  }
});

elements.actingCharacter.addEventListener("change", () => {
  selectedCharacterId = elements.actingCharacter.value;
  renderActiveHero();
  renderPinnedContext();
});

elements.rollD20.addEventListener("click", async () => {
  const state = currentState();
  const pending = state?.pending_turn;
  if (!pending || turnPending) return;
  turnPending = true;
  actionFeedback = { kind: "pending", key: "check.waitingForMaster" };
  startDiceAnimation();
  renderControls();
  try {
    const result = await rollTurn(state.session_id, pending.action_id);
    const check = latestCheck(result, pending.sequence);
    if (!check) throw new ApiError("invalid_response");
    await settleDiceAnimation(check);

    if (result.status === "awaiting_roll") {
      store = putSession(store, {
        ...mergeServerState(state, result.state, result),
        pending_turn: result.pending_turn,
      });
      actionFeedback = { kind: "pending", key: "check.nextReady" };
      persistStore();
      return;
    }

    const action = { characterId: pending.character_id, text: pending.text };
    const next = applyTurnResult(state, action, result);
    const changes = result.accepted ? stateChanges(state, next) : null;
    store = putSession(store, { ...next, pending_turn: null });
    persistStore();
    if (result.accepted) {
      const items = turnReceipts(result.events, state, next);
      receiptsBySession.set(state.session_id, {
        turnNumber: result.turn_number,
        items: items.length ? items : [{ type: "no_change" }],
      });
      elements.actionText.value = "";
      const warningKey = warningMessageKey(result.warnings);
      actionFeedback = warningKey
        ? { kind: "warning", key: warningKey }
        : { kind: "success", key: result.check_outcome === "failure" ? "check.turnFailed" : "game.turnAccepted" };
      if (changes) playStateChanges(changes);
    } else {
      actionFeedback = { kind: "error", key: failureMessageKey({ errors: result.errors }) };
    }
  } catch (error) {
    stopDiceAnimation();
    elements.diceOverlay.hidden = true;
    if (error instanceof ApiError && error.code === "network_error") {
      backendState = "offline";
      componentStates = { ...componentStates, backend: "offline" };
    }
    actionFeedback = { kind: "error", key: failureMessageKey(error) };
  } finally {
    turnPending = false;
    renderAll();
  }
});

elements.cancelCheck.addEventListener("click", async () => {
  const state = currentState();
  const pending = state?.pending_turn;
  if (!pending || !pending.can_cancel || turnPending) return;
  turnPending = true;
  renderControls();
  try {
    await cancelTurn(state.session_id, pending.action_id);
    store = putSession(store, { ...state, pending_turn: null });
    elements.actionText.value = pending.text;
    actionFeedback = null;
    persistStore();
  } catch (error) {
    actionFeedback = { kind: "error", key: failureMessageKey(error) };
  } finally {
    turnPending = false;
    renderAll();
  }
});

elements.actionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const state = currentState();
  const text = elements.actionText.value.trim();
  const characterId = elements.actingCharacter.value;
  if (!canPlay(state) || !characterId || !text || turnPending || state.pending_turn) return;

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
    if (result.status === "awaiting_roll") {
      const waiting = {
        ...mergeServerState(state, result.state, result),
        pending_turn: result.pending_turn,
      };
      store = putSession(store, waiting);
      pendingTurnAttempt = null;
      persistStore();
      actionFeedback = { kind: "pending", key: "check.ready" };
      return;
    }
    const next = applyTurnResult(state, { characterId, text }, result);
    changes = result.accepted ? stateChanges(state, next) : null;
    store = putSession(store, next);
    syncTimes.set(state.session_id, new Date());
    pendingTurnAttempt = null;
    persistStore();
    if (result.accepted) {
      const items = turnReceipts(result.events, state, next);
      receiptsBySession.set(state.session_id, {
        turnNumber: result.turn_number,
        items: items.length ? items : [{ type: "no_change" }],
      });
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
renderAll();
checkHealth();
