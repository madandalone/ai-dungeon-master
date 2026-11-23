"""Response checks only; game rules and state changes belong to Engine."""

import json
import re
from collections import Counter
from collections.abc import Sequence

from ai_dm.models import (
    CampaignStartRequest,
    CampaignStartResponse,
    CharacterState,
    CheckDecision,
    CheckResolution,
    TurnRequest,
    TurnPlan,
    TurnResponse,
)


_RUSSIAN = frozenset("АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя")
_LOOKALIKES = dict(zip("ABCEHKMOPTXYaceopxy", "АВСЕНКМОРТХУасеорху"))
_WORDS = re.compile(r"\w+")
_EXPLICIT_UNCERTAINTY = (
    "пытаюсь", "пытается", "попытка", "рискуя", "риск", "опасн", "сложн",
    "try", "attempt", "risk", "dangerous", "difficult",
)

# D&D 5e difficulty classes: 5 is very easy, 30 is nearly impossible.
MIN_DIFFICULTY = 5
MAX_DIFFICULTY = 30
# A run of this many consecutive words copied from the hidden plot counts as quoting it.
QUOTE_WORDS = 6


def _reference_pattern(references: Sequence[str]) -> re.Pattern[str] | None:
    names = sorted({name for name in references if name}, key=lambda name: (-len(name), name))
    if not names:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(name) for name in names) + r")(?!\w)")


def _normalize_text(text: str, references: Sequence[str] = ()) -> str:
    """Fix only visual Latin lookalikes in predominantly Russian words.

    Exact references are protected before processing words; this is not translation.
    """
    def normalize_word(match: re.Match[str]) -> str:
        word = match.group()
        russian = sum(char in _RUSSIAN for char in word)
        latin = sum(char in _LOOKALIKES for char in word)
        if russian > latin > 0 and russian + latin == len(word):
            return "".join(_LOOKALIKES.get(char, char) for char in word)
        return word

    pattern = _reference_pattern(references)
    if pattern is None:
        return _WORDS.sub(normalize_word, text)
    parts = []
    start = 0
    for match in pattern.finditer(text):
        parts.extend((_WORDS.sub(normalize_word, text[start:match.start()]), match.group()))
        start = match.end()
    parts.append(_WORDS.sub(normalize_word, text[start:]))
    return "".join(parts)


def _language_errors(fields: dict[str, str], references: Sequence[str] = ()) -> list[str]:
    """Check Russian lettering, allowing exact existing names in prose.

    This is a script check, not semantic language identification.
    """
    invalid = []
    pattern = _reference_pattern(references)
    for field, value in fields.items():
        text, reference_count = pattern.subn("", value) if pattern else (value, 0)
        letters = [char for char in text if char.isalpha()]
        if (not letters and not reference_count) or any(char not in _RUSSIAN for char in letters):
            invalid.append(field)
    if not invalid:
        return []
    return ["Напиши строго по-русски, без иностранных слов, поля: " + ", ".join(invalid) + "."]


def _inventory_errors(response: TurnResponse, characters: Sequence[CharacterState]) -> list[str]:
    """Reject removals the Engine would refuse, so the one repair attempt can fix them.

    Deltas are replayed in the order integration maps them (additions, then removals),
    so an item added earlier in the same turn may be removed. Engine still decides the result.
    """
    held = {character.name: Counter(character.inventory) for character in characters}
    missing: dict[str, Counter] = {}
    for update in response.state_updates:
        inventory = held.get(update.character_name)
        if inventory is None:
            continue  # Reported separately as an unknown character_name.
        inventory.update(update.items_added)
        for item, quantity in Counter(update.items_removed).items():
            shortage = quantity - inventory[item]
            if shortage > 0:
                missing.setdefault(update.character_name, Counter())[item] += shortage
            inventory[item] = max(0, inventory[item] - quantity)
    errors = []
    for name, items in missing.items():
        available = sorted(item for character in characters if character.name == name
                           for item in character.inventory)
        errors.append(
            f"state_updates для {json.dumps(name, ensure_ascii=False)} убирает предметы, "
            f"которых у персонажа нет: {json.dumps(sorted(items.elements()), ensure_ascii=False)}. "
            f"Инвентарь персонажа: {json.dumps(available, ensure_ascii=False)}. "
            "В items_removed указывай только эти названия точно и не больше имеющегося количества."
        )
    return errors


def _word_runs(texts: Sequence[str]) -> set[tuple[str, ...]]:
    runs = set()
    for text in texts:
        words = _WORDS.findall(text.casefold().replace("ё", "е"))
        runs.update(tuple(words[start:start + QUOTE_WORDS])
                    for start in range(len(words) - QUOTE_WORDS + 1))
    return runs


def _quote_errors(public: dict[str, str], secret: str, known: Sequence[str]) -> list[str]:
    """Catch verbatim quoting of the hidden plot in player-facing text.

    Wording the players have already seen is not a leak. A paraphrased secret is
    not detected: that still needs the prompt and human review.
    """
    private = _word_runs([secret]) - _word_runs(known)
    quoted = [field for field, value in public.items() if private & _word_runs([value])]
    if not quoted:
        return []
    return [
        "Поля " + ", ".join(quoted) + " дословно повторяют скрытый сюжет. Не цитируй его: "
        "опиши своими словами только то, что персонажи узнали или увидели сами."
    ]


def _check_errors(response: TurnResponse, request: TurnRequest) -> list[str]:
    """The model only proposes a check; the stat and difficulty must be ones Engine can roll."""
    if request.check_result is not None:
        if response.requires_check or response.check is not None:
            return [
                "Проверка в этом ходе уже проведена. Верни requires_check: false и check: null "
                "и опиши исход по переданному результату броска."
            ]
        return []
    if not response.requires_check:
        if response.check is not None:
            return ["check задан при requires_check: false. Верни check: null или requires_check: true."]
        return []
    if response.check is None:
        return ["requires_check: true требует объект check с полями stat и difficulty."]
    actor = next((character for character in request.game_state.active_characters
                  if character.name == request.acting_character), None)
    available = (actor.stats or actor.abilities) if actor is not None else {}
    if not available:
        return [
            "Проверка недоступна: действующий персонаж не указан или у него нет характеристик. "
            "Верни requires_check: false и check: null; не придумывай бросок и его результат."
        ]
    errors = []
    if response.check.stat not in available:
        errors.append(
            f"check.stat {json.dumps(response.check.stat, ensure_ascii=False)} нет у персонажа "
            f"{json.dumps(actor.name, ensure_ascii=False)}. Допустимые характеристики: "
            f"{json.dumps(sorted(available), ensure_ascii=False)}. Скопируй название точно."
        )
    if not MIN_DIFFICULTY <= response.check.difficulty <= MAX_DIFFICULTY:
        errors.append(
            f"check.difficulty должно быть целым числом от {MIN_DIFFICULTY} до {MAX_DIFFICULTY}, "
            f"получено {response.check.difficulty}."
        )
    if response.state_updates or response.new_location is not None:
        errors.append(
            "При requires_check: true не предлагай изменения: верни state_updates: [] и "
            "new_location: null. Последствия ты опишешь после броска."
        )
    return errors


def validate_campaign(
    response: CampaignStartResponse, *, request: CampaignStartRequest,
) -> CampaignStartResponse:
    """Return a normalized, validated copy without modifying caller-owned models."""
    references = [name for character in request.characters for name in (
        character.name, *character.inventory, *character.stats, *character.abilities,
    )]
    response = response.model_copy(update={
        field: _normalize_text(value, () if field in ("campaign_title", "starting_location") else references)
        for field, value in response.model_dump().items()
    })
    fields = response.model_dump()
    names = {field: fields.pop(field) for field in ("campaign_title", "starting_location")}
    errors = _language_errors(names) + _language_errors(fields, references)
    # room_context is the observable scene, so wording it shares with the plot is not secret.
    errors.extend(_quote_errors(
        {"intro_narrative": response.intro_narrative}, response.hidden_plot,
        [response.room_context, request.setting_theme],
    ))
    if errors:
        raise ValueError(" ".join(errors))
    return response


def validate_turn(response: TurnResponse, *, request: TurnRequest) -> TurnResponse:
    """Normalize generated text only, then validate references and lettering."""
    character_names = [character.name for character in request.game_state.active_characters]
    allowed_names = set(character_names)
    names_json = json.dumps(character_names, ensure_ascii=False)
    inventory = {item for character in request.game_state.active_characters
                 for item in character.inventory}
    stats = {
        stat for character in request.game_state.active_characters
        for stat in (*character.stats, *character.abilities)
    }
    references = [*character_names, *inventory, *stats, request.game_state.location]
    response = response.model_copy(deep=True)
    response.narrative = _normalize_text(response.narrative, references)
    response.dm_thoughts = _normalize_text(response.dm_thoughts, references)
    if response.new_location is not None and response.new_location != request.game_state.location:
        response.new_location = _normalize_text(response.new_location)
    for update in response.state_updates:
        update.items_added = [item if item in inventory else _normalize_text(item) for item in update.items_added]
    prose = {}
    # While a check is pending the outcome is unknown, so there may be nothing to narrate yet.
    if response.narrative.strip() or not response.requires_check:
        prose["narrative"] = response.narrative
    if response.dm_thoughts.strip():
        prose["dm_thoughts"] = response.dm_thoughts
    errors = _language_errors(prose, references)
    errors.extend(_check_errors(response, request))
    errors.extend(_quote_errors(
        {"narrative": response.narrative}, request.hidden_plot,
        [request.game_state.room_context, request.player_action,
         *(text for entry in request.history for text in (entry.player_action, entry.narrative))],
    ))
    generated_names = {}
    if response.new_location is not None and response.new_location != request.game_state.location:
        generated_names["new_location"] = response.new_location
    for index, update in enumerate(response.state_updates):
        for item_index, item in enumerate(update.items_added):
            if item not in inventory:
                generated_names[f"state_updates[{index}].items_added[{item_index}]"] = item
    # Engine refuses blank ids; say so instead of reporting them as a language problem.
    blank = [field for field, value in generated_names.items() if not value.strip()]
    if blank:
        errors.append(
            "Пустые значения недопустимы, поля: " + ", ".join(blank) + ". "
            "Если герой не перемещается, верни new_location: null; не добавляй пустые предметы."
        )
    errors.extend(_language_errors({
        field: value for field, value in generated_names.items() if field not in blank
    }))
    errors.extend(_inventory_errors(response, request.game_state.active_characters))
    unknown_names = sorted({
        update.character_name for update in response.state_updates
        if update.character_name not in allowed_names
    })
    if unknown_names:
        errors.append(
            "state_updates содержит неизвестные character_name: "
            f"{json.dumps(unknown_names, ensure_ascii=False)}. "
            f"Допустимые имена: {names_json}. "
            "Скопируй имя нужного персонажа точно, сохранив регистр, пробелы и скобки."
        )
    if errors:
        raise ValueError(" ".join(errors))
    return response


def _check_error(ability: str, request: TurnRequest) -> str | None:
    actor = next(
        (character for character in request.game_state.active_characters
         if character.name == request.acting_character),
        None,
    )
    if actor is None or ability not in (actor.abilities or actor.stats):
        return f"Характеристика {ability!r} отсутствует у действующего персонажа."
    return None


def validate_check_decision(decision: CheckDecision, *, request: TurnRequest) -> CheckDecision:
    """The decision must name an ability the hero has, and must not wave away an openly risky action."""
    if decision.needs_check:
        error = _check_error(decision.ability, request)
        if error:
            raise ValueError(error)
        return decision
    action = request.player_action.casefold()
    if any(marker in action for marker in _EXPLICIT_UNCERTAINTY):
        raise ValueError(
            "Игрок явно описал попытку, риск или опасность. Верни needs_check: true и заполни "
            "ability, difficulty и reason."
        )
    return decision


def validate_turn_plan(response: TurnPlan, *, request: TurnRequest) -> TurnPlan:
    if response.status == "resolved":
        action = request.player_action.casefold()
        if any(marker in action for marker in _EXPLICIT_UNCERTAINTY):
            raise ValueError(
                "Игрок явно описал попытку, риск или опасность. Верни status=check; "
                "не определяй исход и изменения состояния до броска Engine."
            )
        response.response = validate_turn(response.response, request=request)
        return response
    error = _check_error(response.check.ability, request)
    if error:
        raise ValueError(error)
    return response


def validate_check_resolution(
    response: CheckResolution, *, request: TurnRequest, expected_outcome: str,
) -> CheckResolution:
    if response.outcome != expected_outcome:
        raise ValueError(
            f"Исход должен совпадать с результатом Engine: {expected_outcome}."
        )
    if response.status == "resolved":
        response.response = validate_turn(response.response, request=request)
        return response
    error = _check_error(response.check.ability, request)
    if error:
        raise ValueError(error)
    return response
