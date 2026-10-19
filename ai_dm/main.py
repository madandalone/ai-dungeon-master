"""Two-turn demonstration using the existing integration contracts."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from ai_dm.dm_engine import AIDungeonMaster
from ai_dm.errors import AIDMError, ProviderError
from ai_dm.models import CampaignStartRequest, CharacterState, TurnHistoryEntry, TurnRequest, TurnResponse
from engine.dice import Dice, LocalRandomSource
from engine.models import Character, GameState, InventoryItem, PlayerAction
from integration import IntegrationError, TurnOrchestrator, TurnOutcome
from integration.ports import DungeonMaster, RulesProvider
from rules_service import RulesService


# Small Russian fixture for demonstrating keyword retrieval, not a full SRD book.
DEMO_RULES = """# Осмотр
Осмотр позволяет описать видимые детали комнаты и доступные предметы.
Не считай предмет полученным, пока персонаж не взял его.

# Инвентарь
Использовать можно предметы из текущего инвентаря персонажа.
"""


class HistoryAdapter:
    """Supply a caller-owned history snapshot to the existing DM port."""

    def __init__(self, dm: DungeonMaster, history: Sequence[TurnHistoryEntry]):
        self._dm = dm
        self._history = tuple(entry.model_copy(deep=True) for entry in history)

    def process_turn(self, request: TurnRequest) -> TurnResponse:
        # A fresh copy also protects the snapshot from a DM mutating its input.
        enriched = request.model_copy(
            deep=True,
            update={"history": [entry.model_copy(deep=True) for entry in self._history]},
        )
        return self._dm.process_turn(enriched)


@dataclass
class DemoSession:
    """In-memory caller state for this demo; production persistence belongs to Backend."""

    state: GameState
    hidden_plot: str
    room_context: str = ""
    history: list[TurnHistoryEntry] = field(default_factory=list)

    def play_turn(
        self, action: PlayerAction, dm: DungeonMaster, rules: RulesProvider, dice: Dice
    ) -> TurnOutcome:
        orchestrator = TurnOrchestrator(HistoryAdapter(dm, self.history), rules)
        outcome = orchestrator.play_turn(
            self.state, action, self.hidden_plot, dice, self.room_context
        )
        if outcome.accepted:
            entry = TurnHistoryEntry(
                player_action=action.text,
                narrative=outcome.narration,
                dm_notes=outcome.dm_notes or "",
            )
            if outcome.state.location_id != self.state.location_id:
                self.room_context = ""
            self.state = outcome.state
            self.history.append(entry)
        return outcome


def run():
    dm = AIDungeonMaster()
    hero = CharacterState(
        name="Элиас",
        hp=12,
        max_hp=12,
        inventory=["Кожаная броня", "Кинжал", "Набор воровских инструментов"],
    )
    print("Генерируем кампанию с данными монстра из справочника пятой редакции...")
    try:
        campaign = dm.generate_campaign_start(CampaignStartRequest(
            setting_theme="Затопленный склеп некроманта с саркофагом в центре зала. Герой — плут.",
            characters=[hero],
            monster_encounter="skeleton",
        ))
    except AIDMError as exc:
        print(f"Не удалось создать кампанию: {exc}")
        return

    character = Character(
        id="elias",
        name=hero.name,
        concept="Плут",
        hp=hero.hp,
        max_hp=hero.max_hp,
        stats={},
        inventory=tuple(InventoryItem(name, name, 1) for name in hero.inventory),
    )
    session = DemoSession(
        state=GameState(
            session_id="demo",
            revision=0,
            turn_number=0,
            location_id=campaign.starting_location,
            characters=(character,),
            world_flags={},
        ),
        hidden_plot=campaign.hidden_plot,
        room_context=campaign.room_context,
    )
    rules = RulesService.from_text(DEMO_RULES)
    dice = Dice(LocalRandomSource())
    print(f"\n===== КАМПАНИЯ: {campaign.campaign_title} =====")
    print(campaign.intro_narrative)

    actions = (
        "Осмотр: я изучаю саркофаг в поисках тайников или ловушек.",
        "Осмотр: я продолжаю исследование, учитывая то, что узнал на предыдущем ходу.",
    )
    for number, text in enumerate(actions, start=1):
        if number > 1 and not session.history:
            text = "Осмотр: я изучаю саркофаг в поисках тайников или ловушек."
        action = PlayerAction(
            action_id=f"demo-turn-{number}",
            session_id=session.state.session_id,
            character_id=character.id,
            text=text,
            expected_revision=session.state.revision,
        )
        print(f"\nХод {number}. Игрок: {text}")
        try:
            outcome = session.play_turn(action, dm, rules, dice)
        except IntegrationError as exc:
            error = exc.__cause__ if isinstance(exc.__cause__, ProviderError) else exc
            print(f"Ход не выполнен: {error}")
            continue
        if not outcome.accepted:
            print("Ход отклонён: " + ", ".join(error.code for error in outcome.errors))
            continue
        print(outcome.narration)
        print(f"Локация: {session.state.location_id}")
        for current in session.state.characters:
            print(f"{current.name}: Здоровье {current.hp}/{current.max_hp}")
            print("Инвентарь: " + ", ".join(f"{item.name} ×{item.quantity}" for item in current.inventory))


if __name__ == "__main__":
    run()
