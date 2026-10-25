# Оркестрация хода: как подключить AI DM, Rules Service и Game Engine

Пакет `integration/` реализует игровой ход из `docs/integration.md`
(раздел "Target flow") как чистую функцию без HTTP, БД и сети.
Backend вызывает её из `POST /sessions/{id}/turns`, собирая оркестратор фабрикой `build_orchestrator()`
(см. разделы "Сборка компонентов" и "Как Backend подключён сейчас").

```text
PlayerAction + GameState + history (из хранилища Backend)
  -> TurnOrchestrator.play_turn(...)
       1. precheck:  apply_action(state, action, [], dice)   # дёшево отсекает STALE_REVISION и пр.
       2. правила:   RulesProvider.get_rules(action.text)
       3. DM:        DungeonMaster.process_turn(TurnRequest)  # LLM, вывод НЕ доверенный
       4. маппинг:   TurnResponse -> GameCommand[]            # integration/mapping.py
       5. движок:    apply_action(state, action, commands, dice)
  -> TurnOutcome(accepted, state, events, errors, narration, dm_notes, rules_failed)
```

## Принципы

| Принцип | Как реализован |
|---|---|
| Зависимости направлены внутрь, компоненты не знают друг друга | `integration/` импортирует `engine`, `ai_dm.models`; сами они `integration` не импортируют |
| Ports and adapters | `RulesProvider` и `DungeonMaster` - `Protocol` в `ports.py`; реальная реализация или заглушка подставляется снаружи |
| LLM не доверяем | Единственная проверка семантики - `engine.apply_action`; адаптер лишь переводит формат |
| Один источник истины | Состояние меняет только движок; нарратив не возвращается, если ход отклонён |
| Не тратить LLM зря | Пустой `apply_action` до вызова DM отсекает устаревшую ревизию, чужую сессию и пустой текст |
| Явные ошибки | Отказ движка - значение (`accepted=False`, `errors`). Сбой DM - исключение `IntegrationError` (-> 5xx, ничего не сохранять) |
| Вспомогательное не валит основное | Сбой Rules Service не прерывает ход: он играется без правил (`rules_failed=True`, предупреждение в логе) |
| Чистота | Нет глобального состояния, кубики (`Dice`) и `action_id` приходят от Backend; история ходов передаётся аргументом |
| Секреты | `hidden_plot` и `history` уходят только в DM; `dm_notes` Backend сохраняет у себя и не отдаёт на Frontend |

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

## История ходов

`TurnOrchestrator.play_turn` принимает опциональный аргумент `history` (последовательность
`TurnHistoryEntry`, тип зафиксирован аннотацией) и кладёт его в `TurnRequest.history`; без аргумента
история пустая. Словари и произвольные объекты не конвертируются: граница строгая, а неверный тип
отклоняет валидация `TurnRequest` (`pydantic.ValidationError` - ошибка вызывающего кода, не `IntegrationError`).
DM сам берёт из истории только последние 10 записей (`HISTORY_LIMIT` в `ai_dm.dm_engine`).

Backend должен передавать в `history` только **принятые** ходы, в порядке от старых к новым.
Нарратив отклонённого хода не добавляется. `dm_notes` никогда не отдаётся Frontend.

Альтернатива: оборачивать сам `DungeonMaster` в `HistoryAdapter` из `ai_dm/main.py`. В таком случае
`play_turn` вызывается без `history`, а адаптер вставляет снимок истории непосредственно в запрос
перед вызовом LLM. Оба подхода корректны; параметр в `play_turn` удобнее, если Backend сам управляет
историей.

## Сборка компонентов (`integration/factory.py`)

Backend не должен сам собирать RAG, эмбеддер и DM. Фабрика делает это по настройкам из окружения:

| Переменная | Значение | По умолчанию |
|---|---|---|
| `RULES_BOOK_PATH` | путь к книге правил (`.md`, `.txt`, `.pdf`) | обязательна |
| `RULES_MODE` | `keyword`, `rag` или `hybrid` | `keyword` |
| `RULES_CACHE_DIR` | куда кэшировать векторы (rag/hybrid) | `.rules_index` |
| `RULES_EMBEDDING_MODEL` | имя модели sentence-transformers | `paraphrase-multilingual-MiniLM-L12-v2` |

- `build_orchestrator()` создаёт `TurnOrchestrator` с настоящими Rules Service и `AIDungeonMaster`.
  Любую часть можно подменить (`dm=`, `rules=`) - так делают тесты и заглушки.
- Сборка может быть долгой: в режимах `rag` и `hybrid` при первом запуске эмбеддится вся книга
  (дальше берётся из кэша). Поэтому оркестратор создаётся **один раз при старте приложения**, а не на
  каждый запрос; сам он не хранит состояния между ходами.
- Ошибки настройки (нет книги, неизвестный режим) - `IntegrationConfigError`. Это не `IntegrationError`:
  обработчик хода, ловящий `IntegrationError` и отвечающий 502, не должен маскировать поломку конфигурации.
- Режим `keyword` не скачивает модель и стартует мгновенно (SRD, около 2000 фрагментов, около 0,4 с).

## Как использовать из Backend

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ai_dm.models import TurnHistoryEntry
from engine.dice import Dice, LocalRandomSource
from integration import IntegrationError, build_orchestrator


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.orchestrator = build_orchestrator()      # настройки из окружения, один раз при старте
    yield


app = FastAPI(lifespan=lifespan)

# внутри обработчика POST /sessions/{id}/turns:
try:
    outcome = app.state.orchestrator.play_turn(
        state,
        action,
        hidden_plot,
        Dice(LocalRandomSource()),
        room_context=room_context,
        history=session_history[-10:],
    )
except IntegrationError:
    ...                                  # 502, ничего не сохранять

if outcome.accepted:
    session_state = outcome.state
    session_history.append(TurnHistoryEntry(
        player_action=action.text,
        narrative=outcome.narration,
        dm_notes=outcome.dm_notes or "",
    ))
    ...  # compare-and-swap по revision, сохранить state, события, narration, dm_notes
else:
    ...  # outcome.errors[0].code == "STALE_REVISION" -> 409
```

## Согласование с Егорской валидацией DM

Последняя версия `ai_dm` требует от DM:
- писать `narrative`, `dm_thoughts`, `new_location` строго по-русски (с переводом терминов);
- не переводить **существующие** имена персонажей и названия предметов из `active_characters`;
- в `state_updates[].character_name` передавать имя точно, с учётом регистра и пробелов;
- сохранять всё в валидном JSON и проходить одну попытку исправления.

`Rules Service` продолжает возвращать англоязычный текст SRD как контекст правил. Перевод
терминов в русский повествование - ответственность DM (промпт), а не Rules Service. Адаптер
`integration/mapping.py` сохраняет имена персонажей и инвентаря в точности, как они есть
в `GameState`, поэтому валидация имён не ломается на стыке.

## Сбои и логи

Контракт сбоев строгий в обе стороны: обрабатываются только объявленные типы исключений, всё остальное
считается багом и пробрасывается, чтобы его нельзя было спутать с "сервис временно недоступен".

| Что произошло | Объявленный тип | Поведение | Что видит Backend |
|---|---|---|---|
| Источник правил временно недоступен (не загрузилась модель) | `rules_service.RulesUnavailableError` | ход продолжается с `NO_RULES`, `WARNING` с трассировкой | `TurnOutcome.rules_failed=True` |
| Rules Service вернул пустую строку | - | то же `NO_RULES`, это не сбой | `rules_failed=False` |
| Баг в поставщике правил (`KeyError` и т.п.) | любой другой | не перехватывается | исходное исключение (500) |
| DM сообщил об ошибке | `ai_dm.errors.AIDMError` и наследники | `DungeonMasterError`, ничего не применено | исключение с `category`, `retryable`, `retry_after` |
| Баг в DM (`TypeError` и т.п.) | любой другой | не перехватывается | исходное исключение (500) |
| DM назвал неизвестного персонажа | - | `UnmappableResponse` | исключение |
| Движок отклонил команды | - | `accepted=False`, нарратив отброшен | `errors` с кодами |
| Нет книги, неизвестный режим, модель не грузится при старте | - | `IntegrationConfigError` при `build_orchestrator()` | исключение при старте приложения |

### Метаданные ошибки DM

`DungeonMasterError` переносит безопасные данные из ошибок Егора (`ai_dm.errors`), а исходное исключение
остаётся в `__cause__` для логов. Текст сообщения формирует сам DM по своему контракту безопасных
сообщений; он не содержит запрос и скрытый сюжет.

| Источник в `ai_dm` | `category` | `retryable` |
|---|---|---|
| `ProviderError` | `authentication`, `permission`, `quota`, `rate_limit`, `unavailable`, `invalid_request`, `timeout`, `connection`, `unknown` (как у провайдера) | как у провайдера (`rate_limit`, `unavailable`) |
| `ContextBudgetError` | `context_budget` | нет |
| `ConfigurationError` | `configuration` | нет |
| `InvalidResponseError` | `invalid_response` | нет |
| прочие `AIDMError` | `unknown` | нет |

Рекомендуемое соответствие HTTP-ответам для Backend (решает Наталья; сейчас любая ошибка идёт как 502):

| `category` | Ответ |
|---|---|
| `rate_limit` | 429, заголовок `Retry-After` из `retry_after`, если есть |
| `unavailable`, `connection` | 503 |
| `timeout` | 504 |
| `authentication`, `permission`, `quota`, `invalid_request`, `configuration` | 503: проблема оператора, игроку показать общий текст, подробности только в логах |
| `invalid_response`, `context_budget`, `unknown` | 502 |

Логгер `integration.orchestrator` пишет одну строку на ход:

```text
turn session=s1 action=a1 accepted=True errors=- commands=2 rules_failed=False rules_ms=12 dm_ms=3400
```

Только идентификаторы, коды ошибок и время. Текст игрока, нарратив, заметки DM и скрытый сюжет в логи
не попадают (проверено тестом). Уровни: `INFO` - ход, `WARNING` - недоступные правила, `ERROR` - сбой DM
(в строке `category` и `retryable`, без текста) и неразрешимый ответ.

## Тесты

`python -m unittest discover -s integration/tests -t .` - 40 тестов с реальным движком и реальным
`RulesService`/`RagRulesService`, DM заменён заглушкой `FakeDM`:
- оркестратор: применение изменений, контекст для DM (правила, сюжет, состояние, история), отсечка
  устаревшей ревизии без вызова LLM, отбрасывание нарратива при отказе движка, неизвестный персонаж,
  подмена keyword-поиска на RAG;
- фабрика: настройки из окружения, все режимы правил, кэш индекса, ошибки конфигурации;
- устойчивость: недоступные правила и немаскируемые баги, категории ошибок DM, отсутствие секретов
  в логах;
- фабрика: быстрый отказ при старте, если модель эмбеддингов недоступна.

## Как Backend подключён сейчас и что стоит поправить

`backend/main.py` использует фабрику: `get_orchestrator()` с `@lru_cache(maxsize=1)` вызывает
`build_orchestrator()` и подставляется в `POST /sessions/{id}/turns` через `Depends`; тесты Backend
подменяют зависимость на оркестратор с заглушками. Через настоящий маршрут проверено:

| Ситуация | Результат |
|---|---|
| DM вернул `ProviderError(category="quota", status_code=402)` | HTTP 502, `detail`: "Исчерпана квота или требуется оплата. Проверьте лимиты и баланс у провайдера. HTTP 402."; в логе `category=quota retryable=False`; ход не применён |
| `build_orchestrator()` бросает `IntegrationConfigError` (нет `RULES_BOOK_PATH`) | на каждый запрос HTTP 500, **сборка выполняется заново на каждый запрос** (3 запроса - 3 вызова): `lru_cache` не запоминает исключения |

Следствия для Backend (его файл, решает Наталья):
- Оркестратор строится лениво, при первом запросе. В режимах `rag`/`hybrid` первый игрок оплатит загрузку
  модели и эмбеддинг книги (при холодном кэше - минуты), а при поломке конфигурации каждый запрос повторяет
  сборку. Надёжнее строить один раз при старте (`lifespan`, пример выше): приложение тогда не запустится
  с неверной конфигурацией, а не будет отвечать 500.
- Сообщение `DungeonMasterError` можно отдавать клиенту как есть (оно безопасно), а по `category` выбирать
  статус (таблица выше); сейчас любая ошибка DM - 502.

## Что остаётся Backend и не реализовано

Загрузка/сохранение состояния и compare-and-swap, идемпотентность по `action_id`, сохранение seed
кубиков (см. `docs/integration.md`). Адаптер намеренно не решает эти задачи, чтобы не залезать в зону Backend.

## Известные риски

- Нарратив LLM может описывать то, что движок отклонил; в этом случае он отбрасывается
  и игроку нужно показать ошибку или повторить запрос (политика - решение Backend).
- Если DM вернул hp_change для персонажа, который не совершал действие, мы это допускаем
  (в групповой игре это нормально); ограничение при необходимости вводится в движке.
- Предметы добавляются с `item_id == name`. Если DM изменит написание названия при удалении
  (`Ключ` vs `ключ`), `RemoveItem` отклонится. Это контракт между генеративной моделью и
  движком; решение может потребовать каталог предметов.
