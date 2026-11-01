"""Offline product-provider and budget checks; no network calls are made."""

from datetime import date, datetime, timedelta, timezone
import json
import ssl
from importlib.metadata import PackageNotFoundError
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from marketlens_bot.provider import (ClockGuard, MAX_OUTPUT_TOKENS, MAX_RESERVED_INPUT_TOKENS,
                                     OpenAITransport, Pricing, ProviderError, ResponsesProvider,
                                     _openai_https_handler, build_runtime_provider)
from marketlens_bot.state import State
from marketlens_bot.trusted_clock import ClockSourceError


SINGAPORE = timezone(timedelta(hours=8))


class FakeClock:
    def __init__(self, local=None):
        self.wall = (local or datetime(2026, 10, 20, 12, tzinfo=SINGAPORE)).timestamp()
        self.mono = 100.0
        self.trusted = True

    def __call__(self):
        return self.wall, self.mono, self.trusted

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds


class FakeTransport:
    def __init__(self, answers=(), fail_on=None, usage=None):
        self.answers = list(answers)
        self.calls = []
        self.fail_on = fail_on
        self.usage = usage if usage is not None else {"input_tokens": 100, "output_tokens": 20}
        self.service_tier = "default"

    def post(self, path, payload, ticket, guard):
        guard.check(ticket)
        self.calls.append((path, payload))
        if path == self.fail_on:
            raise OSError("uncertain fake transmission")
        if path != "/responses":
            raise AssertionError(f"Unexpected provider endpoint: {path}")
        answer = self.answers.pop(0) if self.answers else {"status": "abstained", "reason": "No evidence.", "claims": []}
        response = {"status": "completed", "usage": self.usage, "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(answer)}]}]}
        if self.service_tier is not None:
            response["service_tier"] = self.service_tier
        return response


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(Path(self.tmp.name) / "state")
        self.clock = FakeClock()
        self.transport = FakeTransport()
        self.pricing = Pricing(date(2026, 10, 1))
        self.request = {"instructions": "instructions", "input": "example evidence", "text": {}}

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def enable(self):
        self.state.record_notice("privacy", 10)
        self.assertTrue(self.state.confirm_notice("privacy", 10))
        self.state.record_notice("ai", 20)
        self.assertTrue(self.state.confirm_notice("ai", 20))

    def provider(self):
        return ResponsesProvider(self.state, self.transport, ClockGuard(self.clock), self.pricing).for_answer()

    def test_runtime_provider_factory_stays_disconnected_when_transport_locked(self):
        class ForbiddenEnvironment:
            def get(self, *_args):
                raise AssertionError("locked runtime read the OpenAI credential")

        with patch("marketlens_bot.provider.LIVE_TRANSPORT_ENABLED", False), \
                patch("marketlens_bot.provider.ChronyNTSClock") as clock_factory, \
                patch("marketlens_bot.provider.OpenAITransport") as transport_factory:
            provider = build_runtime_provider(self.state, environment=ForbiddenEnvironment())

        self.assertIsNone(provider)
        clock_factory.assert_not_called()
        transport_factory.assert_not_called()

    def test_runtime_provider_factory_wires_trusted_clock_and_dated_pricing(self):
        clock = FakeClock()
        with patch("marketlens_bot.provider.LIVE_TRANSPORT_ENABLED", True), \
                patch("marketlens_bot.provider.ChronyNTSClock", return_value=clock) as clock_factory, \
                patch("marketlens_bot.provider.OpenAITransport") as transport_factory:
            provider = build_runtime_provider(
                self.state, environment={"OPENAI_API_KEY": "offline-test-credential"}
            )

        clock_factory.assert_called_once_with()
        transport_factory.assert_called_once_with("offline-test-credential")
        self.assertIs(provider.clock_guard.clock, clock)
        self.assertEqual(provider.pricing.checked_on, date(2026, 11, 2))
        self.assertIs(provider.transport, transport_factory.return_value)

    def test_runtime_provider_factory_fails_closed_without_key_or_clock(self):
        with patch("marketlens_bot.provider.LIVE_TRANSPORT_ENABLED", True), \
                patch("marketlens_bot.provider.ChronyNTSClock") as clock_factory, \
                patch("marketlens_bot.provider.OpenAITransport") as transport_factory:
            self.assertIsNone(build_runtime_provider(self.state, environment={}))
            clock_factory.assert_not_called()
            transport_factory.assert_not_called()

        with patch("marketlens_bot.provider.LIVE_TRANSPORT_ENABLED", True), \
                patch("marketlens_bot.provider.ChronyNTSClock", side_effect=ClockSourceError("unavailable")), \
                patch("marketlens_bot.provider.OpenAITransport") as transport_factory:
            self.assertIsNone(build_runtime_provider(
                self.state, environment={"OPENAI_API_KEY": "offline-test-credential"}
            ))
            transport_factory.assert_not_called()

    def test_runtime_provider_factory_leaves_local_mode_when_tls_setup_fails(self):
        with patch("marketlens_bot.provider.LIVE_TRANSPORT_ENABLED", True), \
                patch("marketlens_bot.provider.ChronyNTSClock"), \
                patch("marketlens_bot.provider.OpenAITransport", side_effect=ProviderError("TLS unavailable")):
            self.assertIsNone(build_runtime_provider(
                self.state, environment={"OPENAI_API_KEY": "offline-test-credential"}
            ))

    def test_consent_required_before_provider_dispatch(self):
        with self.assertRaises(ProviderError):
            self.provider().generate(self.request)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.state.budget("2026-10")["reserved_nanodollars"], 0)

    def test_generation_reserves_full_bound_and_settles_actual_usage(self):
        self.enable()
        result = self.provider().generate(self.request)
        self.assertEqual(result["answer"]["status"], "abstained")
        self.assertEqual([path for path, _ in self.transport.calls], ["/responses"])
        generation = self.transport.calls[0][1]
        self.assertEqual(generation["model"], "gpt-6-luna")
        self.assertEqual(generation["service_tier"], "default")
        self.assertIs(generation["store"], False)
        self.assertIs(generation["background"], False)
        self.assertNotIn("tools", generation)
        self.assertEqual(self.state.budget("2026-10")["settled_nanodollars"], 45000)
        self.assertEqual(self.state.budget("2026-10")["reserved_nanodollars"], 0)
        rows = self.state.db.execute("SELECT answer_id, state FROM attempts").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(len({row[0] for row in rows}), 1)
        self.assertTrue(all(row[1] == "settled" for row in rows))
        ledger = str(self.state.db.execute("SELECT * FROM attempts").fetchall())
        self.assertNotIn("example evidence", ledger)
        self.assertNotIn("No evidence.", ledger)

    def test_unknown_generation_outcome_keeps_reservation_and_halts(self):
        self.enable()
        self.transport.fail_on = "/responses"
        provider = self.provider()
        with self.assertRaises(ProviderError):
            provider.generate(self.request)
        self.assertEqual(self.state.budget("2026-10")["reserved_nanodollars"], 26_500_000)
        self.assertTrue(provider.halted)
        with self.assertRaises(ProviderError):
            provider.generate(self.request)
        self.assertEqual(len(self.transport.calls), 1)

    def test_unexpected_service_tier_keeps_reservation(self):
        self.enable()
        self.transport.service_tier = "priority"
        with self.assertRaises(ProviderError):
            self.provider().generate(self.request)
        self.assertEqual(self.state.budget("2026-10")["reserved_nanodollars"], 26_500_000)
        self.assertEqual(self.state.budget("2026-11")["reserved_nanodollars"], 26_500_000)

    def test_missing_service_tier_keeps_reservation(self):
        self.enable()
        self.transport.service_tier = None
        with self.assertRaises(ProviderError):
            self.provider().generate(self.request)
        self.assertEqual(self.state.budget("2026-10")["reserved_nanodollars"], 26_500_000)

    def test_slow_response_inside_rollover_guard_settles_in_same_month(self):
        self.enable()
        self.clock = FakeClock(datetime(2026, 10, 31, 23, 54, tzinfo=SINGAPORE))
        original_post = self.transport.post
        def delayed_post(*args):
            result = original_post(*args)
            self.clock.advance(120)
            return result
        self.transport.post = delayed_post
        self.provider().generate(self.request)
        self.assertEqual(self.state.budget("2026-10")["settled_nanodollars"], 45_000)
        self.assertEqual(self.state.budget("2026-10")["reserved_nanodollars"], 0)

    def test_cross_month_completion_keeps_reservation_after_restart(self):
        self.enable()
        self.clock = FakeClock(datetime(2026, 10, 31, 23, 54, tzinfo=SINGAPORE))
        original_post = self.transport.post
        def delayed_post(*args):
            result = original_post(*args)
            self.clock.advance(720)
            return result
        self.transport.post = delayed_post
        with self.assertRaises(ProviderError):
            self.provider().generate(self.request)
        self.state.close()
        self.state = State(Path(self.tmp.name) / "state")
        self.assertEqual(self.state.budget("2026-10")["reserved_nanodollars"], 26_500_000)
        self.assertEqual(self.state.budget("2026-11")["reserved_nanodollars"], 26_500_000)

    def test_clock_jump_after_transport_keeps_reservation(self):
        self.enable()
        original_post = self.transport.post
        def jumped_post(*args):
            result = original_post(*args)
            self.clock.wall += 3600
            return result
        self.transport.post = jumped_post
        with self.assertRaises(ProviderError):
            self.provider().generate(self.request)
        self.assertEqual(self.state.budget("2026-10")["reserved_nanodollars"], 26_500_000)

    def test_untrusted_clock_after_transport_keeps_reservation(self):
        self.enable()
        original_post = self.transport.post
        def untrusted_post(*args):
            result = original_post(*args)
            self.clock.trusted = False
            return result
        self.transport.post = untrusted_post
        with self.assertRaises(ProviderError):
            self.provider().generate(self.request)
        self.assertEqual(self.state.budget("2026-10")["reserved_nanodollars"], 26_500_000)

    def test_invalid_usage_keeps_generation_reservation(self):
        self.enable()
        self.transport.usage = {"input_tokens": MAX_RESERVED_INPUT_TOKENS + 1, "output_tokens": 20}
        with self.assertRaises(ProviderError):
            self.provider().generate(self.request)
        self.assertEqual(self.state.budget("2026-10")["reserved_nanodollars"], 26_500_000)

    def test_zero_usage_with_an_answer_keeps_generation_reservation(self):
        self.enable()
        self.transport.usage = {"input_tokens": 0, "output_tokens": 0}
        with self.assertRaises(ProviderError):
            self.provider().generate(self.request)
        self.assertEqual(self.state.budget("2026-10")["reserved_nanodollars"], 26_500_000)

    def test_stale_pricing_and_untrusted_clock_block_transmission(self):
        self.enable()
        self.pricing = Pricing(date(2026, 1, 1))
        with self.assertRaises(ProviderError):
            self.provider().generate(self.request)
        self.assertEqual(self.state.budget("2026-10")["reserved_nanodollars"], 0)
        self.pricing = Pricing(date(2026, 10, 1))
        self.clock.trusted = False
        with self.assertRaises(ProviderError):
            self.provider().generate(self.request)
        self.assertEqual(self.transport.calls, [])

    def test_rate_below_conservative_floor_blocks_transmission(self):
        self.enable()
        self.pricing = Pricing(date(2026, 10, 1), input_bound_per_token=349)
        with self.assertRaises(ProviderError):
            self.provider().generate(self.request)
        self.assertEqual(self.transport.calls, [])

    def test_higher_configured_rates_raise_the_accounted_cost(self):
        self.enable()
        self.pricing = Pricing(date(2026, 10, 1),
                               input_bound_per_token=400, output_bound_per_token=550)
        self.provider().generate(self.request)
        self.assertEqual(self.state.budget("2026-10")["settled_nanodollars"], 51_000)

    def test_reservation_above_answer_cap_blocks_transmission(self):
        self.enable()
        self.pricing = Pricing(date(2026, 10, 1),
                               input_bound_per_token=750, output_bound_per_token=1000)
        with self.assertRaises(ProviderError):
            self.provider().generate(self.request)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.state.budget("2026-10")["reserved_nanodollars"], 0)

    def test_wall_clock_jump_blocks_transmission(self):
        self.enable()
        calls = 0
        def changed_clock():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.clock.wall += 3600
            return self.clock()
        provider = ResponsesProvider(self.state, self.transport, ClockGuard(changed_clock), self.pricing).for_answer()
        with self.assertRaises(ProviderError):
            provider.generate(self.request)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.state.budget("2026-10")["reserved_nanodollars"], 0)

    def test_expired_dispatch_deadline_releases_unsent_reservation(self):
        self.enable()
        calls = 0
        def paused_clock():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.clock.advance(3)
            return self.clock()
        provider = ResponsesProvider(self.state, self.transport, ClockGuard(paused_clock), self.pricing).for_answer()
        with self.assertRaises(ProviderError):
            provider.generate(self.request)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.state.budget("2026-10")["reserved_nanodollars"], 0)

    def test_rollover_guard_blocks_without_transmission(self):
        self.enable()
        self.clock = FakeClock(datetime(2026, 10, 31, 23, 59, tzinfo=SINGAPORE))
        with self.assertRaises(ProviderError):
            self.provider().generate(self.request)
        self.assertEqual(self.transport.calls, [])

    def test_oversized_request_is_rejected_before_reservation_or_dispatch(self):
        self.enable()
        request = {**self.request, "input": "x" * 32_000}
        with self.assertRaises(ProviderError):
            self.provider().generate(request)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.state.budget("2026-10")["reserved_nanodollars"], 0)

    def test_retry_in_new_month_keeps_same_answer_id(self):
        self.enable()
        self.clock = FakeClock(datetime(2026, 10, 31, 23, 54, tzinfo=SINGAPORE))
        provider = self.provider()
        provider.generate(self.request)
        self.clock.advance(12 * 60)
        provider.generate(self.request)
        rows = self.state.db.execute("SELECT answer_id, month FROM attempts").fetchall()
        self.assertEqual(len({row[0] for row in rows}), 1)
        self.assertEqual({row[1] for row in rows}, {"2026-10", "2026-11"})

    def test_maximum_attempt_blocks_retry_if_answer_cap_has_no_room(self):
        self.enable()
        self.transport.usage = {"input_tokens": MAX_RESERVED_INPUT_TOKENS,
                                "output_tokens": MAX_OUTPUT_TOKENS}
        provider = self.provider()
        provider.generate(self.request)
        with self.assertRaises(ProviderError):
            provider.generate(self.request)
        self.assertEqual([path for path, _ in self.transport.calls], ["/responses"])
        self.assertEqual(self.transport.calls[0][1]["max_output_tokens"], 25000)
        budget = self.state.budget("2026-10")
        self.assertEqual(budget["settled_nanodollars"], 26_500_000)
        self.assertEqual(budget["reserved_nanodollars"], 0)

    def test_retry_remains_available_when_first_attempt_leaves_room(self):
        self.enable()
        provider = self.provider()
        provider.generate(self.request)
        provider.generate(self.request)
        self.assertEqual([path for path, _ in self.transport.calls], ["/responses", "/responses"])

    def test_transport_respects_source_lock_and_rejects_other_endpoints(self):
        with patch("marketlens_bot.provider.LIVE_TRANSPORT_ENABLED", False), \
                patch("marketlens_bot.provider._openai_https_handler"), \
                patch("marketlens_bot.provider.build_opener") as opener:
            with self.assertRaises(ProviderError):
                OpenAITransport("example-key").post("/responses", {}, None, None)
            opener.assert_not_called()
        with patch("marketlens_bot.provider.LIVE_TRANSPORT_ENABLED", True), \
                patch("marketlens_bot.provider._openai_https_handler"):
            with patch("marketlens_bot.provider.build_opener") as opener:
                with self.assertRaises(ProviderError):
                    OpenAITransport("example-key").post("/responses/input_tokens", {}, None, None)
                opener.assert_not_called()

    def test_live_transport_uses_fixed_endpoint_with_fake_opener(self):
        response = Mock()
        response.geturl.return_value = "https://api.openai.com/v1/responses"
        response.read.return_value = b'{"status":"completed"}'
        guard = Mock()
        with patch("marketlens_bot.provider.LIVE_TRANSPORT_ENABLED", True), \
                patch("marketlens_bot.provider._openai_https_handler"), \
                patch("marketlens_bot.provider.build_opener") as factory:
            factory.return_value.open.return_value.__enter__.return_value = response
            result = OpenAITransport("example-key").post("/responses", {}, "ticket", guard)
        self.assertEqual(result, {"status": "completed"})
        guard.check.assert_called_once_with("ticket")
        request = factory.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")

    def test_macos_openai_tls_uses_pinned_system_trust(self):
        context = ssl.create_default_context()
        truststore = Mock()
        truststore.SSLContext.return_value = context
        with patch("marketlens_bot.provider.sys.platform", "darwin"), \
                patch("marketlens_bot.provider.version", return_value="0.10.5"), \
                patch.dict("sys.modules", {"truststore": truststore}):
            handler = _openai_https_handler()
        truststore.SSLContext.assert_called_once_with(ssl.PROTOCOL_TLS_CLIENT)
        self.assertIs(handler._context, context)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_macos_openai_tls_missing_dependency_fails_closed(self):
        with patch("marketlens_bot.provider.sys.platform", "darwin"), \
                patch("marketlens_bot.provider.version", side_effect=PackageNotFoundError("truststore")):
            with self.assertRaises(ProviderError):
                _openai_https_handler()

    def test_macos_openai_tls_rejects_unpinned_dependency(self):
        with patch("marketlens_bot.provider.sys.platform", "darwin"), \
                patch("marketlens_bot.provider.version", return_value="0.10.4"):
            with self.assertRaises(ProviderError):
                _openai_https_handler()


if __name__ == "__main__":
    unittest.main()
