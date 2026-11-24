import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as backend
from ai_dm.errors import ProviderError
from ai_dm.models import CampaignStartResponse, StateDelta, TurnResponse
from backend.storage import SQLiteSessionStore
from backend.tests.helpers import create_hero
from integration.factory import build_orchestrator

PLOT = "the abbot feeds the village to the crypt"
INTRO = "Вы входите в затопленный склеп."


class Rules:
    def get_rules(self, query):
        return "rule"


class WritingDM:
    """Stand-in DM that can write campaigns and records everything it is asked."""

    def __init__(self, fail_campaign_once=False):
        self.campaigns, self.turns = [], []
        self.fail_campaign_once = fail_campaign_once

    def generate_campaign_start(self, request):
        self.campaigns.append(request)
        if self.fail_campaign_once and len(self.campaigns) == 1:
            raise ProviderError(category="quota", status_code=402)
        return CampaignStartResponse(
            campaign_title="Склеп некроманта", hidden_plot=PLOT, starting_location="Затопленный склеп",
            room_context="Вода по колено, в центре зала саркофаг.", intro_narrative=INTRO,
        )

    def process_turn(self, request):
        self.turns.append(request)
        return TurnResponse(
            narrative="Вы осматриваетесь.", state_updates=[StateDelta(character_name="Hero", hp_change=-1)],
            new_location=None, dm_thoughts="",
        )


class StubDM:
    """A DM with no campaign ability, like the stubs used in demos."""

    def process_turn(self, request):
        return TurnResponse(narrative="ok", state_updates=[], new_location=None, dm_thoughts="")


class CampaignStartApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "game.db"
        self.store = SQLiteSessionStore(self.db_path)
        patcher = patch.object(backend, "get_session_store", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.reset_memory()
        self.hero_ids = {}
        self.client = TestClient(backend.app)
        self.addCleanup(self.client.close)
        self.addCleanup(backend.app.dependency_overrides.clear)

    def reset_memory(self):
        backend.sessions.clear()
        backend.game_states.clear()
        backend.game_histories.clear()
        backend.turn_executor = backend.IdempotentExecutor()

    def use(self, dm):
        backend.app.dependency_overrides[backend.get_orchestrator] = lambda: build_orchestrator(dm=dm, rules=Rules())
        return dm

    def new_session(self, **body):
        response = self.client.post("/sessions", json={"name": "Quest", **body})
        self.assertEqual(response.status_code, 200)
        session_id = response.json()["id"]
        hero = create_hero(self.client, session_id)
        self.hero_ids[session_id] = hero["id"]
        return session_id

    def turn(self, session_id, text="Я осматриваюсь", **extra):
        return self.client.post(
            f"/sessions/{session_id}/turns",
            json={"character_id": self.hero_ids[session_id], "text": text, **extra},
        )

    def test_the_campaign_endpoint_writes_stores_and_hides_the_plot(self):
        dm = self.use(WritingDM())
        sid = self.new_session()
        response = self.client.post(f"/sessions/{sid}/campaign", json={"theme": "Склеп", "monster": "skeleton"})
        body = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual((body["created"], body["title"], body["intro"]), (True, "Склеп некроманта", INTRO))
        self.assertEqual(body["state"]["location"], "Затопленный склеп")
        self.assertEqual(body["state"]["revision"], 2)
        self.assertEqual([entry["kind"] for entry in body["state"]["log"]], ["master"], "no empty player line")
        self.assertNotIn(PLOT, response.text)
        request = dm.campaigns[0]
        self.assertEqual((request.setting_theme, request.monster_encounter), ("Склеп", "skeleton"))
        self.assertEqual([c.name for c in request.characters], ["Hero"])

        record = self.store.load_campaign(sid)
        self.assertEqual((record["started"], record["hidden_plot"]), (True, PLOT))
        self.assertEqual(record["room_context"], "Вода по колено, в центре зала саркофаг.")

    def test_session_setup_is_stored_exposed_safely_and_used_to_start(self):
        dm = self.use(WritingDM())
        sid = self.new_session(
            theme="Городское фэнтези в старой пекарне",
            opening_hook="Ночью из печи слышен голос пропавшего пекаря.",
        )

        before = self.client.get(f"/sessions/{sid}/state").json()
        self.assertEqual(before["campaign"], {
            "started": False,
            "title": "",
            "theme": "Городское фэнтези в старой пекарне",
            "opening_hook": "Ночью из печи слышен голос пропавшего пекаря.",
        })

        response = self.client.post(f"/sessions/{sid}/campaign", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(dm.campaigns[0].setting_theme, "Городское фэнтези в старой пекарне")
        self.assertEqual(dm.campaigns[0].opening_hook, "Ночью из печи слышен голос пропавшего пекаря.")
        public = response.json()["state"]
        self.assertEqual(public["campaign"]["started"], True)
        self.assertEqual(public["campaign"]["title"], "Склеп некроманта")
        self.assertEqual(public["location"], "Затопленный склеп")
        self.assertEqual(public["log"][0]["text"], INTRO)

    def test_name_only_clients_keep_the_default_setup(self):
        self.use(WritingDM())
        response = self.client.post("/sessions", json={"name": "Old client"})
        self.assertEqual(response.status_code, 200)
        sid = response.json()["id"]
        campaign = self.client.get(f"/sessions/{sid}/state").json()["campaign"]
        self.assertEqual(campaign["theme"], backend.DEFAULT_THEME)
        self.assertEqual(campaign["opening_hook"], "")

    def test_repeating_the_request_does_not_ask_the_dm_again(self):
        dm = self.use(WritingDM())
        sid = self.new_session()
        self.client.post(f"/sessions/{sid}/campaign", json={})
        again = self.client.post(f"/sessions/{sid}/campaign", json={}).json()
        self.assertEqual((again["created"], again["title"], again["state"]["revision"]), (False, "Склеп некроманта", 2))
        self.assertEqual(len(dm.campaigns), 1)

    def test_a_default_theme_is_used_when_none_is_sent(self):
        dm = self.use(WritingDM())
        self.client.post(f"/sessions/{self.new_session()}/campaign")
        self.assertEqual(dm.campaigns[0].setting_theme, backend.DEFAULT_THEME)

    def test_the_first_turn_of_a_new_game_starts_the_campaign_and_later_turns_use_it(self):
        dm = self.use(WritingDM())
        sid = self.new_session()
        first = self.turn(sid, expected_revision=1)
        second = self.turn(sid, "Я иду дальше", expected_revision=first.json()["revision"])

        self.assertEqual((first.status_code, first.json()["accepted"]), (200, True))
        self.assertTrue(second.json()["accepted"])
        self.assertEqual(len(dm.campaigns), 1, "the campaign is written once")
        for request in dm.turns:
            self.assertEqual(request.hidden_plot, PLOT)
            self.assertEqual(request.game_state.room_context, "Вода по колено, в центре зала саркофаг.")
            self.assertEqual(request.game_state.location, "Затопленный склеп")
        self.assertEqual(second.json()["revision"], 4, "character + campaign start + two turns")
        self.assertNotIn(PLOT, first.text + second.text)

    def test_a_client_that_saw_revision_zero_is_not_rejected_because_the_campaign_moved_it(self):
        self.use(WritingDM())
        sid = self.new_session()
        self.assertTrue(self.turn(sid, expected_revision=1).json()["accepted"])

    def test_a_stale_client_is_still_rejected_after_the_campaign_started(self):
        self.use(WritingDM())
        sid = self.new_session()
        self.turn(sid, expected_revision=1)
        stale = self.turn(sid, "Ещё раз", expected_revision=1).json()
        self.assertFalse(stale["accepted"])
        self.assertEqual([error["code"] for error in stale["errors"]], ["STALE_REVISION"])

    def test_an_explicit_plot_at_creation_is_kept_and_no_campaign_is_written(self):
        dm = self.use(WritingDM())
        sid = self.new_session(hidden_plot="A plot chosen by the author.")
        self.assertTrue(self.turn(sid).json()["accepted"])
        self.assertEqual(dm.campaigns, [])
        self.assertEqual(dm.turns[0].hidden_plot, "A plot chosen by the author.")

    def test_a_dm_that_cannot_write_campaigns_keeps_the_default_flow(self):
        self.use(StubDM())
        sid = self.new_session()
        self.assertTrue(self.turn(sid).json()["accepted"])
        refused = self.client.post(f"/sessions/{sid}/campaign", json={})
        self.assertEqual((refused.status_code, refused.json()["detail"]["code"]), (409, "CAMPAIGN_UNAVAILABLE"))
        self.assertFalse(self.store.load_campaign(sid)["started"])

    def test_a_dm_failure_leaves_no_campaign_and_can_be_retried(self):
        dm = self.use(WritingDM(fail_campaign_once=True))
        sid = self.new_session()
        failed = self.client.post(f"/sessions/{sid}/campaign", json={})
        self.assertEqual(failed.status_code, 502)
        self.assertIn("402", failed.json()["detail"])
        self.assertEqual((self.store.load_campaign(sid)["started"], backend.game_states[sid].revision), (False, 1))
        self.assertTrue(self.client.post(f"/sessions/{sid}/campaign", json={}).json()["created"])
        self.assertEqual(len(dm.campaigns), 2)

    def test_a_failed_campaign_does_not_block_nor_lose_the_first_turn_retry(self):
        dm = self.use(WritingDM(fail_campaign_once=True))
        sid = self.new_session()
        self.assertEqual(self.turn(sid, action_id="a-1").status_code, 502)
        retry = self.turn(sid, action_id="a-1")
        self.assertEqual((retry.status_code, retry.json()["accepted"]), (200, True))
        self.assertEqual(len(dm.campaigns), 2)

    def test_the_campaign_survives_a_restart(self):
        self.use(WritingDM())
        sid = self.new_session()
        self.client.post(f"/sessions/{sid}/campaign", json={})

        self.reset_memory()
        restored = SQLiteSessionStore(self.db_path)
        with patch.object(backend, "get_session_store", return_value=restored):
            state = self.client.get(f"/sessions/{sid}/state").json()
        self.assertEqual(state["location"], "Затопленный склеп")
        self.assertEqual([(e["kind"], e["text"]) for e in state["log"]], [("master", INTRO)])
        self.assertEqual(restored.load_campaign(sid)["hidden_plot"], PLOT)

    def test_unknown_session_is_404(self):
        self.use(WritingDM())
        self.assertEqual(self.client.post("/sessions/nope/campaign", json={}).status_code, 404)

    def test_deleting_a_session_removes_its_campaign(self):
        self.use(WritingDM())
        sid = self.new_session()
        self.client.post(f"/sessions/{sid}/campaign", json={})
        self.assertEqual(self.client.delete(f"/sessions/{sid}").status_code, 200)
        self.assertIsNone(self.store.load_campaign(sid))


class CampaignStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SQLiteSessionStore(Path(self.tmp.name) / "s.db")

    def test_a_save_against_a_moved_revision_is_refused_and_changes_nothing(self):
        from dataclasses import replace

        from ai_dm.models import TurnHistoryEntry
        from backend.storage import StateRevisionConflict
        from engine.models import Character, GameState

        state = GameState("s1", 0, 0, "start", (Character("c", "Hero", "", 10, 10, {}, ()),), {})
        self.store.save_session("s1", "Quest", state)
        entry = TurnHistoryEntry(player_action="", narrative="intro")
        with self.assertRaises(StateRevisionConflict):
            self.store.save_campaign(
                "s1", replace(state, revision=5), entry, title="T", hidden_plot="P", room_context="R"
            )
        self.assertEqual(self.store.load_campaign("s1"), {
            "started": False, "title": "", "hidden_plot": "Test campaign", "room_context": "",
            "theme": backend.DEFAULT_THEME, "opening_hook": "",
        })
        self.assertEqual(self.store.load_session("s1")[2], [])

    def test_unknown_session_has_no_campaign(self):
        self.assertIsNone(self.store.load_campaign("missing"))


if __name__ == "__main__":
    unittest.main()


class AuthoredCampaignApiTests(CampaignStartApiTests):
    """Authored campaigns, the empty party and the .env loading, on the same fixtures."""

    AUTHORED = {
        "title": "Сундук в сторожке",
        "hidden_plot": "В сундуке лежит медный ключ. Других секретов нет.",
        "starting_location": "Сторожка",
        "room_context": "Одна комната, деревянный стол и старый сундук с заклинившей крышкой.",
        "intro_narrative": "Вы стоите в небольшой пустой сторожке. Перед вами старый сундук.",
    }

    def test_an_authored_campaign_is_stored_without_calling_the_dm(self):
        dm = self.use(WritingDM())
        sid = self.new_session()
        response = self.client.post(f"/sessions/{sid}/campaign", json={"authored": self.AUTHORED})
        body = response.json()

        self.assertEqual((response.status_code, body["created"], body["title"]), (200, True, "Сундук в сторожке"))
        self.assertEqual(dm.campaigns, [])
        self.assertEqual(body["state"]["location"], "Сторожка")
        self.assertEqual([e["text"] for e in body["state"]["log"]], [self.AUTHORED["intro_narrative"]])
        self.assertNotIn(self.AUTHORED["hidden_plot"], response.text)
        record = self.store.load_campaign(sid)
        self.assertEqual((record["hidden_plot"], record["room_context"]), (
            self.AUTHORED["hidden_plot"], self.AUTHORED["room_context"]))

    def test_the_authored_plot_and_room_reach_every_turn(self):
        dm = self.use(WritingDM())
        sid = self.new_session()
        self.client.post(f"/sessions/{sid}/campaign", json={"authored": self.AUTHORED})
        self.assertTrue(self.turn(sid).json()["accepted"])
        request = dm.turns[0]
        self.assertEqual(request.hidden_plot, self.AUTHORED["hidden_plot"])
        self.assertEqual(request.game_state.room_context, self.AUTHORED["room_context"])
        self.assertEqual(dm.campaigns, [])

    def test_an_authored_campaign_works_with_a_dm_that_cannot_write_campaigns(self):
        self.use(StubDM())
        sid = self.new_session()
        created = self.client.post(f"/sessions/{sid}/campaign", json={"authored": self.AUTHORED})
        self.assertEqual((created.status_code, created.json()["created"]), (200, True))

    def test_a_second_request_keeps_the_first_campaign(self):
        self.use(StubDM())
        sid = self.new_session()
        self.client.post(f"/sessions/{sid}/campaign", json={"authored": self.AUTHORED})
        other = {**self.AUTHORED, "title": "Другая", "hidden_plot": "Иной сюжет."}
        again = self.client.post(f"/sessions/{sid}/campaign", json={"authored": other}).json()
        self.assertEqual((again["created"], again["title"]), (False, "Сундук в сторожке"))
        self.assertEqual(self.store.load_campaign(sid)["hidden_plot"], self.AUTHORED["hidden_plot"])

    def test_incomplete_or_blank_authored_fields_are_rejected_by_validation(self):
        self.use(StubDM())
        sid = self.new_session()
        for field in self.AUTHORED:
            for bad in ("", "   "):
                response = self.client.post(f"/sessions/{sid}/campaign", json={"authored": {**self.AUTHORED, field: bad}})
                self.assertEqual(response.status_code, 422, (field, bad))
        missing = {k: v for k, v in self.AUTHORED.items() if k != "hidden_plot"}
        self.assertEqual(self.client.post(f"/sessions/{sid}/campaign", json={"authored": missing}).status_code, 422)
        self.assertFalse(self.store.load_campaign(sid)["started"])

    def test_a_campaign_needs_a_hero_and_says_so_instead_of_failing(self):
        self.use(WritingDM())
        empty = self.client.post("/sessions", json={"name": "Пустая"}).json()["id"]
        for body in ({}, {"authored": self.AUTHORED}):
            response = self.client.post(f"/sessions/{empty}/campaign", json=body)
            self.assertEqual((response.status_code, response.json()["detail"]["code"]), (409, "NO_CHARACTERS"), body)
        self.assertFalse(self.store.load_campaign(empty)["started"])

    def test_a_turn_in_a_session_without_heroes_is_rejected_as_a_normal_turn_not_a_crash(self):
        dm = self.use(WritingDM())
        empty = self.client.post("/sessions", json={"name": "Пустая"}).json()["id"]
        response = self.client.post(f"/sessions/{empty}/turns", json={"character_id": "nobody", "text": "Привет"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["errors"][0]["code"], "INVALID_CHARACTER")
        self.assertEqual(dm.campaigns, [], "no campaign is written for an empty party")


class EnvironmentLoadingTests(unittest.TestCase):
    """python-dotenv finds .env next to the calling code (the repository root), not in the working directory."""

    def settings_with_dotenv(self, environment):
        import os

        import dotenv
        from integration import RulesSettings

        original = dotenv.load_dotenv
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("RULES_BOOK_PATH=from-dotenv.md\nRULES_MODE=keyword\n", encoding="utf-8")
            loader = lambda *args, **kwargs: original(dotenv_path=env_file)
            with patch.dict(os.environ, environment, clear=True), patch("dotenv.load_dotenv", loader):
                return RulesSettings.from_env()

    def test_settings_come_from_dotenv_when_the_environment_has_none(self):
        self.assertEqual(self.settings_with_dotenv({}).book_path, Path("from-dotenv.md"))

    def test_the_environment_wins_over_dotenv(self):
        self.assertEqual(self.settings_with_dotenv({"RULES_BOOK_PATH": "explicit.md"}).book_path, Path("explicit.md"))

    def test_an_explicit_mapping_never_touches_dotenv(self):
        from integration import RulesSettings

        with patch("dotenv.load_dotenv", side_effect=AssertionError("must not be called")):
            self.assertEqual(RulesSettings.from_env({"RULES_BOOK_PATH": "b.md"}).book_path, Path("b.md"))

    def test_the_backend_loads_dotenv_before_anything_reads_the_environment(self):
        import inspect

        source = inspect.getsource(backend)
        self.assertLess(source.index("load_dotenv()"), source.index("app = FastAPI("))
