"""Response checks only; game rules and state changes belong to Engine."""

import json
import re
from collections.abc import Sequence

from ai_dm.models import CampaignStartRequest, CampaignStartResponse, TurnRequest, TurnResponse


def _language_errors(fields: dict[str, str], references: Sequence[str] = ()) -> list[str]:
    """Check Russian lettering, allowing exact existing names in prose.

    This is a script check, not semantic language identification.
    """
    invalid = []
    names = sorted({name for name in references if name}, key=len, reverse=True)
    pattern = r"(?<!\w)(?:" + "|".join(re.escape(name) for name in names) + r")(?!\w)"
    for field, value in fields.items():
        text = re.sub(pattern, "", value) if names else value
        letters = [char for char in text if char.isalpha()]
        if not letters or any(char not in "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя"
                              for char in letters):
            invalid.append(field)
    if not invalid:
        return []
    return ["Напиши строго по-русски, без иностранных слов, поля: " + ", ".join(invalid) + "."]


def validate_campaign(response: CampaignStartResponse, *, request: CampaignStartRequest) -> None:
    fields = response.model_dump()
    names = {field: fields.pop(field) for field in ("campaign_title", "starting_location")}
    references = [name for character in request.characters
                  for name in (character.name, *character.inventory)]
    errors = _language_errors(names) + _language_errors(fields, references)
    if errors:
        raise ValueError(" ".join(errors))


def validate_turn(response: TurnResponse, *, request: TurnRequest) -> None:
    character_names = [character.name for character in request.game_state.active_characters]
    allowed_names = set(character_names)
    names_json = json.dumps(character_names, ensure_ascii=False)
    inventory = {item for character in request.game_state.active_characters
                 for item in character.inventory}
    prose = {"narrative": response.narrative}
    if response.dm_thoughts.strip():
        prose["dm_thoughts"] = response.dm_thoughts
    errors = _language_errors(prose, [*character_names, *inventory])
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
