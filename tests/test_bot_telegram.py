"""Fake-Telegram checks for private access, consent, polling and delivery."""

import tempfile
import unittest
import ssl
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.request import HTTPSHandler

from marketlens_bot.bot import Bot, OVERFLOW, bounded_reply
from marketlens_bot.state import State
from marketlens_bot.telegram import (Poller, TelegramClient, TelegramError,
                                     TelegramSetupError, _telegram_https_handler,
                                     private_start_identity)


def update(update_id, text, *, chat_id=11, sender_id=22, reply_to=None, message_id=None):
    message = {"message_id": update_id if message_id is None else message_id,
               "chat": {"id": chat_id, "type": "private"},
               "from": {"id": sender_id, "is_bot": False}, "text": text}
    if reply_to is not None:
        message["reply_to_message"] = {"message_id": reply_to, "chat": {"id": chat_id}}
    return {"update_id": update_id, "message": message}


class FakeTelegram:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.offsets = []
        self.sent = []
        self.fail_send = False

    def get_updates(self, offset=None):
        self.offsets.append(offset)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        if self.fail_send:
            raise TelegramError("uncertain")
        return len(self.sent)


class BotTelegramTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(Path(self.tmp.name) / "state")
        self.telegram = FakeTelegram()
        self.questions = []
        self.bot = Bot(self.state, self.telegram, chat_id=11, sender_id=22, answer=self.answer)

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def answer(self, question, ai_enabled):
        self.questions.append((question, ai_enabled))
        return "Cited test answer"

    def test_private_allowlist_and_reply_bound_consent(self):
        self.bot.handle_update(update(1, "/start", sender_id=23))
        self.assertEqual(self.telegram.sent, [])
        self.bot.handle_update(update(2, "/start"))
        self.bot.handle_update(update(3, "/privacy accept v1"))
        self.assertFalse(self.state.status()["privacy_accepted"])
        self.bot.handle_update(update(4, "/privacy accept v1", reply_to=1))
        self.assertTrue(self.state.status()["privacy_accepted"])
        self.bot.handle_update(update(5, "/ai on"))
        ai_notice = len(self.telegram.sent)
        self.bot.handle_update(update(6, "/ai off"))
        self.bot.handle_update(update(7, "/ai confirm v4", reply_to=ai_notice))
        self.assertFalse(self.state.status()["ai_enabled"])

    def test_old_ai_confirmation_version_cannot_accept_new_budget_notice(self):
        self.bot.handle_update(update(1, "/start"))
        self.bot.handle_update(update(2, "/privacy accept v1", reply_to=1))
        self.bot.handle_update(update(3, "/ai on"))
        ai_notice = len(self.telegram.sent)
        self.assertIn("$0.03 per answer", self.telegram.sent[ai_notice - 1][1])
        self.assertIn("Cloudflare's authenticated time service", self.telegram.sent[ai_notice - 1][1])
        self.assertIn("contains no question or filing text", self.telegram.sent[ai_notice - 1][1])
        self.assertIn("Actual OpenAI charges can differ", self.telegram.sent[ai_notice - 1][1])
        self.bot.handle_update(update(4, "/ai confirm v3", reply_to=ai_notice))
        self.assertFalse(self.state.status()["ai_enabled"])
        self.bot.handle_update(update(5, "/ai confirm v4", reply_to=ai_notice))
        self.assertTrue(self.state.status()["ai_enabled"])

    def test_uncertain_send_is_not_repeated_on_redelivery(self):
        self.telegram.fail_send = True
        self.bot.handle_update(update(1, "/start"))
        self.telegram.fail_send = False
        self.bot.handle_update(update(1, "/start"))
        self.assertEqual(len(self.telegram.sent), 1)
        self.assertEqual(self.state.delivery_status(), "uncertain")
        self.bot.handle_update(update(2, "/privacy accept v1", reply_to=1))
        self.assertFalse(self.state.status()["privacy_accepted"])

    def test_reused_update_id_with_new_message_id_is_not_dropped(self):
        self.bot.handle_update(update(100, "/start", message_id=1000))
        self.bot.handle_update(update(100, "/start", message_id=2000))
        self.assertEqual(len(self.telegram.sent), 2)

    def test_changing_allowlist_identity_revokes_prior_consent(self):
        self.bot.handle_update(update(1, "/start"))
        self.bot.handle_update(update(2, "/privacy accept v1", reply_to=1))
        self.bot.handle_update(update(3, "/ai on"))
        self.bot.handle_update(update(4, "/ai confirm v4", reply_to=3))
        self.assertTrue(self.state.status()["ai_enabled"])
        replacement = Bot(self.state, self.telegram, chat_id=33, sender_id=44, answer=self.answer)
        self.assertFalse(self.state.status()["privacy_accepted"])
        self.assertFalse(self.state.status()["ai_enabled"])
        replacement.handle_update(update(5, "/privacy accept v1", chat_id=33, sender_id=44, reply_to=1))
        self.assertFalse(self.state.status()["privacy_accepted"])

    def test_budget_describes_conservative_accounting(self):
        self.bot.handle_update(update(1, "/start"))
        self.bot.handle_update(update(2, "/privacy accept v1", reply_to=1))
        self.bot.handle_update(update(3, "/budget"))
        self.assertIn("accounted up to", self.telegram.sent[-1][1])
        self.assertIn("Unresolved reservations count against each month", self.telegram.sent[-1][1])
        self.assertIn("per-answer limit $0.03", self.telegram.sent[-1][1])
        self.assertIn("Actual charges may be higher or lower", self.telegram.sent[-1][1])

    def test_status_reports_consent_and_provider_configuration(self):
        self.bot.handle_update(update(1, "/start"))
        self.bot.handle_update(update(2, "/privacy accept v1", reply_to=1))
        self.bot.handle_update(update(3, "/status"))
        self.assertIn("OpenAI consent: off", self.telegram.sent[-1][1])
        self.assertIn("OpenAI provider: not configured", self.telegram.sent[-1][1])
        self.bot.provider_configured = True
        self.bot.handle_update(update(4, "/status"))
        self.assertIn("OpenAI provider: configured", self.telegram.sent[-1][1])

    def test_poller_handles_ack_result_and_lower_id_after_idle(self):
        client = FakeTelegram([[update(100, "/start")], [update(101, "/start")], [],
                               [update(4, "/start")], []])
        bot = Bot(self.state, client, chat_id=11, sender_id=22, answer=self.answer)
        poller = Poller(client, bot)
        self.assertTrue(poller.poll_once())
        self.assertTrue(poller.poll_once())
        self.assertTrue(poller.poll_once())
        self.assertEqual(client.offsets, [None, 101, 102, None, 5])
        self.assertEqual(len(client.sent), 3)

    def test_lost_ack_returns_to_offsetless_and_does_not_resend(self):
        first = update(100, "/start")
        client = FakeTelegram([[first], TelegramError("timeout"), [first], []])
        bot = Bot(self.state, client, chat_id=11, sender_id=22, answer=self.answer)
        poller = Poller(client, bot)
        self.assertFalse(poller.poll_once())
        self.assertTrue(poller.poll_once())
        self.assertEqual(client.offsets, [None, 101, None, 101])
        self.assertEqual(len(client.sent), 1)

    def test_expired_pending_update_is_not_acknowledged_with_its_old_offset(self):
        now = [100.0]
        client = FakeTelegram([[update(100, "/start")], [update(101, "/start")],
                               [update(4, "/start")], []])
        bot = Bot(self.state, client, chat_id=11, sender_id=22, answer=self.answer)
        poller = Poller(client, bot, monotonic=lambda: now[0])
        self.assertTrue(poller.poll_once())
        now[0] += Poller.ACK_WINDOW_SECONDS
        self.assertTrue(poller.poll_once())
        self.assertTrue(poller.poll_once())
        self.assertEqual(client.offsets, [None, 101, None, 5])
        self.assertEqual(len(client.sent), 2)

    def test_purge_discards_queued_updates_until_empty(self):
        self.state.begin_purge()
        client = FakeTelegram([[update(50, "/start")], [], []])
        bot = Bot(self.state, client, chat_id=11, sender_id=22, answer=self.answer)
        poller = Poller(client, bot)
        self.assertTrue(poller.poll_once())
        self.assertTrue(self.state.status()["purging"])
        self.assertFalse(poller.poll_once())
        self.assertFalse(self.state.status()["purging"])
        self.assertEqual(client.sent, [])
        self.assertFalse(self.state.claim_update(50))

    def test_reply_bound_counts_utf16_without_cutting_citations(self):
        self.assertEqual(bounded_reply("😀" * 1900), "😀" * 1900)
        self.assertEqual(bounded_reply("😀" * 1901), OVERFLOW)


class TelegramIdentityTests(unittest.TestCase):
    def test_private_start_identity_requires_one_sender(self):
        first = update(1, "/start")
        self.assertEqual(private_start_identity([first, first]), (11, 22))
        self.assertIsNone(private_start_identity([first, update(2, "/start", chat_id=33, sender_id=44)]))
        group = update(3, "/start")
        group["message"]["chat"]["type"] = "group"
        self.assertIsNone(private_start_identity([group]))

    def test_identity_snapshot_does_not_acknowledge_updates(self):
        class SnapshotClient(TelegramClient):
            def __init__(self):
                self.request = None

            def _request(self, method, payload):
                self.request = (method, payload)
                return []

        client = SnapshotClient()
        self.assertEqual(client.get_update_snapshot(), [])
        method, payload = client.request
        self.assertEqual(method, "getUpdates")
        self.assertNotIn("offset", payload)
        self.assertEqual(payload["timeout"], 0)


class TelegramTLSSetupTests(unittest.TestCase):
    def test_macos_uses_system_trust_with_client_verification(self):
        context = object()
        truststore = Mock()
        truststore.SSLContext.return_value = context
        with patch("marketlens_bot.telegram.sys.platform", "darwin"), \
             patch("marketlens_bot.telegram.version", return_value="0.10.5"), \
             patch.dict("sys.modules", {"truststore": truststore}):
            handler = _telegram_https_handler()
        self.assertIsInstance(handler, HTTPSHandler)
        self.assertIs(handler._context, context)
        truststore.SSLContext.assert_called_once_with(ssl.PROTOCOL_TLS_CLIENT)

    def test_macos_rejects_an_unpinned_version(self):
        with patch("marketlens_bot.telegram.sys.platform", "darwin"), \
             patch("marketlens_bot.telegram.version", return_value="0.10.4"):
            with self.assertRaisesRegex(TelegramSetupError, "truststore==0.10.5"):
                _telegram_https_handler()

    def test_macos_requires_the_dependency(self):
        with patch("marketlens_bot.telegram.sys.platform", "darwin"), \
             patch("marketlens_bot.telegram.version", side_effect=PackageNotFoundError("truststore")):
            with self.assertRaisesRegex(TelegramSetupError, "truststore==0.10.5"):
                _telegram_https_handler()


if __name__ == "__main__":
    unittest.main()
