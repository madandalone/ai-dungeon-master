export function signed(value, zeroSign = false) {
  return value > 0 || (zeroSign && value === 0) ? `+${value}` : String(value);
}

export function characterName(state, characterId, fallback = "?") {
  return state?.characters.find((character) => character.id === characterId)?.name || fallback;
}
