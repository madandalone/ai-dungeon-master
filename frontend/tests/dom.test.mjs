import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const [html, source] = await Promise.all([
  readFile(new URL("../index.html", import.meta.url), "utf8"),
  readFile(new URL("../js/dom.js", import.meta.url), "utf8"),
]);

test("every collected DOM id exists in the page", () => {
  const ids = [...source.matchAll(/"([a-z][a-z0-9-]+)"/g)].map((match) => match[1]);
  ids.forEach((id) => assert.match(html, new RegExp(`id=["']${id}["']`), `missing #${id}`));
});

test("app shell keeps the concise controller alias", () => {
  assert.match(source, /elements\.shell = elements\.appShell/);
});
