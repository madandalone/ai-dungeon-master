import { createSession, fetchHealth } from "./api.js";
import { SETTINGS_KEY, applyTranslations, getLanguage, initLanguage, setLanguage, t } from "./i18n.js";
import {
  activateSession, activePublicState, emptyPublicState, loadSessionStore, putSession, saveSessionStore,
} from "./state.js";
import { characterArchetype, healthPercent, healthTone, iconMarkup } from "./visuals.js";

const elements = {
  shell: document.querySelector("#app-shell"), sidebar: document.querySelector("#sidebar"),
  sidebarToggle: document.querySelector("#sidebar-toggle"), mobileMenu: document.querySelector("#mobile-menu"),
  sidebarBackdrop: document.querySelector("#sidebar-backdrop"), sectionTitle: document.querySelector("#section-title"),
  sectionDescription: document.querySelector("#section-description"), sessionChipValue: document.querySelector("#session-chip-value"),
  offlineBanner: document.querySelector("#offline-banner"), globalRetry: document.querySelector("#global-retry"),
  serverMini: document.querySelector("#server-mini"), serverMiniText: document.querySelector("#server-mini-text"),
  connectionState: document.querySelector("#connection-state"), healthStatus: document.querySelector("#health-status"),
  healthDescription: document.querySelector("#health-description"), recheckHealth: document.querySelector("#recheck-health"),
  createSessionForm: document.querySelector("#create-session-form"), sessionName: document.querySelector("#session-name"),
  joinSessionForm: document.querySelector("#join-session-form"), joinSessionId: document.querySelector("#join-session-id"),
  sessionResult: document.querySelector("#session-result"), sessionList: document.querySelector("#session-list"),
  characterForm: document.querySelector("#character-form"), characterName: document.querySelector("#character-name"),
  characterConcept: document.querySelector("#character-concept"), characterHp: document.querySelector("#character-hp"),
  startingItem: document.querySelector("#starting-item"), characterMessage: document.querySelector("#character-message"),
  statRows: document.querySelector("#stat-rows"), addStat: document.querySelector("#add-stat"),
  characterGrid: document.querySelector("#character-grid"), characterCount: document.querySelector("#character-count"),
  actionForm: document.querySelector("#action-form"), actingCharacter: document.querySelector("#acting-character"),
  actionText: document.querySelector("#action-text"), actionHint: document.querySelector("#action-hint"),
  log: document.querySelector("#log"), turnBadge: document.querySelector("#turn-badge"), activeHero: document.querySelector("#active-hero"),
  worldLocation: document.querySelector("#world-location"), worldSessionName: document.querySelector("#world-session-name"),
  worldSessionId: document.querySelector("#world-session-id"), worldCharacterCount: document.querySelector("#world-character-count"),
  worldLogCount: document.querySelector("#world-log-count"), worldParty: document.querySelector("#world-party"),
};

function readUiSettings() {
  try { return JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") || {}; } catch { return {}; }
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

function currentState() { return activePublicState(store); }
function canMutate() { return backendState === "online"; }
function persistStore() { store = saveSessionStore(store); }

function emptyMessage(text) {
  const message = document.createElement("div");
  message.className = "empty-state";
  message.textContent = text;
  return message;
}

function setFormDisabled(form, disabled) {
  form.querySelectorAll("input, textarea, select, button").forEach((control) => { control.disabled = disabled; });
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
    if (active) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current");
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

function renderControls() {
  const state = currentState();
  setFormDisabled(elements.createSessionForm, !canMutate());
  setFormDisabled(elements.joinSessionForm, !canMutate());
  setFormDisabled(elements.characterForm, !canMutate() || !state);
  setFormDisabled(elements.actionForm, !canMutate() || !state || state.characters.length === 0);
  elements.addStat.disabled = !canMutate() || !state;
  if (!state) elements.actionHint.textContent = t("game.noSession");
  else if (!state.characters.length) elements.actionHint.textContent = t("game.noCharacters");
  else if (!canMutate()) elements.actionHint.textContent = t("offline.body");
  else elements.actionHint.textContent = t("game.localHint");
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
  const row = document.createElement("div"); row.className = "health-row";
  const heart = document.createElement("span"); heart.className = "heart"; heart.textContent = "♥"; heart.setAttribute("aria-hidden", "true");
  const track = document.createElement("span"); track.className = "health-track"; track.setAttribute("aria-hidden", "true");
  const fill = document.createElement("span"); fill.className = "health-fill"; fill.style.width = `${healthPercent(character.hp, character.max_hp)}%`; track.append(fill);
  const value = document.createElement("span"); value.className = "health-value"; value.textContent = `${character.hp}/${character.max_hp}`;
  row.append(heart, track, value); widget.append(row); return widget;
}

function characterCard(character, compact = false) {
  const archetype = characterArchetype(character.concept);
  const card = document.createElement("article"); card.className = "character-card";
  card.dataset.archetype = archetype;
  const icon = document.createElement("div"); icon.className = compact ? "mini-icon" : "character-icon"; icon.innerHTML = iconMarkup(archetype);
  const body = document.createElement("div");
  const name = document.createElement("h3"); name.className = "character-name"; name.textContent = character.name;
  const concept = document.createElement("p"); concept.className = "archetype"; concept.textContent = character.concept || t(`archetype.${archetype}`);
  body.append(name, concept, healthWidget(character));
  if (!compact) {
    const stats = document.createElement("ul"); stats.className = "stat-chips";
    const entries = Object.entries(character.stats);
    if (!entries.length) { const item = document.createElement("li"); item.className = "stat-chip"; item.textContent = t("characters.statsEmpty"); stats.append(item); }
    entries.forEach(([stat, value]) => { const item = document.createElement("li"); item.className = "stat-chip"; item.textContent = `${stat}: ${value}`; stats.append(item); });
    const inventoryTitle = document.createElement("p"); inventoryTitle.className = "inventory-title"; inventoryTitle.textContent = t("characters.inventory");
    const inventory = document.createElement("ul"); inventory.className = "inventory-list";
    const items = character.inventory.filter((item) => item.quantity > 0);
    if (!items.length) { const item = document.createElement("li"); item.className = "inventory-item"; item.textContent = t("characters.inventoryEmpty"); inventory.append(item); }
    items.forEach((entry) => { const item = document.createElement("li"); item.className = "inventory-item"; item.textContent = `${entry.name} × ${entry.quantity}`; inventory.append(item); });
    body.append(stats, inventoryTitle, inventory);
  }
  card.append(icon, body); return card;
}

function renderCharacters() {
  const characters = currentState()?.characters || [];
  elements.characterCount.textContent = String(characters.length);
  elements.characterGrid.replaceChildren();
  if (!characters.length) elements.characterGrid.append(emptyMessage(t("characters.empty")));
  else characters.forEach((character) => elements.characterGrid.append(characterCard(character)));

  const previous = selectedCharacterId || elements.actingCharacter.value;
  elements.actingCharacter.replaceChildren();
  if (!characters.length) {
    const option = document.createElement("option"); option.value = ""; option.textContent = t("game.noCharacters"); elements.actingCharacter.append(option);
    selectedCharacterId = "";
  } else {
    characters.forEach((character) => { const option = document.createElement("option"); option.value = character.id; option.textContent = character.name; elements.actingCharacter.append(option); });
    selectedCharacterId = characters.some((character) => character.id === previous) ? previous : characters[0].id;
    elements.actingCharacter.value = selectedCharacterId;
  }
  renderActiveHero();
}

function renderActiveHero() {
  elements.activeHero.replaceChildren();
  const hero = currentState()?.characters.find((character) => character.id === selectedCharacterId);
  if (!hero) { elements.activeHero.append(emptyMessage(t("game.noCharacters"))); return; }
  const wrapper = document.createElement("div"); wrapper.className = "hero-focus"; wrapper.append(characterCard(hero)); elements.activeHero.append(wrapper);
}

function renderLog() {
  const log = currentState()?.log || [];
  elements.turnBadge.textContent = String(log.length);
  elements.turnBadge.setAttribute("aria-label", t("game.turns", { count: log.length }));
  elements.log.replaceChildren();
  if (!log.length) { elements.log.append(emptyMessage(t("game.noLog"))); return; }
  log.forEach((entry) => {
    const item = document.createElement("li"); item.dataset.kind = entry.kind;
    const who = document.createElement("span"); who.className = "who"; who.textContent = entry.kind === "master" ? t("game.master") : characterName(entry.character_id);
    const text = document.createElement("p"); text.textContent = entry.text; item.append(who, text); elements.log.append(item);
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
  if (!state?.characters.length) { elements.worldParty.append(emptyMessage(t("world.empty"))); return; }
  const list = document.createElement("div"); list.className = "world-party-list";
  state.characters.forEach((character) => {
    const row = document.createElement("div"); row.className = "world-party-item";
    const archetype = characterArchetype(character.concept);
    const icon = document.createElement("span"); icon.className = "mini-icon"; icon.innerHTML = iconMarkup(archetype);
    const name = document.createElement("strong"); name.textContent = character.name;
    const hp = document.createElement("span"); hp.textContent = `♥ ${character.hp}/${character.max_hp}`; hp.className = "health-value";
    row.append(icon, name, hp); list.append(row);
  });
  elements.worldParty.append(list);
}

function connectionLabel(connection) {
  return t(connection === "server" ? "session.server" : connection === "legacy" ? "session.legacyStatus" : "session.unverified");
}

function renderSessions() {
  const state = currentState();
  elements.sessionChipValue.textContent = state?.session_name || (state ? t("session.untitled") : t("session.none"));
  elements.sessionList.replaceChildren();
  const sessions = Object.values(store.sessions);
  if (!sessions.length) { elements.sessionList.append(emptyMessage(t("session.savedEmpty"))); return; }
  sessions.forEach((session) => {
    const item = document.createElement("article"); item.className = "session-item"; item.classList.toggle("is-active", session.session_id === store.activeSessionId);
    const text = document.createElement("div");
    const title = document.createElement("strong"); title.textContent = session.session_name || (session.connection === "legacy" ? t("session.legacy") : t("session.untitled"));
    const status = document.createElement("span"); status.textContent = connectionLabel(session.connection); text.append(title, status);
    const button = document.createElement("button"); button.type = "button"; button.className = "secondary-button"; button.textContent = session.session_id === store.activeSessionId ? t("session.active") : t("session.open"); button.disabled = session.session_id === store.activeSessionId;
    button.addEventListener("click", () => { store = activateSession(store, session.session_id); selectedCharacterId = ""; persistStore(); renderAll(); });
    item.append(text, button); elements.sessionList.append(item);
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
    const active = button.dataset.language === getLanguage(); button.classList.toggle("is-active", active); button.setAttribute("aria-pressed", String(active));
  });
  switchSection(currentSection); renderBackend(); renderSessions(); renderCharacters(); renderLog(); renderWorld(); renderControls();
}

function addStatRow(name = "", value = "") {
  const row = document.createElement("div"); row.className = "stat-row";
  const nameInput = document.createElement("input"); nameInput.type = "text"; nameInput.maxLength = 24; nameInput.value = name; nameInput.placeholder = t("characters.statName"); nameInput.setAttribute("aria-label", t("characters.statName"));
  const valueInput = document.createElement("input"); valueInput.type = "number"; valueInput.value = value; valueInput.placeholder = "0"; valueInput.setAttribute("aria-label", t("characters.statValue"));
  const remove = document.createElement("button"); remove.type = "button"; remove.className = "remove-stat"; remove.dataset.i18n = "characters.removeStat"; remove.textContent = t("characters.removeStat"); remove.addEventListener("click", () => row.remove());
  row.append(nameInput, valueInput, remove); elements.statRows.append(row);
}

function readStats() {
  const stats = {};
  elements.statRows.querySelectorAll(".stat-row").forEach((row) => {
    const [nameInput, valueInput] = row.querySelectorAll("input"); const name = nameInput.value.trim(); const value = Number(valueInput.value);
    if (name) stats[name] = Number.isInteger(value) ? value : 0;
  });
  return stats;
}

async function checkHealth() {
  backendState = "checking"; renderBackend(); renderControls();
  const result = await fetchHealth(); backendState = result.available ? "online" : "offline"; renderAll();
}

document.querySelectorAll("[data-section-target]").forEach((button) => button.addEventListener("click", () => switchSection(button.dataset.sectionTarget)));
document.querySelectorAll("[data-language]").forEach((button) => button.addEventListener("click", () => { setLanguage(button.dataset.language); renderAll(); }));

elements.sidebarToggle.addEventListener("click", () => {
  const collapsed = !elements.shell.classList.contains("is-collapsed"); elements.shell.classList.toggle("is-collapsed", collapsed);
  elements.sidebarToggle.setAttribute("aria-expanded", String(!collapsed)); elements.sidebarToggle.querySelector(".sr-only").textContent = t(collapsed ? "nav.expand" : "nav.collapse"); writeUiSettings({ sidebarCollapsed: collapsed });
});
elements.mobileMenu.addEventListener("click", () => { elements.shell.classList.add("is-mobile-open"); elements.mobileMenu.setAttribute("aria-expanded", "true"); });
elements.sidebarBackdrop.addEventListener("click", () => { elements.shell.classList.remove("is-mobile-open"); elements.mobileMenu.setAttribute("aria-expanded", "false"); });
elements.recheckHealth.addEventListener("click", checkHealth); elements.globalRetry.addEventListener("click", checkHealth);

elements.createSessionForm.addEventListener("submit", async (event) => {
  event.preventDefault(); if (!canMutate()) return;
  const name = elements.sessionName.value.trim();
  if (!name) { elements.sessionResult.textContent = t("session.nameRequired"); return; }
  elements.sessionResult.textContent = t("session.creating");
  try {
    const session = await createSession(name); store = putSession(store, emptyPublicState(session.id, session.name, "server")); persistStore(); selectedCharacterId = "";
    elements.sessionResult.textContent = t("session.created", { name: session.name }); elements.createSessionForm.reset(); currentSection = "characters"; renderAll();
  } catch { backendState = "offline"; elements.sessionResult.textContent = t("session.createError"); renderAll(); }
});

elements.joinSessionForm.addEventListener("submit", (event) => {
  event.preventDefault(); if (!canMutate()) return;
  const id = elements.joinSessionId.value.trim(); if (!id) return;
  store = store.sessions[id] ? activateSession(store, id) : putSession(store, emptyPublicState(id, "", "unverified"));
  persistStore(); selectedCharacterId = ""; elements.sessionResult.textContent = t("session.joined"); elements.joinSessionForm.reset(); renderAll();
});

elements.addStat.addEventListener("click", () => addStatRow());
elements.characterForm.addEventListener("submit", (event) => {
  event.preventDefault(); const state = currentState(); if (!canMutate() || !state) return;
  const name = elements.characterName.value.trim(); const concept = elements.characterConcept.value.trim(); const hp = Number(elements.characterHp.value);
  if (!name || !Number.isInteger(hp) || hp < 1) { elements.characterMessage.textContent = t("characters.invalid"); return; }
  const startingItem = elements.startingItem.value.trim();
  const id = globalThis.crypto?.randomUUID?.() || `local-character-${Date.now()}`;
  const character = { id, name, concept, hp, max_hp: hp, stats: readStats(), inventory: startingItem ? [{ item_id: startingItem, name: startingItem, quantity: 1 }] : [] };
  store = putSession(store, { ...state, characters: [...state.characters, character] }); persistStore(); selectedCharacterId = id;
  elements.characterForm.reset(); elements.characterHp.value = "10"; elements.statRows.replaceChildren(); addStatRow(); elements.characterMessage.textContent = t("characters.created", { name }); renderAll();
});

elements.actingCharacter.addEventListener("change", () => { selectedCharacterId = elements.actingCharacter.value; renderActiveHero(); });
elements.actionForm.addEventListener("submit", (event) => {
  event.preventDefault(); const state = currentState(); const text = elements.actionText.value.trim(); const characterId = elements.actingCharacter.value;
  if (!canMutate() || !state || !characterId || !text) return;
  store = putSession(store, { ...state, log: [...state.log, { kind: "player", character_id: characterId, text }, { kind: "master", character_id: "", text: t("game.placeholderReply") }] });
  persistStore(); elements.actionText.value = ""; renderAll(); elements.log.lastElementChild?.scrollIntoView({ block: "nearest" });
});

if (readUiSettings().sidebarCollapsed) elements.shell.classList.add("is-collapsed");
addStatRow(); renderAll(); checkHealth();
