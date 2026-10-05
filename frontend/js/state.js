const STORAGE_KEY = "ai-dungeon-master.public-cache";

export function emptyPublicState() {
  return {
    session_id: "",
    session_name: "",
    location: "",
    characters: [],
    log: [],
  };
}

function copyCharacter(character) {
  const stats = {};
  Object.entries(character.stats || {}).forEach(([name, value]) => {
    stats[name] = value;
  });
  return {
    id: String(character.id || ""),
    name: String(character.name || ""),
    concept: String(character.concept || ""),
    hp: Number(character.hp || 0),
    max_hp: Number(character.max_hp || 0),
    stats,
    inventory: (character.inventory || []).map((item) => ({
      item_id: String(item.item_id || ""),
      name: String(item.name || item.item_id || ""),
      quantity: Number(item.quantity || 0),
    })),
  };
}

export function normalizePublicState(value) {
  const source = value && typeof value === "object" ? value : {};
  return {
    session_id: String(source.session_id || ""),
    session_name: String(source.session_name || ""),
    location: String(source.location || ""),
    characters: Array.isArray(source.characters) ? source.characters.map(copyCharacter) : [],
    log: Array.isArray(source.log)
      ? source.log
          .filter((entry) => entry && (entry.kind === "player" || entry.kind === "master"))
          .map((entry) => ({
            kind: entry.kind,
            character_id: String(entry.character_id || ""),
            text: String(entry.text || ""),
          }))
      : [],
  };
}

export function loadPublicState() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return emptyPublicState();
    }
    return normalizePublicState(JSON.parse(raw));
  } catch {
    return emptyPublicState();
  }
}

export function savePublicState(state) {
  const publicState = normalizePublicState(state);
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(publicState));
  return publicState;
}
