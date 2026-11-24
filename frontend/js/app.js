import { ApiClient } from "./api.js?v=20261124-2";
import { AppStore } from "./app-store.js?v=20261124-2";
import { AppView } from "./app-view.js?v=20261124-2";
import {
  CharacterController, SessionController, ShellController, TurnController,
} from "./controllers.js?v=20261124-2";
import { createDiceView } from "./dice-view.js?v=20261124-2";
import { collectElements } from "./dom.js?v=20261124-2";
import { initLanguage, t } from "./i18n.js?v=20261124-2";

initLanguage();

const elements = collectElements();
const store = new AppStore();
const api = new ApiClient();
const view = new AppView(elements, store);
const diceView = createDiceView({
  overlay: elements.diceOverlay,
  title: elements.diceTitle,
  d20: elements.d20,
  value: elements.d20Value,
  formula: elements.diceFormula,
}, t);

const sessions = new SessionController({ store, view, api, elements, diceView });
const shell = new ShellController({ store, view, api, elements, sessions });
const controllers = [
  sessions,
  shell,
  new CharacterController({ store, api, elements, sessions }),
  new TurnController({ store, view, api, elements, diceView, sessions }),
];

store.subscribe(() => view.render());
controllers.forEach((controller) => controller.bind());

if (!store.current()) store.setSection("session");
else view.render();

shell.checkHealth();
