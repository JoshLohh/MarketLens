"""Offline generated-answer integration with a fake OpenAI transport."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from marketlens_bot.answer import FilingAnswerer
from marketlens_bot.provider import ClockGuard, Pricing, ResponsesProvider, build_runtime_provider
from marketlens_bot.state import State
from test_bot_answer import pack
from test_bot_provider import FakeClock, FakeTransport


class ProviderAnswerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(Path(self.tmp.name) / "state")
        self.state.record_notice("privacy", 10)
        self.state.confirm_notice("privacy", 10)
        self.state.record_notice("ai", 20)
        self.state.confirm_notice("ai", 20)
        self.evidence = pack()
        self.manifest = {"documents": [{"ticker": "NBIS", "sec_filing_date": "2024-11-01"}]}

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def answer(self, payloads, ai_enabled=True):
        transport = FakeTransport(payloads)
        provider = ResponsesProvider(self.state, transport, ClockGuard(FakeClock()), Pricing(date(2026, 10, 1)))
        answerer = FilingAnswerer(self.manifest, [], object(), provider)
        with patch("marketlens_bot.answer.query_result", return_value={"pack": self.evidence}):
            response = answerer("What did NBIS report?", ai_enabled)
        return response, transport

    def test_fake_provider_returns_checked_citation(self):
        payload = {"status": "answered", "reason": "", "claims": [{
            "text": "Revenue was USD 10 million.", "ticker": "NBIS", "company_name": "Nebius Group",
            "period": "Q3 2024", "accounting_basis": "reported", "numbers": [],
            "citations": [{"chunk_id": "NBIS-S1:00001", "quote": "Revenue was USD 10 million."}]}]}
        answer, transport = self.answer([payload])
        self.assertIn("Revenue was USD 10 million.", answer)
        self.assertIn("https://www.sec.gov/example", answer)
        self.assertEqual([path for path, _ in transport.calls], ["/responses"])

    def test_model_abstention_is_not_replaced_with_local_excerpts(self):
        payload = {"status": "abstained", "reason": "Evidence is insufficient.", "claims": []}
        answer, transport = self.answer([payload])
        self.assertIn("supplied evidence was insufficient", answer)
        self.assertNotIn("These excerpts may help", answer)
        self.assertEqual(len(transport.calls), 1)

    def test_invented_quotes_are_not_returned_after_retry(self):
        payload = {"status": "answered", "reason": "", "claims": [{
            "text": "Unsupported statement.", "ticker": "NBIS", "company_name": "Nebius Group",
            "period": "Q3 2024", "accounting_basis": "reported", "numbers": [],
            "citations": [{"chunk_id": "NBIS-S1:00001", "quote": "invented quotation"}]}]}
        answer, transport = self.answer([payload, payload])
        self.assertNotIn("invented quotation", answer)
        self.assertNotIn("Unsupported statement", answer)
        self.assertIn("OpenAI explanation unavailable", answer)
        self.assertIn("did not pass the evidence or format checks", answer)
        self.assertIn("A request may have been charged", answer)
        self.assertIn("https://www.sec.gov/example", answer)
        self.assertEqual([path for path, _ in transport.calls], ["/responses", "/responses"])
        ids = self.state.db.execute("SELECT DISTINCT answer_id FROM attempts").fetchall()
        self.assertEqual(len(ids), 1)

    def test_incomplete_response_reports_fixed_diagnostic_without_provider_text(self):
        class IncompleteTransport(FakeTransport):
            def post(self, path, payload, ticket, guard):
                response = super().post(path, payload, ticket, guard)
                response.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"},
                                output=[{"private": "must-not-be-displayed"}])
                return response

        transport = IncompleteTransport()
        provider = ResponsesProvider(self.state, transport, ClockGuard(FakeClock()), Pricing(date(2026, 10, 1)))
        answerer = FilingAnswerer(self.manifest, [], object(), provider)
        with patch("marketlens_bot.answer.query_result", return_value={"pack": self.evidence}):
            answer = answerer("What did NBIS report?", ai_enabled=True)
        self.assertIn("reached its output limit", answer)
        self.assertNotIn("must-not-be-displayed", answer)
        self.assertIn("https://www.sec.gov/example", answer)

    def test_uncertain_provider_failure_returns_cited_local_fallback(self):
        transport = FakeTransport(fail_on="/responses")
        provider = ResponsesProvider(self.state, transport, ClockGuard(FakeClock()), Pricing(date(2026, 10, 1)))
        answerer = FilingAnswerer(self.manifest, [], object(), provider)
        with patch("marketlens_bot.answer.query_result", return_value={"pack": self.evidence}):
            answer = answerer("What did NBIS report?", ai_enabled=True)
        self.assertIn("OpenAI explanation unavailable", answer)
        self.assertIn("A request may have been charged", answer)
        self.assertIn("The OpenAI request could not be completed", answer)
        self.assertNotIn("uncertain fake transmission", answer)
        self.assertIn("https://www.sec.gov/example", answer)
        self.assertEqual(len(transport.calls), 1)
        self.assertGreater(self.state.budget("2026-10")["reserved_nanodollars"], 0)

    def test_reused_halted_provider_does_not_report_old_request(self):
        provider = ResponsesProvider(self.state, FakeTransport(), ClockGuard(FakeClock()), Pricing(date(2026, 10, 1))).for_answer()
        provider.halted = True
        provider.transmission_entered = True
        provider.safe_failure_category = "output_limit"
        answerer = FilingAnswerer(self.manifest, [], object(), provider)
        with patch("marketlens_bot.answer.query_result", return_value={"pack": self.evidence}):
            answer = answerer("What did NBIS report?", ai_enabled=True)
        self.assertIn("No OpenAI request was sent", answer)
        self.assertIn("provider was unavailable", answer)
        self.assertNotIn("output limit", answer)

    def test_pre_dispatch_clock_failure_returns_local_without_charge_warning(self):
        clock = FakeClock()
        clock.trusted = False
        transport = FakeTransport()
        provider = ResponsesProvider(self.state, transport, ClockGuard(clock), Pricing(date(2026, 10, 1)))
        answerer = FilingAnswerer(self.manifest, [], object(), provider)
        with patch("marketlens_bot.answer.query_result", return_value={"pack": self.evidence}):
            answer = answerer("What did NBIS report?", ai_enabled=True)
        self.assertIn("No OpenAI request was sent", answer)
        self.assertNotIn("may have been charged", answer)
        self.assertIn("https://www.sec.gov/example", answer)
        self.assertEqual(transport.calls, [])

    def test_consent_off_does_not_call_provider(self):
        answer, transport = self.answer([], ai_enabled=False)
        self.assertTrue(answer)
        self.assertEqual(transport.calls, [])

    def test_missing_provider_uses_cited_local_answer(self):
        answerer = FilingAnswerer(self.manifest, [], object(), provider=None)
        with patch("marketlens_bot.answer.query_result", return_value={"pack": self.evidence}):
            answer = answerer("What did NBIS report?", ai_enabled=True)
        self.assertIn("https://www.sec.gov/example", answer)
        self.assertNotIn("A request may have been charged", answer)

    def test_runtime_provider_factory_clock_is_used_for_checked_answer(self):
        payload = {"status": "answered", "reason": "", "claims": [{
            "text": "Revenue was USD 10 million.", "ticker": "NBIS", "company_name": "Nebius Group",
            "period": "Q3 2024", "accounting_basis": "reported", "numbers": [],
            "citations": [{"chunk_id": "NBIS-S1:00001", "quote": "Revenue was USD 10 million."}]}]}
        clock = FakeClock(datetime(2026, 11, 5, 12, tzinfo=timezone(timedelta(hours=8))))
        transport = FakeTransport([payload])
        with patch("marketlens_bot.provider.LIVE_TRANSPORT_ENABLED", True), \
                patch("marketlens_bot.provider.ChronyNTSClock", return_value=clock) as clock_factory, \
                patch("marketlens_bot.provider.OpenAITransport", return_value=transport):
            provider = build_runtime_provider(
                self.state, environment={"OPENAI_API_KEY": "offline-test-credential"}
            )
            answerer = FilingAnswerer(self.manifest, [], object(), provider)
            with patch("marketlens_bot.answer.query_result", return_value={"pack": self.evidence}):
                answer = answerer("What did NBIS report?", ai_enabled=True)

        clock_factory.assert_called_once_with()
        self.assertIs(provider.clock_guard.clock, clock)
        self.assertIn("Revenue was USD 10 million.", answer)
        self.assertIn("https://www.sec.gov/example", answer)
        self.assertEqual([path for path, _ in transport.calls], ["/responses"])


if __name__ == "__main__":
    unittest.main()
