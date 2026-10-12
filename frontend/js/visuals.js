const ARCHETYPES = [
  { name: "warrior", words: ["воин", "рыцар", "боец", "warrior", "knight", "fighter"] },
  { name: "rogue", words: ["плут", "вор", "разбой", "rogue", "thief", "assassin"] },
  { name: "mage", words: ["маг", "волшеб", "чарод", "mage", "wizard", "sorcer"] },
  { name: "ranger", words: ["следопыт", "лучник", "охотник", "ranger", "archer", "hunter"] },
  { name: "healer", words: ["целител", "жрец", "лекар", "healer", "cleric", "priest"] },
];

const ICONS = {
  warrior: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 1h3v5h1v2H6v7H3V8H2V6h1V1Zm7 0h3v5h1v2h-1v7h-3V8H9V6h1V1ZM6 9h4v2H6V9Z"/></svg>',
  rogue: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M11 1h3v3h-2v2h-2v2H8v2H6v2H4v3H1v-3h2v-2h2V8h2V6h2V4h2V1ZM2 2h3v3H2V2Z"/></svg>',
  mage: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M7 0h2v3h3v2H9v3H7V5H4V3h3V0Zm-3 8h8v2h2v6H2v-6h2V8Zm2 3v3h4v-3H6Z"/></svg>',
  ranger: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 1h2v2h2v2h2v2h2v2h2v2h2v2h-2v-1h-2v-1H8V9H6V7H4V5H2V3H1V1h1Zm9 0h4v4h-2V3h-2V1ZM1 11h4v2H3v2H1v-4Z"/></svg>',
  healer: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 1h4v4h4v5h-4v5H6v-5H2V5h4V1Zm2 5v3H5v2h3v3h1v-3h3V9H9V6H8Z"/></svg>',
  adventurer: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M7 1h2v2h2v2h2v6h-2v2H9v2H7v-2H5v-2H3V5h2V3h2V1Zm0 4v2H5v3h2v2h2v-2h2V7H9V5H7Z"/></svg>',
};

export function characterArchetype(concept = "") {
  const normalized = String(concept).toLowerCase();
  return ARCHETYPES.find((entry) => entry.words.some((word) => normalized.includes(word)))?.name ?? "adventurer";
}

export function iconMarkup(archetype) { return ICONS[archetype] ?? ICONS.adventurer; }

export function healthTone(current, maximum) {
  const safeMaximum = Number(maximum);
  if (!Number.isFinite(safeMaximum) || safeMaximum <= 0) return "critical";
  const ratio = Math.max(0, Number(current)) / safeMaximum;
  if (ratio <= 0.25) return "critical";
  if (ratio <= 0.5) return "warning";
  return "healthy";
}

export function healthPercent(current, maximum) {
  const safeMaximum = Number(maximum);
  if (!Number.isFinite(safeMaximum) || safeMaximum <= 0) return 0;
  return Math.max(0, Math.min(100, (Number(current) / safeMaximum) * 100));
}
