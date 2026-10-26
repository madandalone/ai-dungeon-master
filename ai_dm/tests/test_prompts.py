import json
import os
import unittest
from contextlib import chdir
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ai_dm import prompts
from ai_dm.dm_engine import AIDungeonMaster
from ai_dm.errors import ConfigurationError, ContextBudgetError
from ai_dm.models import CampaignStartRequest, TurnHistoryEntry
from ai_dm.prompts import PromptTemplates
from ai_dm.tests.test_dm_engine import CAMPAIGN, TURN, completion, fake_client, turn_request


class PromptTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {
            "AI_DM_MAX_CONTEXT_CHARS": "100000",
            "AI_DM_MAX_OUTPUT_TOKENS": "1024",
            "AI_DM_MAX_RETRY_WAIT_SECONDS": "0",
        })
        env.start()
        self.addCleanup(env.stop)
        monster = patch("ai_dm.dm_engine.DnD5eClient.get_monster", return_value={"name": "Skeleton"})
        monster.start()
        self.addCleanup(monster.stop)

    def invoke(self, client, *, campaign=False):
        dm = AIDungeonMaster(client)
        if campaign:
            return dm.generate_campaign_start(CampaignStartRequest(setting_theme="Крипта", characters=[]))
        return dm.process_turn(turn_request())

    def test_generation_and_repair_load_templates_outside_project_directory(self):
        outside = Path(__file__).resolve().parents[3]
        for campaign in (False, True):
            with self.subTest(campaign=campaign):
                data = CAMPAIGN if campaign else TURN
                baseline = fake_client(completion("{}"), completion(json.dumps(data)))
                self.invoke(baseline, campaign=campaign)
                client = fake_client(completion("{}"), completion(json.dumps(data)))
                with chdir(outside):
                    self.invoke(client, campaign=campaign)
                self.assertEqual(client.chat.completions.create.call_args_list,
                                 baseline.chat.completions.create.call_args_list)

    def test_turn_data_is_substituted_once_and_preserved_literally(self):
        request = turn_request()
        marker = 'данные {schema} ${hidden_plot} $5 "руны"\n\\тайник 🗝'
        request.rules_summary = "Правила " + marker
        request.hidden_plot = "Тайна " + marker
        request.player_action = "Действие " + marker
        request.game_state.location = "Локация " + marker
        request.game_state.room_context = "Комната " + marker
        request.game_state.active_characters[0].name = "Герой " + marker
        request.game_state.active_characters[0].inventory = ["Предмет " + marker]
        request.history = [
            TurnHistoryEntry(player_action=marker, narrative=marker, dm_notes=marker),
        ]
        before = request.model_copy(deep=True)
        client = fake_client(completion(json.dumps(TURN)))
        AIDungeonMaster(client).process_turn(request)
        text = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        for value in (request.rules_summary, request.hidden_plot, request.player_action,
                      request.game_state.location, request.game_state.room_context):
            self.assertIn(value, text)
        self.assertIn(json.dumps([c.model_dump() for c in request.game_state.active_characters],
                                 ensure_ascii=False), text)
        self.assertIn(json.dumps([request.game_state.active_characters[0].name], ensure_ascii=False), text)
        self.assertIn(json.dumps([item.model_dump() for item in request.history], ensure_ascii=False), text)
        self.assertEqual(request, before)

    def test_campaign_data_and_repair_details_are_not_interpreted_as_templates(self):
        templates = PromptTemplates.load()
        marker = 'крипта {characters} ${monster_stats} $5\n"руны"'
        request = CampaignStartRequest(
            setting_theme=marker, characters=turn_request().game_state.active_characters,
        )
        monster = {"name": marker, "hit_points": 13, "armor_class": 12, "actions": [marker]}
        text = templates.campaign_prompt(request, monster)
        self.assertIn(marker, text)
        self.assertIn(json.dumps(monster, ensure_ascii=False), text)
        self.assertIn(json.dumps([c.model_dump() for c in request.characters], ensure_ascii=False), text)
        self.assertIn(marker, templates.repair_prompt(marker))
        self.assertIn("Базовый противник", templates.campaign_prompt(request, None))

    def test_each_loaded_template_is_included_in_the_context_budget(self):
        original_read = prompts._read_template
        for name in ("system.txt", "campaign.txt", "turn.txt", "request.txt", "repair.txt"):
            with self.subTest(name=name):
                def read(filename):
                    value = original_read(filename)
                    return "Дополнительная инструкция. " * 1000 + value if filename == name else value

                client = fake_client(completion("{}")) if name == "repair.txt" else fake_client()
                with patch.object(prompts, "_read_template", side_effect=read), \
                        patch.dict(os.environ, {"AI_DM_MAX_CONTEXT_CHARS": "10000"}):
                    with self.assertRaises(ContextBudgetError) as caught:
                        self.invoke(client, campaign=name == "campaign.txt")
                self.assertEqual(caught.exception.repair, name == "repair.txt")
                self.assertEqual(client.chat.completions.create.call_count, int(name == "repair.txt"))

    def test_templates_are_stable_during_repair_and_reloaded_for_next_operation(self):
        original_read = prompts._read_template
        changed = False

        def read(name):
            text = original_read(name)
            return text + "\nНовая инструкция." if changed and name == "system.txt" else text

        def respond(**kwargs):
            nonlocal changed
            if not changed:
                changed = True
                return completion("{}")
            return completion(json.dumps(TURN))

        client = fake_client()
        client.chat.completions.create.side_effect = respond
        dm = AIDungeonMaster(client)
        with patch.object(prompts, "_read_template", side_effect=read):
            dm.process_turn(turn_request())
            dm.process_turn(turn_request())
        first, repair, next_turn = [
            call.kwargs["messages"] for call in client.chat.completions.create.call_args_list
        ]
        self.assertEqual(first[:2], repair[:2])
        self.assertNotIn("Новая инструкция.", repair[0]["content"])
        self.assertIn("Новая инструкция.", next_turn[0]["content"])

    def test_unreadable_templates_fail_without_llm_request(self):
        for failure in (FileNotFoundError("missing"), PermissionError("denied"),
                        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")):
            for campaign in (False, True):
                with self.subTest(error=type(failure), campaign=campaign):
                    client = fake_client()
                    with patch.object(Path, "read_text", side_effect=failure):
                        with self.assertRaises(ConfigurationError) as caught:
                            self.invoke(client, campaign=campaign)
                    self.assertIs(caught.exception.__cause__, failure)
                    self.assertIn("system.txt", str(caught.exception))
                    client.chat.completions.create.assert_not_called()

    def test_bad_placeholder_fails_without_sending_private_data(self):
        templates = PromptTemplates.load()
        client = fake_client()
        for text in ("${unknown}", "${unfinished"):
            with self.subTest(text=text):
                with patch.object(PromptTemplates, "load", return_value=replace(templates, turn=text)):
                    with self.assertRaises(ConfigurationError) as caught:
                        self.invoke(client)
                self.assertIn("turn.txt", str(caught.exception))
                self.assertNotIn(turn_request().hidden_plot, str(caught.exception))
                client.chat.completions.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
