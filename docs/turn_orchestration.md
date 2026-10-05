# Оркестрация хода: как подключить AI DM, Rules Service и Game Engine

Пакет `integration/` реализует игровой ход из `docs/integration.md`
(раздел "Target flow") как чистую функцию без HTTP, БД и сети.
Backend вызывает её из будущего `POST /turns`.

```text
PlayerAction + GameState (из хранилища Backend)
  -> TurnOrchestrator.play_turn(...)
       1. precheck:  apply_action(state, action, [], dice)   # дёшево отсекает STALE_REVISION и пр.
       2. правила:   RulesProvider.get_rules(action.text)
       3. DM:        DungeonMaster.process_turn(TurnRequest)  # LLM, вывод НЕ доверенный
       4. маппинг:   TurnResponse -> GameCommand[]            # integration/mapping.py
       5. движок:    apply_action(state, action, commands, dice)
  -> TurnOutcome(accepted, state, events, errors, narration, dm_notes)
```

## Принципы

| Принцип | Как реализован |
|---|---|
| Зависимости направлены внутрь, компоненты не знают друг друга | `integration/` импортирует `engine`, `ai_dm.models`; сами они `integration` не импортируют |
| Ports and adapters | `RulesProvider` и `DungeonMaster` - `Protocol` в `ports.py`; реальная реализация или заглушка подставляется снаружи |
| LLM не доверяем | Единственная проверка семантики - `engine.apply_action`; адаптер лишь переводит формат |
| Один источник истины | Состояние меняет только движок; нарратив не возвращается, если ход отклонён |
| Не тратить LLM зря | Пустой `apply_action` до вызова DM отсекает устаревшую ревизию, чужую сессию и пустой текст |
| Явные ошибки | Отказ движка - значение (`accepted=False`, `errors`). Сбой инфраструктуры - исключение `IntegrationError` (-> 5xx, ничего не сохранять) |
| Чистота | Нет глобального состояния, кубики (`Dice`) и `action_id` приходят от Backend |
| Секреты | `hidden_plot` уходит только в DM; `dm_notes` (`dm_thoughts`) Backend сохраняет у себя и не отдаёт на Frontend |

## Контракт между DM и движком (маппинг)

| `TurnResponse` (Егор) | `GameCommand` (Денис) |
|---|---|
| `state_updates[i].hp_change != 0` | `AdjustHealth(character_id, hp_change)` |
| `items_added` (имена, повторы суммируются) | `AddItem(character_id, item_id=имя, quantity=n)` |
| `items_removed` | `RemoveItem(character_id, item_id, n)`; имя ищется в инвентаре, иначе передаётся как есть и отклоняется движком |
| `new_location` | `SetLocation(location_id)` |
| `narrative` | `TurnOutcome.narration` (только при `accepted`) |
| `dm_thoughts` | `TurnOutcome.dm_notes` (приватно) |

Порядок команд: по персонажам в порядке `state_updates`, внутри - здоровье, добавление,
удаление; `SetLocation` последним.

Ограничения и допущения (нужно согласовать с Егором и Денисом):
- DM идентифицирует персонажей по **имени**, движок - по `id`. Имена должны быть
  уникальны (иначе `UnmappableResponse`); неизвестное имя - тоже `UnmappableResponse`.
- Инвентарь для DM - список названий (предмет с количеством 2 повторяется дважды).
- Новый предмет получает `item_id == name` (так работает `AddItem` движка).
- `SkillCheck`, `AdjustStat`, `SetWorldFlag` DM пока не может предложить: в `TurnResponse` нет таких полей.
- `room_context` DM'у передаёт Backend (в движке такого поля нет).

## Как использовать из Backend

```python
from integration import TurnOrchestrator, IntegrationError
from rules_service import RulesService
from ai_dm import dm_engine          # модуль с process_turn(request)
from engine.dice import Dice, LocalRandomSource

orchestrator = TurnOrchestrator(dm=dm_engine, rules=RulesService.from_file(RULES_BOOK_PATH))

try:
    outcome = orchestrator.play_turn(state, action, hidden_plot, Dice(LocalRandomSource()), room_context)
except IntegrationError:
    ...                                  # 502, ничего не сохранять
if outcome.accepted:
    ...  # compare-and-swap по revision, сохранить outcome.state, события, narration, dm_notes
else:
    ...  # outcome.errors[0].code == "STALE_REVISION" -> 409
```

`ai_dm.dm_engine` подходит как `DungeonMaster`, потому что содержит функцию
`process_turn(request) -> TurnResponse` (модуль структурно удовлетворяет `Protocol`).
Импортировать его нужно только в точке сборки приложения: он требует пакет `openai`
и ключ в `.env`, поэтому тесты `integration/` его не импортируют.

## Тесты

`python -m unittest discover -s integration/tests -t .` - 8 тестов с реальным движком и
реальным `RulesService`, DM заменён заглушкой `FakeDM`. Проверяют: применение изменений,
контекст для DM (правила, сюжет, состояние), отсечку устаревшей ревизии без вызова LLM,
отбрасывание нарратива при отказе движка, неизвестного персонажа, сбой DM.

## Что остаётся Backend и не реализовано

HTTP-эндпоинт, загрузка/сохранение состояния и compare-and-swap, идемпотентность
по `action_id`, сохранение seed кубиков, CORS, ретраи LLM (см. `docs/integration.md`).
Адаптер намеренно не решает эти задачи, чтобы не залезать в зону Backend.

## Известные риски

- Нарратив LLM может описывать то, что движок отклонил; в этом случае он отбрасывается
  и игроку нужно показать ошибку или повторить запрос (политика - решение Backend).
- Если DM вернул hp_change для персонажа, который не совершал действие, мы это допускаем
  (в групповой игре это нормально); ограничение при необходимости вводится в движке.
