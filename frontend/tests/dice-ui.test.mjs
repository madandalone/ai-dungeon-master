import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const [html, css, app, dice] = await Promise.all([
  readFile(new URL("../index.html", import.meta.url), "utf8"),
  readFile(new URL("../styles.css", import.meta.url), "utf8"),
  readFile(new URL("../js/app.js", import.meta.url), "utf8"),
  readFile(new URL("../js/dice-view.js", import.meta.url), "utf8"),
]);

test("skill check card exposes chance, sequence and a separate roll action", () => {
  assert.match(html, /id="skill-check-chance"/);
  assert.match(html, /id="roll-d20"/);
  assert.match(app, /current: pending\.sequence/);
  assert.match(app, /maximum: pending\.maximum/);
  assert.match(app, /pending\.chance/);
});

test("dice overlay has rolling, landing and outcome states", () => {
  assert.match(html, /id="dice-overlay"/);
  assert.match(dice, /is-rolling/);
  assert.match(dice, /is-settling/);
  assert.match(dice, /is-success/);
  assert.match(dice, /is-failure/);
  assert.match(dice, /PIXEL_DIGITS/);
  assert.match(css, /\.pixel-cell\.is-lit/);
  assert.match(dice, /check\.roll.*check\.modifier.*check\.total.*check\.difficulty/);
});

test("finished checks are hidden and reduced motion is supported", () => {
  assert.match(css, /\.skill-check-card\[hidden\]\s*\{\s*display:\s*none/);
  assert.match(css, /@media\s*\(prefers-reduced-motion:\s*reduce\)/);
  assert.match(dice, /matchMedia\?\.\("\(prefers-reduced-motion: reduce\)"\)/);
});

test("actor select and collapsed footer use custom pixel controls", () => {
  assert.match(html, /class="select-chevron"/);
  assert.match(css, /appearance:\s*none/);
  assert.match(css, /\.app-shell\.is-collapsed \.server-mini/);
  assert.match(css, /\.app-shell\.is-collapsed \.language-compact/);
});

test("new actions stay blocked while a check is pending", () => {
  assert.match(app, /Boolean\(state\?\.pending_turn\)/);
  assert.match(app, /if \(!canPlay\(state\).*state\.pending_turn\) return/s);
});

test("session deletion uses an accessible confirmation dialog", () => {
  assert.match(html, /<dialog[^>]+id="session-delete-dialog"[^>]+aria-labelledby="session-delete-title"/);
  assert.match(html, /id="session-delete-pending"[^>]+hidden/);
  assert.match(html, /id="session-delete-cancel"/);
  assert.match(html, /id="session-delete-confirm"/);
  assert.match(app, /await deleteSession\(target\.session_id\)/);
  assert.match(app, /store = removeSession\(store, target\.session_id\)/);
});
