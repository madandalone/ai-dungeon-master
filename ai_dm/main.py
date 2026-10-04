from ai_dm.models import CharacterState, GameState, TurnRequest, CampaignStartRequest
from ai_dm.dm_engine import generate_campaign_start, process_turn


def run():
    print("Подключаемся к D&D 5e API и запрашиваем монстра 'skeleton'...")
    from ai_dm.dnd_api import DnD5eClient
    monster_data = DnD5eClient.get_monster("skeleton")
    print(f"-> УСПЕШНО получены данные из D&D API: {monster_data}\n")

    # 1. Задаем стартовую группу персонажей
    hero = CharacterState(
        name="Элиас (Плут)",
        hp=12,
        max_hp=12,
        inventory=["Кожаная броня", "Кинжал", "Набор воровских инструментов"]
    )

    print("1. Подключаемся к D&D 5e API и генерируем кампанию...")
    start_req = CampaignStartRequest(
        setting_theme="Затопленный склеп некроманта в проклятом лесу с саркофагом в центре зала",
        characters=[hero],
        monster_encounter="skeleton"  # Подтянет реального скелета из dnd5eapi.co
    )
    campaign = generate_campaign_start(start_req)

    print(f"\n===== КАМПАНИЯ: {campaign.campaign_title} =====")
    print(f"Локация: {campaign.starting_location}")
    print(f"Скрытый сюжет мастера: {campaign.hidden_plot}\n")
    print(f"ВВЕДЕНИЕ ДЛЯ ИГРОКА:\n{campaign.intro_narrative}\n")

    # 2. Игрок делает первое действие в только что созданной локации
    player_action = "Я аккуратно осматриваю саркофаг в центре зала в поисках тайников или ловушек."
    print(f"Игрок решает: \"{player_action}\"\n")

    current_state = GameState(
        location=campaign.starting_location,
        room_context=campaign.room_context,
        active_characters=[hero]
    )

    turn_req = TurnRequest(
        rules_summary="D&D 5e SRD rules. Внимание к деталям снижает шанс активации ловушек.",
        hidden_plot=campaign.hidden_plot,
        game_state=current_state,
        player_action=player_action
    )

    print("2. AI Dungeon Master рассчитывает последствия хода...")
    turn_res = process_turn(turn_req)

    print("\n===== РЕЗУЛЬТАТ ХОДА =====")
    print(turn_res.narrative)

    print("\n===== ИЗМЕНЕНИЯ СОСТОЯНИЯ (Game Engine) =====")
    for delta in turn_res.state_updates:
        print(f"Персонаж: {delta.character_name} | HP: {delta.hp_change} | Новые вещи: {delta.items_added}")


if __name__ == "__main__":
    run()