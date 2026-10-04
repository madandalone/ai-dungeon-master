from ai_dm.models import CharacterState, GameState, TurnRequest
from ai_dm.dm_engine import process_turn


def run():
    # Создаём тестового персонажа и окружение
    hero = CharacterState(
        name="Элиас (Плут)",
        hp=12,
        max_hp=12,
        inventory=["Кинжал", "Набор отмычек", "Факел"]
    )

    state = GameState(
        location="Подземелье гоблинов - Входная зала",
        room_context="Тёмный сырой зал с каменными колоннами. В углу стоит старый обитый железом сундук.",
        active_characters=[hero]
    )

    request = TurnRequest(
        rules_summary="Простая d20 система. Ловушки наносят урон, если их не проверить.",
        hidden_plot="В замке сундука спрятана игла с ядом (-3 HP). Внутри лежит Рубин Душ.",
        game_state=state,
        player_action="Я подбегаю к сундуку и торопливо вскрываю замок отмычками, не осматривая его на ловушки."
    )

    print("Отправка хода в AI Dungeon Master...\n")
    response = process_turn(request)

    print("=== ТЕКСТ ДЛЯ ИГРОКА (Frontend) ===")
    print(response.narrative)

    print("\n=== ИЗМЕНЕНИЯ ДЛЯ ДВИЖКА (Game Engine) ===")
    for delta in response.state_updates:
        print(f"Персонаж: {delta.character_name}")
        print(f"  Изменение HP: {delta.hp_change}")
        print(f"  Получено предметов: {delta.items_added}")
        print(f"  Потеряно предметов: {delta.items_removed}")

    print("\n=== СКРЫТЫЕ МЫСЛИ МАСТЕРА ===")
    print(response.dm_thoughts)


if __name__ == "__main__":
    run()