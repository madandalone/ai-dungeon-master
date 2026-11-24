const PIXEL_DIGITS = {
  "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
  "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
  "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
  "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
  "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
  "5": ["11111", "10000", "10000", "11110", "00001", "00001", "11110"],
  "6": ["01110", "10000", "10000", "11110", "10001", "10001", "01110"],
  "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
  "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
  "9": ["01110", "10001", "10001", "01111", "00001", "00001", "01110"],
  "?": ["01110", "10001", "00001", "00010", "00100", "00000", "00100"],
};

import { signed } from "./format.js?v=20261124-1";

export function createDiceView(elements, translate) {
  let ticker = null;

  function renderValue(value) {
    const text = String(value);
    const digits = [...text].map((character) => {
      const digit = document.createElement("span");
      digit.className = "pixel-digit";
      (PIXEL_DIGITS[character] || PIXEL_DIGITS["?"]).join("").split("").forEach((bit) => {
        const pixel = document.createElement("span");
        pixel.className = `pixel-cell${bit === "1" ? " is-lit" : ""}`;
        digit.append(pixel);
      });
      return digit;
    });
    elements.value.setAttribute("aria-label", text);
    elements.value.replaceChildren(...digits);
  }

  function stop() {
    if (ticker !== null) globalThis.clearInterval(ticker);
    ticker = null;
  }

  function start() {
    const reduced = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    elements.overlay.hidden = false;
    elements.formula.textContent = translate("check.waitingForMaster");
    elements.title.textContent = translate("check.rolling");
    elements.d20.className = `d20${reduced ? "" : " is-rolling"}`;
    renderValue("?");
    if (!reduced) {
      ticker = globalThis.setInterval(() => renderValue(1 + Math.floor(Math.random() * 20)), 90);
    }
  }

  async function settle(check) {
    stop();
    renderValue(check.roll);
    elements.d20.className = `d20 is-settling ${check.success ? "is-success" : "is-failure"}`;
    elements.title.textContent = translate(check.success ? "check.success" : "check.failure");
    elements.formula.textContent = `${check.roll} ${signed(check.modifier, true)} = ${check.total} vs DC ${check.difficulty}`;
    await new Promise((resolve) => globalThis.setTimeout(resolve, 850));
    elements.overlay.hidden = true;
    elements.d20.className = "d20";
  }

  function hide() {
    stop();
    elements.overlay.hidden = true;
    elements.d20.className = "d20";
  }

  return { start, stop, settle, hide, renderValue };
}
