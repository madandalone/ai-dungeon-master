const IDS = [
  "app-shell", "sidebar", "sidebar-toggle", "mobile-menu", "sidebar-backdrop",
  "section-title", "section-description", "header-session-name", "copy-session-id",
  "copy-feedback", "hud-hero", "hud-hero-hp", "hud-event", "hud-event-toggle",
  "hud-expand", "offline-banner", "global-retry", "server-mini", "server-mini-text",
  "language-compact", "connection-state", "health-status", "health-description",
  "recheck-health", "create-session-form", "session-name", "session-theme", "session-hook", "join-session-form",
  "join-session-id", "session-result", "session-list", "session-delete-dialog",
  "session-delete-message", "session-delete-pending", "session-delete-error",
  "session-delete-cancel", "session-delete-confirm", "character-form", "character-name",
  "character-concept", "character-hp", "starting-item", "character-message",
  "character-form-notice", "stat-rows", "character-grid", "character-count",
  "character-next-actions", "character-next-prompt", "add-another-hero", "start-adventure", "go-to-game", "action-form",
  "acting-character", "action-text", "action-hint", "action-submit",
  "action-submit-label", "action-status", "log", "turn-badge", "sync-session",
  "sync-status", "turn-receipt", "receipt-turn", "receipt-list", "skill-check-card",
  "skill-check-actor", "skill-check-title", "skill-check-reason", "skill-check-formula",
  "skill-check-chance", "roll-d20", "cancel-check", "dice-overlay", "dice-title",
  "d20", "d20-value", "dice-formula", "active-hero", "world-location",
  "world-session-name", "world-session-id", "world-character-count", "world-log-count",
  "world-party",
];

function camel(id) {
  return id.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
}

export function collectElements(root = document) {
  const elements = Object.fromEntries(IDS.map((id) => [camel(id), root.querySelector(`#${id}`)]));
  elements.shell = elements.appShell;
  return elements;
}
