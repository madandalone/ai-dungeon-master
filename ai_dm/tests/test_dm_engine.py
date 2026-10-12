import copy
import importlib.util
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from openai import OpenAI
from pydantic import ValidationError

from ai_dm import dm_engine
from ai_dm.dm_engine import AIDungeonMaster
from ai_dm.errors import ConfigurationError, InvalidResponseError, ProviderError
from ai_dm.models import (
    CampaignStartRequest,
    CampaignStartResponse,
    CharacterState,
    GameState,
    TurnRequest,
    TurnResponse,
)
from engine.dice import Dice, SequenceSource
from engine.models import Character, GameState as EngineState, PlayerAction
from integration import DungeonMasterError, TurnOrchestrator
from rules_service import RulesService


TURN = {
    "narrative": "Вы замечаете дверь.",
    "state_updates": [],
    "new_location": None,
    "dm_thoughts": "За дверью скрывается страж.",
}
CAMPAIGN = {
    "campaign_title": "Крипта",
    "hidden_plot": "Страж охраняет ключ.",
    "starting_location": "crypt",
    "room_context": "Каменный зал.",
    "intro_narrative": "Вы входите в крипту.",
}


def completion(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def fake_client(*responses):
    client = Mock()
    client.chat.completions.create.side_effect = responses
    return client


def turn_request():
    return TurnRequest(
        rules_summary="Дверь можно осмотреть.",
        hidden_plot="Страж охраняет ключ.",
        game_state=GameState(
            location="crypt",
            room_context="Каменный зал.",
            active_characters=[CharacterState(name="Элиас", hp=12, max_hp=12, inventory=[])],
        ),
        player_action="Осматриваю дверь.",
    )


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.monster = patch.object(dm_engine.DnD5eClient, "get_monster", return_value={"name": "Skeleton"})
        self.monster.start()
        self.addCleanup(self.monster.stop)

    def invoke(self, client, kind):
        dm = AIDungeonMaster(client)
        if kind == "turn":
            return dm.process_turn(turn_request())
        return dm.generate_campaign_start(CampaignStartRequest(setting_theme="Крипта", characters=[]))

    def test_plain_and_fenced_json_for_both_operations(self):
        for kind, data, model in [
            ("turn", TURN, TurnResponse),
            ("campaign", CAMPAIGN, CampaignStartResponse),
        ]:
            for wrapper in ("{}", "```json\n{}\n```", "```\n{}\n```"):
                with self.subTest(kind=kind, wrapper=wrapper):
                    raw = wrapper.format(json.dumps(data, ensure_ascii=False))
                    client = fake_client(completion(raw))
                    result = self.invoke(client, kind)
                    self.assertEqual(result, model(**data))
                    self.assertEqual(client.chat.completions.create.call_count, 1)
                    prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
                    self.assertIn(json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2), prompt)

    def test_turn_prompt_preserves_all_existing_context(self):
        client = fake_client(completion(json.dumps(TURN)))
        self.invoke(client, "turn")
        prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        request = turn_request()
        for text in (request.rules_summary, request.hidden_plot, request.player_action,
                     request.game_state.location, request.game_state.room_context, "Элиас"):
            self.assertIn(text, prompt)

    def test_campaign_keeps_monster_data_and_fallback(self):
        for monster, expected in [({"name": "Skeleton"}, "Skeleton"), (None, "Базовый противник")]:
            with self.subTest(monster=monster):
                client = fake_client(completion(json.dumps(CAMPAIGN)))
                with patch.object(dm_engine.DnD5eClient, "get_monster", return_value=monster):
                    self.invoke(client, "campaign")
                prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
                self.assertIn(expected, prompt)

    def test_invalid_responses_are_repaired_once_for_both_operations(self):
        invalid = [
            SimpleNamespace(choices=[]),
            SimpleNamespace(choices=None),
            SimpleNamespace(choices=[SimpleNamespace(message=None)]),
            completion(None),
            completion(" \n"),
            completion("{broken"),
            completion("{}"),
            completion('{"narrative": 42}'),
        ]
        for kind, data in [("turn", TURN), ("campaign", CAMPAIGN)]:
            for bad in invalid:
                with self.subTest(kind=kind, response=bad):
                    client = fake_client(bad, completion(json.dumps(data)))
                    result = self.invoke(client, kind)
                    self.assertEqual(result.model_dump(), data)
                    calls = client.chat.completions.create.call_args_list
                    self.assertEqual(len(calls), 2)
                    initial, repair = (call.kwargs["messages"] for call in calls)
                    self.assertEqual(len(initial), 2)
                    self.assertEqual(repair[:2], initial)
                    self.assertIn("Ошибки проверки:", repair[-1]["content"])
                    self.assertTrue(all(message["content"].strip() for message in repair))

    def test_repair_includes_previous_output_and_validation_details(self):
        client = fake_client(completion("{}"), completion(json.dumps(TURN)))
        self.invoke(client, "turn")
        messages = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(messages[-2], {"role": "assistant", "content": "{}"})
        self.assertIn("narrative", messages[-1]["content"])
        self.assertIn("missing", messages[-1]["content"])

    def test_second_invalid_response_raises_with_cause(self):
        for kind in ("turn", "campaign"):
            with self.subTest(kind=kind):
                client = fake_client(completion("{}"), completion("{}"))
                with self.assertRaises(InvalidResponseError) as caught:
                    self.invoke(client, kind)
                self.assertIsInstance(caught.exception.__cause__, ValidationError)
                self.assertEqual(client.chat.completions.create.call_count, 2)
                self.assertNotIn("hidden_plot", str(caught.exception))

    def test_repeated_empty_output_raises_with_cause(self):
        client = fake_client(completion(None), SimpleNamespace(choices=[]))
        with self.assertRaises(InvalidResponseError) as caught:
            self.invoke(client, "turn")
        self.assertIsInstance(caught.exception.__cause__, ValueError)
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_transport_failure_is_not_repaired(self):
        for kind in ("turn", "campaign"):
            for prefix in ([], [completion("{}")]):
                with self.subTest(kind=kind, during_repair=bool(prefix)):
                    error = TimeoutError("private provider detail")
                    client = fake_client(*prefix, error)
                    with self.assertRaises(ProviderError) as caught:
                        self.invoke(client, kind)
                    self.assertIs(caught.exception.__cause__, error)
                    self.assertNotIn("private provider detail", str(caught.exception))
                    self.assertEqual(client.chat.completions.create.call_count, len(prefix) + 1)

    def test_programming_errors_are_not_reported_as_provider_failures(self):
        client = fake_client(TypeError("bad client implementation"))
        with self.assertRaises(TypeError):
            self.invoke(client, "turn")

    def test_existing_module_functions_still_work(self):
        client = fake_client(completion(json.dumps(TURN)), completion(json.dumps(CAMPAIGN)))
        with patch.object(dm_engine, "_default_dm", AIDungeonMaster(client)):
            self.assertEqual(dm_engine.process_turn(turn_request()), TurnResponse(**TURN))
            self.assertEqual(
                dm_engine.generate_campaign_start(CampaignStartRequest(setting_theme="Крипта", characters=[])),
                CampaignStartResponse(**CAMPAIGN),
            )


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        dotenv = patch.object(dm_engine, "load_dotenv")
        dotenv.start()
        self.addCleanup(dotenv.stop)
        environment = patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def test_import_without_key_does_not_read_dotenv_or_create_client(self):
        spec = importlib.util.spec_from_file_location("isolated_dm_engine", dm_engine.__file__)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {}, clear=True), patch("dotenv.load_dotenv") as dotenv, \
                patch("openai.OpenAI") as constructor:
            spec.loader.exec_module(module)
            dotenv.assert_not_called()
            constructor.assert_not_called()

    def test_default_client_is_lazy_and_reused(self):
        client = fake_client(completion(json.dumps(TURN)), completion(json.dumps(TURN)))
        with patch.object(dm_engine, "OpenAI", return_value=client) as constructor:
            dm = AIDungeonMaster()
            constructor.assert_not_called()
            dm.process_turn(turn_request())
            dm.process_turn(turn_request())
            constructor.assert_called_once()
            settings = constructor.call_args.kwargs
            self.assertEqual(settings["timeout"], 60)
            self.assertEqual(settings["max_retries"], 0)
            self.assertEqual(settings["base_url"], "https://router.huggingface.co/v1")

    def test_custom_configuration_is_used(self):
        client = fake_client(completion(json.dumps(TURN)))
        with patch.dict(os.environ, {
            "AI_DM_TIMEOUT_SECONDS": "12.5",
            "OPENAI_BASE_URL": "https://example.test/v1",
            "MODEL_NAME": "test-model",
        }), patch.object(dm_engine, "OpenAI", return_value=client) as constructor:
            AIDungeonMaster().process_turn(turn_request())
            self.assertEqual(constructor.call_args.kwargs["timeout"], 12.5)
            self.assertEqual(constructor.call_args.kwargs["base_url"], "https://example.test/v1")
            self.assertEqual(client.chat.completions.create.call_args.kwargs["model"], "test-model")

    def test_invalid_timeout_is_rejected_before_client_creation(self):
        for value in ("", "abc", "0", "-1", "nan", "inf", "-inf"):
            with self.subTest(value=value), patch.dict(os.environ, {"AI_DM_TIMEOUT_SECONDS": value}), \
                    patch.object(dm_engine, "OpenAI") as constructor:
                with self.assertRaises(ConfigurationError) as caught:
                    AIDungeonMaster().process_turn(turn_request())
                self.assertIsInstance(caught.exception.__cause__, ValueError)
                constructor.assert_not_called()

    def test_missing_key_fails_before_campaign_network_access(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "  "}), \
                patch.object(dm_engine.DnD5eClient, "get_monster") as monster, \
                patch.object(dm_engine, "OpenAI") as constructor:
            with self.assertRaises(ConfigurationError):
                AIDungeonMaster().generate_campaign_start(
                    CampaignStartRequest(setting_theme="Крипта", characters=[])
                )
            constructor.assert_not_called()
            monster.assert_not_called()

    def test_client_configuration_failure_preserves_cause(self):
        error = ValueError("invalid URL configuration")
        with patch.object(dm_engine, "OpenAI", side_effect=error):
            with self.assertRaises(ConfigurationError) as caught:
                AIDungeonMaster().process_turn(turn_request())
            self.assertIs(caught.exception.__cause__, error)

    def test_injected_client_needs_no_credentials(self):
        client = fake_client(completion(json.dumps(TURN)))
        with patch.dict(os.environ, {}, clear=True), patch.object(dm_engine, "OpenAI") as constructor:
            self.assertEqual(AIDungeonMaster(client).process_turn(turn_request()), TurnResponse(**TURN))
            constructor.assert_not_called()

    def test_real_sdk_transport_failures_have_no_hidden_retries(self):
        for failure in (401, 429, 500, "timeout", "connection"):
            with self.subTest(failure=failure):
                requests = []

                def handle(request):
                    requests.append(request)
                    if failure == "timeout":
                        raise httpx.ReadTimeout("timed out", request=request)
                    if failure == "connection":
                        raise httpx.ConnectError("connection failed", request=request)
                    return httpx.Response(failure, json={"error": {"message": "unavailable"}})

                with httpx.Client(transport=httpx.MockTransport(handle)) as http_client:
                    def create_client(**kwargs):
                        return OpenAI(http_client=http_client, **kwargs)

                    with patch.object(dm_engine, "OpenAI", side_effect=create_client):
                        with self.assertRaises(ProviderError) as caught:
                            AIDungeonMaster().process_turn(turn_request())
                    self.assertIsNotNone(caught.exception.__cause__)
                self.assertEqual(len(requests), 1)


class IntegrationCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.state = EngineState(
            "session", 0, 0, "crypt",
            (Character("hero", "Элиас", "Плут", 12, 12, {}, ()),), {},
        )
        self.action = PlayerAction("action", "session", "hero", "Осматриваю дверь.", 0)
        self.rules = RulesService.from_text("# Дверь\nДверь можно осмотреть.")
        self.source = SequenceSource([])
        self.dice = Dice(self.source)

    def play(self, client):
        return TurnOrchestrator(AIDungeonMaster(client), self.rules).play_turn(
            self.state, self.action, "secret", self.dice
        )

    def test_valid_response_is_applied_by_real_engine(self):
        data = {**TURN, "state_updates": [{"character_name": "Элиас", "hp_change": -2}]}
        client = fake_client(completion(json.dumps(data)))
        outcome = self.play(client)
        self.assertTrue(outcome.accepted)
        self.assertEqual(outcome.state.characters[0].hp, 10)
        self.assertEqual(outcome.state.revision, 1)
        self.assertEqual(self.state.characters[0].hp, 12)
        self.assertEqual(outcome.narration, TURN["narrative"])
        self.assertEqual(outcome.dm_notes, TURN["dm_thoughts"])

    def test_repaired_response_advances_engine_only_once(self):
        client = fake_client(completion("{}"), completion(json.dumps(TURN)))
        outcome = self.play(client)
        self.assertTrue(outcome.accepted)
        self.assertEqual((outcome.state.revision, outcome.state.turn_number), (1, 1))
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_failures_are_wrapped_without_changing_state(self):
        for responses, error_type in [
            ([TimeoutError("offline")], ProviderError),
            ([completion("{}"), completion("{}")], InvalidResponseError),
        ]:
            with self.subTest(error_type=error_type):
                before = copy.deepcopy(self.state)
                with self.assertRaises(DungeonMasterError) as caught:
                    self.play(fake_client(*responses))
                self.assertIsInstance(caught.exception.__cause__, error_type)
                self.assertEqual(self.state, before)
                self.assertEqual(self.source.consumed, 0)

    def test_engine_rejection_still_discards_narration(self):
        data = {**TURN, "state_updates": [{"character_name": "Элиас", "items_removed": ["Меч"]}]}
        outcome = self.play(fake_client(completion(json.dumps(data))))
        self.assertFalse(outcome.accepted)
        self.assertIs(outcome.state, self.state)
        self.assertIsNone(outcome.narration)
        self.assertIsNone(outcome.dm_notes)


if __name__ == "__main__":
    unittest.main()
