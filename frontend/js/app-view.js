import { applyTranslations, getLanguage, t } from "./i18n.js?v=20261124-2";
import { characterArchetype, healthPercent, healthTone, iconMarkup, orderCharactersByActive } from "./visuals.js?v=20261124-2";
import { characterName, signed } from "./format.js?v=20261124-2";

const ABILITIES = new Set([
  "strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma",
]);

function setFormDisabled(form, disabled) {
  form?.querySelectorAll("input, textarea, select, button").forEach((control) => {
    control.disabled = disabled;
  });
}

function emptyMessage(text, action = null) {
  const message = document.createElement("div");
  message.className = "empty-state";
  const copy = document.createElement("p");
  copy.textContent = text;
  message.append(copy);
  if (action) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary-button";
    button.dataset.navigate = action.section;
    button.textContent = t(action.label);
    message.append(button);
  }
  return message;
}

export class AppView {
  constructor(elements, store) {
    this.e = elements;
    this.store = store;
    this.copyTimer = null;
    this.language = null;
    this.views = [...document.querySelectorAll("[data-view]")];
    this.navigation = [...document.querySelectorAll(".nav-item")];
    this.languageButtons = [...document.querySelectorAll("[data-language]")];
  }

  render() {
    if (this.language !== getLanguage()) {
      this.language = getLanguage();
      applyTranslations();
    }
    this.renderNavigation();
    this.renderBackend();
    this.renderSessionContext();
    this.renderPinnedContext();
    this.renderControls();
    this.renderFeedback();
    if (this.store.section === "game") this.renderGame();
    if (this.store.section === "characters") this.renderCharacters();
    if (this.store.section === "world") this.renderWorld();
    if (this.store.section === "session") this.renderSessions();
  }

  renderNavigation() {
    this.views.forEach((view) => {
      const active = view.dataset.view === this.store.section;
      view.hidden = !active;
      view.classList.toggle("is-active", active);
    });
    this.navigation.forEach((item) => {
      const active = item.dataset.sectionTarget === this.store.section;
      item.classList.toggle("is-active", active);
      active ? item.setAttribute("aria-current", "page") : item.removeAttribute("aria-current");
    });
    this.languageButtons.forEach((button) => {
      const active = button.dataset.language === getLanguage();
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    this.e.sectionTitle.textContent = t(`section.${this.store.section}.title`);
    this.e.sectionDescription.textContent = t(`section.${this.store.section}.description`);
    this.e.shell.classList.toggle("is-collapsed", this.store.sidebarCollapsed);
    this.e.sidebarToggle.setAttribute("aria-expanded", String(!this.store.sidebarCollapsed));
    this.e.sidebarToggle.querySelector(".sr-only").textContent = t(
      this.store.sidebarCollapsed ? "nav.expand" : "nav.collapse",
    );
    this.e.languageCompact.textContent = getLanguage().toUpperCase();
  }

  renderBackend() {
    const key = this.store.backend;
    const online = key === "online";
    for (const element of [this.e.serverMini, this.e.connectionState]) element.dataset.state = key;
    this.e.serverMiniText.textContent = t(`backend.${key}`);
    this.e.serverMini.title = t(`backend.${key}`);
    this.e.serverMini.setAttribute("aria-label", t(`backend.${key}`));
    this.e.healthStatus.textContent = t(`backend.${key}`);
    this.e.healthDescription.textContent = t(`backend.${key}Body`);
    this.e.offlineBanner.hidden = key === "checking" || online;
  }

  renderSessionContext() {
    const state = this.store.current();
    this.e.headerSessionName.textContent = state?.session_name || (state ? t("session.untitled") : t("session.none"));
    this.e.headerSessionName.title = state?.session_name || "";
    this.e.copySessionId.disabled = !state?.session_id;
    this.e.copySessionId.title = state?.session_id || "";
    this.e.copyFeedback.textContent = this.store.copyFeedback ? t(this.store.copyFeedback) : "";
  }

  renderPinnedContext() {
    const state = this.store.current();
    const selected = this.store.selectedCharacterId(state);
    const hero = state?.characters.find((entry) => entry.id === selected) || state?.characters[0];
    const lastEvent = state?.log.at(-1);
    this.e.hudHero.textContent = hero?.name || t("context.noHero");
    this.e.hudHeroHp.textContent = hero ? `♥ ${hero.hp}/${hero.max_hp}` : "";
    this.e.hudEventToggle.classList.toggle("is-expanded", this.store.hudExpanded);
    this.e.hudEventToggle.setAttribute("aria-expanded", String(this.store.hudExpanded));
    this.e.hudExpand.textContent = t(this.store.hudExpanded ? "context.collapse" : "context.expand");
    if (!lastEvent) {
      this.e.hudEvent.textContent = t("context.noEvent");
      this.e.hudExpand.hidden = true;
      return;
    }
    const speaker = lastEvent.kind === "master"
      ? t("game.master")
      : characterName(state, lastEvent.character_id, t("context.heroFallback"));
    const summary = t("context.event", { speaker, text: lastEvent.text });
    this.e.hudEvent.textContent = summary;
    this.e.hudEvent.title = summary;
    this.e.hudExpand.hidden = false;
  }

  renderControls() {
    const state = this.store.current();
    const online = this.store.backend === "online";
    const turnBusy = this.store.operation.turn !== "idle";
    const campaignBusy = Boolean(this.store.operation.campaign);
    const canPlay = online && state?.connection === "server" && state.characters.length > 0
      && state.campaign.started;
    setFormDisabled(this.e.createSessionForm, !online || Boolean(this.store.operation.session));
    setFormDisabled(this.e.joinSessionForm, !online || Boolean(this.store.operation.session));
    const canCreate = online && state?.connection === "server" && !this.store.operation.character && !campaignBusy;
    setFormDisabled(this.e.characterForm, !canCreate);
    setFormDisabled(this.e.actionForm, !canPlay || turnBusy || Boolean(state?.pending_turn));
    this.e.characterFormNotice.textContent = campaignBusy
      ? t("characters.startingAdventure")
      : this.store.operation.character
      ? t("characters.creating")
      : canCreate ? t("characters.ready") : t("characters.localReadOnly");
    const phase = this.store.operation.turn;
    let hint = "game.readyHint";
    if (!state) hint = "game.noSession";
    else if (!state.characters.length) hint = "game.noCharacters";
    else if (!online) hint = "offline.body";
    else if (state.connection !== "server") hint = "game.localReadOnly";
    else if (!state.campaign.started) hint = "game.startCampaignHint";
    else if (phase === "planning") hint = "game.planningCheck";
    else if (phase === "rolling" || phase === "resolving") hint = "check.waitingForMaster";
    else if (state.pending_turn) hint = "check.pendingHint";
    this.e.actionHint.textContent = t(hint);
    this.e.actionForm.setAttribute("aria-busy", String(turnBusy));
    this.e.actionSubmit.classList.toggle("is-pending", turnBusy);
    this.e.actionSubmitLabel.textContent = t(this.store.pendingAttempt ? "game.retryTurn" : "game.send");
    this.e.syncSession.disabled = !online || state?.connection !== "server" || turnBusy || this.store.operation.sync;
    this.e.syncSession.setAttribute("aria-busy", String(this.store.operation.sync));
  }

  renderFeedback() {
    const feedback = this.store.feedback;
    this.e.actionStatus.hidden = !feedback;
    this.e.actionStatus.className = `turn-status${feedback ? ` is-${feedback.kind}` : ""}`;
    this.e.actionStatus.textContent = feedback ? t(feedback.key) : "";
  }

  renderGame() {
    this.renderActorSelect();
    this.renderParty();
    this.renderLog();
    this.renderSyncStatus();
    this.renderReceipt();
    this.renderPendingCheck();
  }

  renderActorSelect() {
    const state = this.store.current();
    const selected = this.store.selectedCharacterId(state);
    this.e.actingCharacter.replaceChildren();
    if (!state?.characters.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = t("game.noCharacters");
      this.e.actingCharacter.append(option);
      return;
    }
    const fragment = document.createDocumentFragment();
    state.characters.forEach((character) => {
      const option = document.createElement("option");
      option.value = character.id;
      option.textContent = character.name;
      fragment.append(option);
    });
    this.e.actingCharacter.append(fragment);
    this.e.actingCharacter.value = selected;
  }

  healthWidget(character) {
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

  characterCard(character, { party = false, active = false } = {}) {
    const card = document.createElement("article");
    card.className = `character-card${party ? " party-character-card" : ""}${active ? " is-active" : ""}`;
    card.dataset.archetype = characterArchetype(character.concept);
    card.dataset.characterId = character.id;
    if (party) {
      card.tabIndex = this.store.operation.turn === "idle" && !this.store.current()?.pending_turn ? 0 : -1;
      card.setAttribute("role", "button");
      card.setAttribute("aria-pressed", String(active));
      card.setAttribute("aria-label", t(active ? "game.activeHeroLabel" : "game.selectHero", { name: character.name }));
    }
    const icon = document.createElement("div");
    icon.className = "character-icon";
    icon.innerHTML = iconMarkup(characterArchetype(character.concept));
    const body = document.createElement("div");
    const name = document.createElement("h3");
    name.className = "character-name";
    name.textContent = character.name;
    const concept = document.createElement("p");
    concept.className = "archetype";
    concept.textContent = character.concept || t(`archetype.${characterArchetype(character.concept)}`);
    body.append(name, concept, this.healthWidget(character));
    const stats = document.createElement("ul");
    stats.className = party ? "stat-chips party-stats" : "stat-chips";
    Object.entries(character.stats).forEach(([stat, value]) => {
      const item = document.createElement("li");
      item.className = "stat-chip";
      const label = ABILITIES.has(stat) ? t(party ? `ability.short.${stat}` : `ability.${stat}`) : stat;
      item.textContent = `${label} ${value}`;
      item.title = `${ABILITIES.has(stat) ? t(`ability.${stat}`) : stat}: ${value}`;
      stats.append(item);
    });
    const inventoryTitle = document.createElement("p");
    inventoryTitle.className = "inventory-title";
    inventoryTitle.textContent = t("characters.inventory");
    const inventory = document.createElement("ul");
    inventory.className = "inventory-list";
    const items = character.inventory.filter((item) => item.quantity > 0);
    (items.length ? items : [{ name: t("characters.inventoryEmpty"), quantity: 0 }]).forEach((entry) => {
      const item = document.createElement("li");
      item.className = "inventory-item";
      item.textContent = entry.quantity ? `${entry.name} × ${entry.quantity}` : entry.name;
      inventory.append(item);
    });
    body.append(stats, inventoryTitle, inventory);
    card.append(icon, body);
    return card;
  }

  renderParty() {
    const state = this.store.current();
    const selected = this.store.selectedCharacterId(state);
    this.e.activeHero.replaceChildren();
    if (!state?.characters.length) {
      this.e.activeHero.append(emptyMessage(t("game.noCharacters"), { section: "characters", label: "onboarding.createHero" }));
      return;
    }
    const list = document.createElement("div");
    list.className = "hero-focus party-list";
    orderCharactersByActive(state.characters, selected).forEach((character) => {
      list.append(this.characterCard(character, { party: true, active: character.id === selected }));
    });
    this.e.activeHero.append(list);
  }

  renderLog() {
    const state = this.store.current();
    this.e.turnBadge.textContent = t("game.turns", { count: state?.turn_number || 0 });
    this.e.log.replaceChildren();
    if (!state) {
      this.e.log.append(emptyMessage(t("game.noSession"), { section: "session", label: "onboarding.createSession" }));
      return;
    }
    if (!state.log.length) {
      this.e.log.append(emptyMessage(
        t(state.campaign.started ? "game.noLog" : "game.noCampaign"),
        state.campaign.started ? null : { section: "characters", label: "onboarding.startAdventure" },
      ));
      return;
    }
    const fragment = document.createDocumentFragment();
    state.log.forEach((entry) => {
      const item = document.createElement("li");
      item.dataset.kind = entry.kind;
      const who = document.createElement("span");
      who.className = "who";
      who.textContent = entry.kind === "master" ? t("game.master") : characterName(state, entry.character_id);
      const text = document.createElement("p");
      text.textContent = entry.text;
      item.append(who, text);
      fragment.append(item);
    });
    this.e.log.append(fragment);
  }

  renderSyncStatus() {
    const state = this.store.current();
    if (this.store.operation.sync) this.e.syncStatus.textContent = t("game.syncing");
    else if (!state || state.connection !== "server") this.e.syncStatus.textContent = t("game.syncUnavailable");
    else if (!this.store.syncTimes.has(state.session_id)) this.e.syncStatus.textContent = t("game.syncNeeded");
    else {
      const locale = getLanguage() === "ru" ? "ru-RU" : "en-US";
      const time = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit" })
        .format(this.store.syncTimes.get(state.session_id));
      this.e.syncStatus.textContent = t("game.syncedAt", { time });
    }
  }

  receiptText(receipt) {
    const state = this.store.current();
    const hero = receipt.characterId ? characterName(state, receipt.characterId) : "";
    if (receipt.type === "health_changed") return t("game.receipt.health", { hero, delta: signed(receipt.delta), value: receipt.value });
    if (receipt.type === "item_added") return t("game.receipt.itemAdded", { hero, item: receipt.item, quantity: receipt.quantity });
    if (receipt.type === "item_removed") return t("game.receipt.itemRemoved", { hero, item: receipt.item, quantity: receipt.quantity });
    if (receipt.type === "location_changed") return t("game.receipt.location", { location: receipt.value });
    if (receipt.type === "stat_changed") return t("game.receipt.stat", { hero, stat: receipt.stat, delta: signed(receipt.delta), value: receipt.value });
    if (receipt.type === "skill_check_resolved") return t("game.receipt.check", {
      hero, stat: t(`ability.${receipt.ability}`), total: receipt.total,
      difficulty: receipt.difficulty, result: t(receipt.success ? "game.receipt.success" : "game.receipt.failure"),
    });
    return t("game.receipt.noChange");
  }

  renderReceipt() {
    const state = this.store.current();
    const receipt = state ? this.store.receipts.get(state.session_id) : null;
    this.e.turnReceipt.hidden = !receipt;
    this.e.receiptList.replaceChildren();
    if (!receipt) return;
    this.e.receiptTurn.textContent = t("game.turns", { count: receipt.turnNumber });
    const icons = { health_changed: "♥", item_added: "+", item_removed: "−", location_changed: "✦", stat_changed: "↑", skill_check_resolved: "◆", no_change: "·" };
    receipt.items.forEach((entry) => {
      const item = document.createElement("li");
      item.dataset.type = entry.type;
      const icon = document.createElement("span");
      icon.className = "receipt-icon";
      icon.textContent = icons[entry.type] || "·";
      icon.setAttribute("aria-hidden", "true");
      const text = document.createElement("span");
      text.textContent = this.receiptText(entry);
      item.append(icon, text);
      this.e.receiptList.append(item);
    });
  }

  renderPendingCheck() {
    const state = this.store.current();
    const pending = state?.pending_turn;
    this.e.skillCheckCard.hidden = !pending;
    if (!pending) return;
    const hero = state.characters.find((character) => character.id === pending.character_id);
    this.e.skillCheckActor.textContent = hero
      ? t("check.actor", { hero: hero.name, score: pending.score })
      : t("check.actorUnknown");
    this.e.skillCheckTitle.textContent = t("check.title", {
      ability: t(`ability.${pending.ability}`), current: pending.sequence, maximum: pending.maximum,
    });
    this.e.skillCheckReason.textContent = pending.reason;
    this.e.skillCheckFormula.textContent = `d20 ${signed(pending.modifier, true)} vs DC ${pending.difficulty}`;
    this.e.skillCheckChance.textContent = t("check.chance", { chance: pending.chance });
    const busy = this.store.operation.turn !== "idle";
    this.e.rollD20.disabled = busy;
    this.e.cancelCheck.hidden = !pending.can_cancel;
    this.e.cancelCheck.disabled = busy;
  }

  renderCharacters() {
    const state = this.store.current();
    const characters = state?.characters || [];
    this.e.characterCount.textContent = String(characters.length);
    this.e.characterGrid.replaceChildren();
    if (!characters.length) this.e.characterGrid.append(emptyMessage(t("characters.empty")));
    else {
      const fragment = document.createDocumentFragment();
      characters.forEach((character) => fragment.append(this.characterCard(character)));
      this.e.characterGrid.append(fragment);
    }
    const needsStart = Boolean(state && state.connection === "server" && characters.length && !state.campaign.started);
    this.e.characterNextActions.hidden = !characters.length || (!needsStart && !this.store.showCharacterNext);
    this.e.startAdventure.hidden = !needsStart;
    this.e.startAdventure.disabled = Boolean(this.store.operation.campaign);
    this.e.goToGame.hidden = !state?.campaign.started;
    this.e.goToGame.disabled = Boolean(this.store.operation.campaign);
    this.e.addAnotherHero.disabled = Boolean(this.store.operation.campaign);
    this.e.characterNextPrompt.textContent = t(state?.campaign.started
      ? "characters.joinedPrompt" : "characters.nextPrompt");
  }

  renderWorld() {
    const state = this.store.current();
    this.e.worldLocation.textContent = state?.location || t("world.unknown");
    this.e.worldSessionName.textContent = state?.session_name || (state ? t("session.untitled") : t("session.none"));
    this.e.worldSessionId.textContent = state?.session_id || "—";
    this.e.worldCharacterCount.textContent = String(state?.characters.length || 0);
    this.e.worldLogCount.textContent = String(state?.log.length || 0);
    this.e.worldParty.replaceChildren();
    if (!state?.characters.length) {
      this.e.worldParty.append(emptyMessage(t("world.empty")));
      return;
    }
    const list = document.createElement("div");
    list.className = "world-party-list";
    state.characters.forEach((character) => {
      const row = document.createElement("div");
      row.className = "world-party-item";
      const icon = document.createElement("span");
      icon.className = "mini-icon";
      icon.innerHTML = iconMarkup(characterArchetype(character.concept));
      const name = document.createElement("strong");
      name.textContent = character.name;
      const hp = document.createElement("span");
      hp.className = "health-value";
      hp.textContent = `♥ ${character.hp}/${character.max_hp}`;
      row.append(icon, name, hp);
      list.append(row);
    });
    this.e.worldParty.append(list);
  }

  renderSessions() {
    this.e.sessionList.replaceChildren();
    const sessions = Object.values(this.store.sessions.sessions);
    if (!sessions.length) {
      this.e.sessionList.append(emptyMessage(t("session.savedEmpty")));
      return;
    }
    const fragment = document.createDocumentFragment();
    sessions.forEach((session) => {
      const active = session.session_id === this.store.currentSessionId();
      const item = document.createElement("article");
      item.className = `session-item${active ? " is-active" : ""}`;
      item.dataset.sessionId = session.session_id;
      const text = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = session.session_name || t(session.connection === "legacy" ? "session.legacy" : "session.untitled");
      const status = document.createElement("span");
      status.textContent = t(session.connection === "server" ? "session.server" : session.connection === "legacy" ? "session.legacyStatus" : "session.unverified");
      const identifier = document.createElement("code");
      identifier.className = "session-id";
      identifier.textContent = session.session_id;
      text.append(title, status, identifier);
      if (active) {
        const badge = document.createElement("span");
        badge.className = "active-session-badge";
        badge.textContent = t("session.activeBadge");
        text.prepend(badge);
      }
      const actions = document.createElement("div");
      actions.className = "session-actions";
      for (const [action, label, className] of [
        ["copy", "session.copyId", "text-button copy-card-button"],
        ["open", active ? "session.active" : "session.open", "secondary-button"],
        ["delete", session.connection === "server" ? "session.delete" : "session.removeLocal", "text-button danger-text-button"],
      ]) {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.sessionAction = action;
        button.className = className;
        button.textContent = t(label);
        button.disabled = (action === "open" && (active || this.store.isBusy()))
          || (action === "delete" && this.store.isBusy());
        actions.append(button);
      }
      item.append(text, actions);
      fragment.append(item);
    });
    this.e.sessionList.append(fragment);
  }

  playStateChanges(changes) {
    if (changes.location) {
      const card = this.e.worldLocation.closest(".location-card");
      card?.classList.add("is-location-changed");
      setTimeout(() => card?.classList.remove("is-location-changed"), 900);
    }
    document.querySelectorAll(".character-card[data-character-id]").forEach((card) => {
      const change = changes.characters[card.dataset.characterId];
      if (!change) return;
      if (change.hp) card.classList.add("is-hp-changed");
      if (change.inventory) card.classList.add("is-inventory-changed");
      setTimeout(() => card.classList.remove("is-hp-changed", "is-inventory-changed"), 900);
    });
  }

  openDeleteDialog(session) {
    const name = session.session_name || t(session.connection === "legacy" ? "session.legacy" : "session.untitled");
    const server = session.connection === "server";
    this.e.sessionDeleteMessage.textContent = t(server ? "session.deleteServerBody" : "session.removeLocalBody", { name });
    this.e.sessionDeletePending.hidden = !session.pending_turn;
    this.e.sessionDeleteError.textContent = "";
    this.e.sessionDeleteConfirm.textContent = t(server ? "session.deleteConfirm" : "session.removeConfirm");
    this.e.sessionDeleteDialog.showModal();
    this.e.sessionDeleteCancel.focus();
  }

  setDeleteBusy(busy) {
    this.e.sessionDeleteCancel.disabled = busy;
    this.e.sessionDeleteConfirm.disabled = busy;
    if (busy) this.e.sessionDeleteConfirm.textContent = t("session.deleting");
  }

  showCopyFeedback(key) {
    this.store.copyFeedback = key;
    this.store.notify();
    if (this.copyTimer !== null) clearTimeout(this.copyTimer);
    this.copyTimer = setTimeout(() => {
      this.store.copyFeedback = "";
      this.store.notify();
    }, 1800);
  }
}
