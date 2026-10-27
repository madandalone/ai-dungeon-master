"""Response checks only; game rules and state changes belong to Engine."""

import json
import re
from collections.abc import Sequence

from ai_dm.models import CampaignStartRequest, CampaignStartResponse, TurnRequest, TurnResponse


_RUSSIAN = frozenset("АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя")
_LOOKALIKES = dict(zip("ABCEHKMOPTXYaceopxy", "АВСЕНКМОРТХУасеорху"))
_WORDS = re.compile(r"\w+")


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


def validate_campaign(
    response: CampaignStartResponse, *, request: CampaignStartRequest,
) -> CampaignStartResponse:
    """Return a normalized, validated copy without modifying caller-owned models."""
    references = [name for character in request.characters
                  for name in (character.name, *character.inventory)]
    response = response.model_copy(update={
        field: _normalize_text(value, () if field in ("campaign_title", "starting_location") else references)
        for field, value in response.model_dump().items()
    })
    fields = response.model_dump()
    names = {field: fields.pop(field) for field in ("campaign_title", "starting_location")}
    errors = _language_errors(names) + _language_errors(fields, references)
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
    references = [*character_names, *inventory, request.game_state.location]
    response = response.model_copy(deep=True)
    response.narrative = _normalize_text(response.narrative, references)
    response.dm_thoughts = _normalize_text(response.dm_thoughts, references)
    if response.new_location is not None and response.new_location != request.game_state.location:
        response.new_location = _normalize_text(response.new_location)
    for update in response.state_updates:
        update.items_added = [item if item in inventory else _normalize_text(item) for item in update.items_added]
    prose = {"narrative": response.narrative}
    if response.dm_thoughts.strip():
        prose["dm_thoughts"] = response.dm_thoughts
    errors = _language_errors(prose, references)
    generated_names = {}
    if response.new_location is not None and response.new_location != request.game_state.location:
        generated_names["new_location"] = response.new_location
    for index, update in enumerate(response.state_updates):
        for item_index, item in enumerate(update.items_added):
            if item not in inventory:
                generated_names[f"state_updates[{index}].items_added[{item_index}]"] = item
    errors.extend(_language_errors(generated_names))
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
