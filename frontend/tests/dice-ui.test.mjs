import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const [html, css, app] = await Promise.all([
  readFile(new URL("../index.html", import.meta.url), "utf8"),
  readFile(new URL("../styles.css", import.meta.url), "utf8"),
  readFile(new URL("../js/app.js", import.meta.url), "utf8"),
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
  assert.match(app, /is-rolling/);
  assert.match(app, /is-settling/);
  assert.match(app, /is-success/);
  assert.match(app, /is-failure/);
  assert.match(app, /PIXEL_DIGITS/);
  assert.match(css, /\.pixel-cell\.is-lit/);
  assert.match(app, /check\.roll.*check\.modifier.*check\.total.*check\.difficulty/);
});

test("finished checks are hidden and reduced motion is supported", () => {
  assert.match(css, /\.skill-check-card\[hidden\]\s*\{\s*display:\s*none/);
  assert.match(css, /@media\s*\(prefers-reduced-motion:\s*reduce\)/);
  assert.match(app, /matchMedia\?\.\("\(prefers-reduced-motion: reduce\)"\)/);
});

test("new actions stay blocked while a check is pending", () => {
  assert.match(app, /Boolean\(state\?\.pending_turn\)/);
  assert.match(app, /if \(!canPlay\(state\).*state\.pending_turn\) return/s);
});
