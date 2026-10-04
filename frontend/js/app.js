import { createSession, fetchHealth } from "./api.js";
import { loadPublicState, savePublicState } from "./state.js";

const healthStatus = document.querySelector("#health-status");
const recheckHealth = document.querySelector("#recheck-health");
const createSessionForm = document.querySelector("#create-session-form");
const sessionNameInput = document.querySelector("#session-name");
const sessionResult = document.querySelector("#session-result");
const joinSessionForm = document.querySelector("#join-session-form");
const joinSessionId = document.querySelector("#join-session-id");
const characterForm = document.querySelector("#character-form");
const statRows = document.querySelector("#stat-rows");
const addStatButton = document.querySelector("#add-stat");
const logList = document.querySelector("#log");
const actionForm = document.querySelector("#action-form");
const actingCharacter = document.querySelector("#acting-character");
const actionText = document.querySelector("#action-text");
const stateSession = document.querySelector("#state-session");
const stateLocation = document.querySelector("#state-location");
const sheets = document.querySelector("#sheets");

let publicState = loadPublicState();

function addStatRow(name = "", value = "") {
  const row = document.createElement("div");
  row.className = "stat-row";

  const nameInput = document.createElement("input");
  nameInput.type = "text";
  nameInput.placeholder = "Stat";
  nameInput.maxLength = 24;
  nameInput.value = name;
  nameInput.setAttribute("aria-label", "Stat name");

  const valueInput = document.createElement("input");
  valueInput.type = "number";
  valueInput.placeholder = "0";
  valueInput.value = value;
  valueInput.setAttribute("aria-label", "Stat value");

  const removeButton = document.createElement("button");
  removeButton.type = "button";
  removeButton.textContent = "Remove";
  removeButton.addEventListener("click", () => {
    row.remove();
  });

  row.append(nameInput, valueInput, removeButton);
  statRows.append(row);
}

function readStats() {
  const stats = {};
  statRows.querySelectorAll(".stat-row").forEach((row) => {
    const [nameInput, valueInput] = row.querySelectorAll("input");
    const name = nameInput.value.trim();
    if (!name) {
      return;
    }
    const value = Number(valueInput.value);
    stats[name] = Number.isInteger(value) ? value : 0;
  });
  return stats;
}

function characterName(characterId) {
  const character = publicState.characters.find((item) => item.id === characterId);
  return character ? character.name : "Unknown character";
}

function render() {
  publicState = savePublicState(publicState);

  const sessionLabel = publicState.session_id
    ? `${publicState.session_name || "Untitled"} (${publicState.session_id})`
    : "No session";
  stateSession.textContent = sessionLabel;
  stateLocation.textContent = publicState.location || "Unknown";

  logList.replaceChildren();
  if (publicState.log.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "No actions yet.";
    logList.append(empty);
  } else {
    publicState.log.forEach((entry) => {
      const item = document.createElement("li");
      item.dataset.kind = entry.kind;
      const who = document.createElement("span");
      who.className = "who";
      who.textContent = entry.kind === "master" ? "Dungeon Master" : characterName(entry.character_id);
      const text = document.createElement("p");
      text.textContent = entry.text;
      item.append(who, text);
      logList.append(item);
    });
  }

  actingCharacter.replaceChildren();
  if (publicState.characters.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "Add a character first";
    actingCharacter.append(option);
    actingCharacter.disabled = true;
  } else {
    actingCharacter.disabled = false;
    publicState.characters.forEach((character) => {
      const option = document.createElement("option");
      option.value = character.id;
      option.textContent = character.name;
      actingCharacter.append(option);
    });
  }

  sheets.replaceChildren();
  if (publicState.characters.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No characters in the local cache.";
    sheets.append(empty);
    return;
  }

  publicState.characters.forEach((character) => {
    const sheet = document.createElement("article");
    sheet.className = "sheet";
    const title = document.createElement("h3");
    title.textContent = character.name;
    const concept = document.createElement("p");
    concept.textContent = character.concept || "No concept";
    const hp = document.createElement("p");
    hp.textContent = `HP ${character.hp} / ${character.max_hp}`;
    const stats = document.createElement("ul");
    const statNames = Object.keys(character.stats);
    if (statNames.length === 0) {
      const item = document.createElement("li");
      item.textContent = "No stats";
      stats.append(item);
    } else {
      statNames.forEach((name) => {
        const item = document.createElement("li");
        item.textContent = `${name}: ${character.stats[name]}`;
        stats.append(item);
      });
    }
    const inventory = document.createElement("ul");
    if (character.inventory.length === 0) {
      const item = document.createElement("li");
      item.textContent = "Inventory empty";
      inventory.append(item);
    } else {
      character.inventory.forEach((item) => {
        const row = document.createElement("li");
        row.textContent = `${item.name} × ${item.quantity}`;
        inventory.append(row);
      });
    }
    sheet.append(title, concept, hp, stats, inventory);
    sheets.append(sheet);
  });
}

async function checkHealth() {
  healthStatus.dataset.state = "";
  healthStatus.textContent = "Checking backend…";
  const health = await fetchHealth();
  healthStatus.dataset.state = health.available ? "ok" : "bad";
  healthStatus.textContent = health.status;
}

recheckHealth.addEventListener("click", () => {
  checkHealth();
});

createSessionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = sessionNameInput.value.trim();
  if (!name) {
    sessionResult.textContent = "Session name is required.";
    return;
  }
  sessionResult.textContent = "Creating session…";
  try {
    const session = await createSession(name);
    publicState = {
      ...publicState,
      session_id: session.id,
      session_name: session.name,
    };
    sessionResult.textContent = `Session created: ${session.id}`;
    render();
  } catch (error) {
    sessionResult.textContent = error.message || "Backend unavailable";
    healthStatus.dataset.state = "bad";
    healthStatus.textContent = "Backend unavailable";
  }
});

joinSessionForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const sessionId = joinSessionId.value.trim();
  if (!sessionId) {
    return;
  }
  publicState = {
    ...publicState,
    session_id: sessionId,
    session_name: publicState.session_name,
  };
  sessionResult.textContent = "Session id stored in the local cache. Backend has not confirmed it.";
  render();
});

addStatButton.addEventListener("click", () => {
  addStatRow();
});

characterForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const name = document.querySelector("#character-name").value.trim();
  const concept = document.querySelector("#character-concept").value.trim();
  const hp = Number(document.querySelector("#character-hp").value);
  const startingItem = document.querySelector("#starting-item").value.trim();
  if (!name || !Number.isInteger(hp) || hp < 1) {
    return;
  }
  const inventory = startingItem
    ? [{ item_id: startingItem, name: startingItem, quantity: 1 }]
    : [];
  const character = {
    id: `local-character-${publicState.characters.length + 1}`,
    name,
    concept,
    hp,
    max_hp: hp,
    stats: readStats(),
    inventory,
  };
  publicState = {
    ...publicState,
    characters: [...publicState.characters, character],
  };
  characterForm.reset();
  document.querySelector("#character-hp").value = "10";
  statRows.replaceChildren();
  addStatRow("might", "1");
  render();
});

actionForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const characterId = actingCharacter.value;
  const text = actionText.value.trim();
  if (!characterId || !text) {
    return;
  }
  publicState = {
    ...publicState,
    log: [
      ...publicState.log,
      { kind: "player", character_id: characterId, text },
      { kind: "master", character_id: "", text: "Dungeon Master is not connected yet." },
    ],
  };
  actionText.value = "";
  render();
  logList.lastElementChild?.scrollIntoView({ block: "nearest" });
});

addStatRow("might", "1");
render();
checkHealth();
