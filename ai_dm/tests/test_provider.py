import copy
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import httpx
from openai import APIStatusError, OpenAI

from ai_dm import dm_engine, main
from ai_dm.context import message_chars
from ai_dm.dm_engine import AIDungeonMaster
from ai_dm.errors import ConfigurationError, ContextBudgetError, InvalidResponseError, ProviderError
from ai_dm.models import CampaignStartRequest
from ai_dm.provider import provider_error
from ai_dm.tests.test_dm_engine import CAMPAIGN, TURN, completion, fake_client, turn_request
from ai_dm.tests.test_history import action, entry, session
from engine.dice import Dice, SequenceSource
from integration import DungeonMasterError
from rules_service import RulesService


PRIVATE = "private-key-and-hidden-plot"


def failure(status, *, headers=None, body=None):
    body = body if body is not None else {"message": PRIVATE}
    response = httpx.Response(
        status, headers=headers, json=body, request=httpx.Request("POST", "https://example.test/private"),
    )
    return APIStatusError(PRIVATE, response=response, body=body)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {
            "AI_DM_MAX_RETRY_WAIT_SECONDS": "10",
            "AI_DM_MAX_CONTEXT_CHARS": "100000",
            "AI_DM_MAX_OUTPUT_TOKENS": "1024",
        })
        env.start()
        self.addCleanup(env.stop)
        sleeper = patch.object(dm_engine.time, "sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)
        monster = patch.object(dm_engine.DnD5eClient, "get_monster", return_value=None)
        monster.start()
        self.addCleanup(monster.stop)

    def invoke(self, client, *, campaign=False):
        dm = AIDungeonMaster(client)
        if campaign:
            return dm.generate_campaign_start(CampaignStartRequest(setting_theme="Крипта", characters=[]))
        return dm.process_turn(turn_request())

    def test_temporary_statuses_retry_once_for_both_operations(self):
        for campaign in (False, True):
            for status in (429, 500, 502, 503, 504):
                with self.subTest(campaign=campaign, status=status):
                    self.sleep.reset_mock()
                    data = CAMPAIGN if campaign else TURN
                    client = fake_client(failure(status), completion(json.dumps(data)))
                    self.assertEqual(self.invoke(client, campaign=campaign).model_dump(), data)
                    calls = client.chat.completions.create.call_args_list
                    self.assertEqual(len(calls), 2)
                    self.assertEqual(calls[0].kwargs, calls[1].kwargs)
                    self.sleep.assert_called_once_with(1.0)

    def test_permanent_errors_are_classified_without_retry_or_private_text(self):
        for status, category in ((400, "invalid_request"), (401, "authentication"),
                                 (402, "quota"), (403, "permission"), (404, "invalid_request"),
                                 (413, "invalid_request"), (422, "invalid_request"), (501, "unknown")):
            with self.subTest(status=status):
                self.sleep.reset_mock()
                original = failure(status)
                client = fake_client(original)
                with self.assertRaises(ProviderError) as caught:
                    self.invoke(client)
                error = caught.exception
                self.assertEqual(error.category, category)
                self.assertEqual(error.status_code, status)
                self.assertFalse(error.retryable)
                self.assertIs(error.__cause__, original)
                self.assertNotIn(PRIVATE, str(error))
                self.assertNotIn("example.test", str(error))
                self.assertEqual(client.chat.completions.create.call_count, 1)
                self.sleep.assert_not_called()

    def test_quota_codes_in_429_are_not_treated_as_temporary_rate_limits(self):
        for code in ("insufficient_quota", "billing_hard_limit_reached", "insufficient_credits"):
            for body in ({"code": code}, {"type": code}, {"error": {"code": code}}):
                with self.subTest(body=body):
                    original = failure(429, body=body, headers={"Retry-After": "2"})
                    client = fake_client(original)
                    with self.assertRaises(ProviderError) as caught:
                        self.invoke(client)
                    self.assertEqual(caught.exception.category, "quota")
                    self.assertEqual(caught.exception.retry_after, 2)
                    self.assertEqual(client.chat.completions.create.call_count, 1)
                    self.sleep.assert_not_called()

    def test_unknown_body_shapes_are_safe_and_not_used_as_instructions(self):
        for body in (None, [], "retry forever " + PRIVATE, {"error": []},
                     {"code": ["insufficient_quota"]}, {"error": {"code": {"nested": PRIVATE}}}):
            with self.subTest(body=body):
                error = provider_error(failure(429, body=body))
                self.assertEqual(error.category, "rate_limit")
                self.assertNotIn(PRIVATE, str(error))

    def test_timeout_and_connection_failures_are_never_retried(self):
        for original, category in ((TimeoutError(PRIVATE), "timeout"), (ConnectionError(PRIVATE), "connection"),
                                   (httpx.ReadTimeout(PRIVATE), "timeout"), (httpx.ConnectError(PRIVATE), "connection")):
            with self.subTest(category=category, type=type(original)):
                client = fake_client(original)
                with self.assertRaises(ProviderError) as caught:
                    self.invoke(client)
                self.assertEqual(caught.exception.category, category)
                self.assertIsNone(caught.exception.status_code)
                self.assertIs(caught.exception.__cause__, original)
                self.assertNotIn(PRIVATE, str(caught.exception))
                self.assertEqual(client.chat.completions.create.call_count, 1)
        self.sleep.assert_not_called()

    def test_retry_header_numeric_milliseconds_and_http_date(self):
        for headers, expected in (
            ({"Retry-After": "2.5"}, 2.5),
            ({"Retry-After": "0"}, 0.0),
            ({"Retry-After": "10"}, 10.0),
            ({"retry-after-ms": "250"}, 0.25),
            ({"retry-after-ms": "bad", "Retry-After": "3"}, 3.0),
            ({"Retry-After": "Thu, 01 Jan 1970 00:16:45 GMT"}, 5.0),
            ({"Retry-After": "Thu, 01 Jan 1970 00:00:00 GMT"}, 0.0),
        ):
            with self.subTest(headers=headers), patch("ai_dm.provider.time.time", return_value=1000):
                self.sleep.reset_mock()
                client = fake_client(failure(429, headers=headers), completion(json.dumps(TURN)))
                self.invoke(client)
                self.sleep.assert_called_once_with(expected)

    def test_invalid_retry_header_uses_bounded_fallback(self):
        for value in ("", "-1", "nan", "inf", "1e999", "nonsense"):
            with self.subTest(value=value):
                self.sleep.reset_mock()
                client = fake_client(failure(429, headers={"Retry-After": value}), completion(json.dumps(TURN)))
                self.invoke(client)
                self.sleep.assert_called_once_with(1.0)

    def test_wait_longer_than_cap_is_reported_without_early_retry(self):
        original = failure(429, headers={"Retry-After": "120"})
        client = fake_client(original)
        with self.assertRaises(ProviderError) as caught:
            self.invoke(client)
        self.assertEqual(caught.exception.retry_after, 120)
        self.assertIn("120", str(caught.exception))
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.sleep.assert_not_called()

    def test_repeated_failure_stops_after_two_calls_and_reports_latest_error(self):
        first = failure(429, headers={"Retry-After": "2"})
        second = failure(503, headers={"Retry-After": "8"})
        client = fake_client(first, second)
        with self.assertRaises(ProviderError) as caught:
            self.invoke(client)
        self.assertEqual(caught.exception.category, "unavailable")
        self.assertEqual(caught.exception.retry_after, 8)
        self.assertIs(caught.exception.__cause__, second)
        self.assertEqual(client.chat.completions.create.call_count, 2)
        self.sleep.assert_called_once_with(2)

    def test_retry_and_repair_share_three_call_budget_in_both_orders(self):
        for campaign in (False, True):
            for retry_first in (False, True):
                for final_failure in (False, True):
                    with self.subTest(campaign=campaign, retry_first=retry_first, final_failure=final_failure):
                        self.sleep.reset_mock()
                        prefix = [failure(429), completion("{}")]
                        if not retry_first:
                            prefix.reverse()
                        data = CAMPAIGN if campaign else TURN
                        last = failure(503) if final_failure else completion(json.dumps(data))
                        client = fake_client(*prefix, last)
                        if final_failure:
                            with self.assertRaises(ProviderError):
                                self.invoke(client, campaign=campaign)
                        else:
                            self.assertEqual(self.invoke(client, campaign=campaign).model_dump(), data)
                        calls = client.chat.completions.create.call_args_list
                        self.assertEqual(len(calls), 3)
                        self.sleep.assert_called_once_with(1.0)
                        self.assertIn("Ошибки проверки:", calls[-1].kwargs["messages"][-1]["content"])

    def test_retry_does_not_grant_a_second_response_repair(self):
        for sequence in (
            [failure(429), completion("{}"), completion("{}")],
            [completion("{}"), failure(503), completion("{}")],
        ):
            self.sleep.reset_mock()
            client = fake_client(*sequence)
            with self.assertRaises(InvalidResponseError):
                self.invoke(client)
            self.assertEqual(client.chat.completions.create.call_count, 3)
            self.sleep.assert_called_once_with(1.0)

    def test_retry_budget_resets_on_each_new_operation(self):
        client = fake_client(
            failure(503), completion(json.dumps(TURN)), failure(503), completion(json.dumps(TURN)),
        )
        dm = AIDungeonMaster(client)
        dm.process_turn(turn_request())
        dm.process_turn(turn_request())
        self.assertEqual(client.chat.completions.create.call_count, 4)
        self.assertEqual(self.sleep.call_count, 2)

    def test_wait_setting_is_validated_and_can_disable_retries(self):
        for value in ("", "-1", "nan", "inf", "61", "abc"):
            with self.subTest(value=value), patch.dict(os.environ, {"AI_DM_MAX_RETRY_WAIT_SECONDS": value}):
                client = fake_client()
                with self.assertRaises(ConfigurationError):
                    self.invoke(client)
                client.chat.completions.create.assert_not_called()
        for value in ("0", "0.5"):
            with self.subTest(value=value), patch.dict(os.environ, {"AI_DM_MAX_RETRY_WAIT_SECONDS": value}):
                client = fake_client(failure(429))
                with self.assertRaises(ProviderError):
                    self.invoke(client)
                self.assertEqual(client.chat.completions.create.call_count, 1)
        self.sleep.assert_not_called()

    def test_context_budget_is_still_checked_before_repair_after_retry(self):
        baseline = fake_client(completion(json.dumps(TURN)))
        self.invoke(baseline)
        limit = message_chars(baseline.chat.completions.create.call_args.kwargs["messages"])
        client = fake_client(failure(503), completion("{}"))
        with patch.dict(os.environ, {"AI_DM_MAX_CONTEXT_CHARS": str(limit)}):
            with self.assertRaises(ContextBudgetError) as caught:
                self.invoke(client)
        self.assertTrue(caught.exception.repair)
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_sdk_retries_are_disabled_even_for_supplied_client_without_mutating_it(self):
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(503, json={"error": {"message": PRIVATE}})

        with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
            client = OpenAI(api_key="test", base_url="https://example.test/v1", http_client=transport, max_retries=5)
            with self.assertRaises(ProviderError):
                self.invoke(client)
            self.assertEqual(client.max_retries, 5)
            self.assertFalse(transport.is_closed)
        self.assertEqual(len(requests), 2)
        self.sleep.assert_called_once_with(1.0)

    def test_real_sdk_retries_then_succeeds_with_same_request(self):
        requests = []

        def handle(request):
            requests.append(json.loads(request.content))
            if len(requests) == 1:
                return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": {"code": "rate_limit_exceeded"}})
            return httpx.Response(200, json={
                "id": "test", "object": "chat.completion", "created": 0, "model": "test",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": json.dumps(TURN)}}],
            })

        with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
            client = OpenAI(api_key="test", base_url="https://example.test/v1", http_client=transport)
            self.assertEqual(self.invoke(client).model_dump(), TURN)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0], requests[1])
        self.sleep.assert_called_once_with(2)

    def test_httpx_status_error_is_classified_without_leaking_body(self):
        response = httpx.Response(
            429, json={"error": {"code": "insufficient_quota", "message": PRIVATE}},
            request=httpx.Request("POST", "https://example.test"),
        )
        original = httpx.HTTPStatusError(PRIVATE, request=response.request, response=response)
        client = fake_client(original)
        with self.assertRaises(ProviderError) as caught:
            self.invoke(client)
        self.assertEqual(caught.exception.category, "quota")
        self.assertNotIn(PRIVATE, str(caught.exception))
        self.sleep.assert_not_called()

    def test_successful_retry_advances_engine_and_history_only_once(self):
        game = session()
        before = copy.deepcopy(game.state)
        data = {**TURN, "state_updates": [{"character_name": "Элиас", "hp_change": -3}]}
        client = fake_client(failure(429), completion("{}"), completion(json.dumps(data)))
        outcome = game.play_turn(
            action(game, 1), AIDungeonMaster(client), RulesService.from_text(main.DEMO_RULES), Dice(SequenceSource([])),
        )
        self.assertTrue(outcome.accepted)
        self.assertEqual(game.state.revision, before.revision + 1)
        self.assertEqual(game.state.characters[0].hp, before.characters[0].hp - 3)
        self.assertEqual(len(game.history), 1)

    def test_exhausted_retry_leaves_state_and_history_unchanged(self):
        game = session()
        game.history = [entry(1)]
        before = copy.deepcopy(game)
        original = failure(429)
        client = fake_client(original, original)
        with self.assertRaises(DungeonMasterError) as caught:
            game.play_turn(
                action(game, 2), AIDungeonMaster(client), RulesService.from_text(main.DEMO_RULES),
                Dice(SequenceSource([])),
            )
        self.assertEqual(game, before)
        self.assertIsInstance(caught.exception.__cause__, ProviderError)
        self.assertIs(caught.exception.__cause__.__cause__, original)
        self.assertNotIn(PRIVATE, str(caught.exception))

    def test_demo_prints_safe_russian_errors_for_campaign_and_turn(self):
        for campaign in (False, True):
            with self.subTest(campaign=campaign):
                responses = [failure(401)] if campaign else [
                    completion(json.dumps(CAMPAIGN)), failure(402), completion(json.dumps(TURN)),
                ]
                client = fake_client(*responses)
                output = io.StringIO()
                with patch.object(main, "AIDungeonMaster", return_value=AIDungeonMaster(client)), redirect_stdout(output):
                    main.run()
                text = output.getvalue()
                self.assertIn("ключ доступа" if campaign else "Исчерпана квота", text)
                self.assertNotIn(PRIVATE, text)
                self.assertNotIn("Dungeon Master failed", text)
                if not campaign:
                    self.assertIn("Ход 2.", text)

    def test_legacy_provider_error_constructor_remains_compatible(self):
        error = ProviderError("offline")
        self.assertEqual(str(error), "offline")
        self.assertEqual(error.category, "unknown")
        self.assertIsNone(error.status_code)
        self.assertIsNone(error.retry_after)
        self.assertFalse(error.retryable)


if __name__ == "__main__":
    unittest.main()
