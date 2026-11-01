"""Private Telegram command handling; answering is an injected boundary."""

from __future__ import annotations

from datetime import datetime
import hashlib
from zoneinfo import ZoneInfo

from .state import AI_CONSENT_VERSION
from .telegram import TelegramError


PRIVACY_NOTICE = (
    "MarketLens uses Telegram to receive your questions and send answers. Telegram retains incoming "
    "updates for up to 24 hours and your chat history is controlled by Telegram. This Mac keeps "
    "consent, delivery safety and budget records, plus the local filing corpus and vector database. "
    "The normal local data purge resets consent and eligible bot records; it does not delete the "
    "corpus, vector database, Telegram history or OpenAI data. Reply to this message with "
    "/privacy accept v1 to continue."
)
AI_CONFIRM_COMMAND = f"/ai confirm v{AI_CONSENT_VERSION}"
AI_NOTICE = (
    "OpenAI answers send your question and selected filing excerpts to OpenAI. API data is not "
    "used for training by default; abuse-monitoring logs may retain content for up to 30 days, "
    "with legal or safety exceptions. The budget check uses Cloudflare's authenticated time service. "
    "That time-only request "
    "contains no question or filing text, but Cloudflare can see your internet address. If time "
    "cannot be verified, the OpenAI request is blocked. "
    "The bot applies local estimated reservation limits of $0.03 per answer, including up to "
    "two attempts, and $5 per Singapore calendar month. Actual OpenAI charges can differ from "
    "these estimates; other API use is not included. Requests that cannot fit the local bounds "
    "are not sent to OpenAI. "
    "/ai off is processed after an active "
    "answer; a request already sent may finish and an already sent or uncertain reply may arrive "
    f"before or after the confirmation. Reply to this message with {AI_CONFIRM_COMMAND} to opt in."
)
OVERFLOW = "The answer and its citations are too long for one message. Please narrow your question and resend it."


def bounded_reply(text: str) -> str:
    """Keep one complete reply, counting Telegram's UTF-16 code units."""
    if not isinstance(text, str) or not text:
        return "I could not produce a supported answer. Please try a narrower question."
    return text if len(text.encode("utf-16-le")) // 2 <= 3800 else OVERFLOW


class Bot:
    def __init__(self, state, telegram, *, chat_id: int, sender_id: int, answer,
                 provider_configured: bool = False):
        if type(chat_id) is not int or type(sender_id) is not int or chat_id <= 0 or sender_id <= 0:
            raise ValueError("Private chat and sender IDs must be positive integers")
        state.bind_identity(hashlib.sha256(f"{chat_id}:{sender_id}".encode()).hexdigest())
        self.state, self.telegram, self.chat_id, self.sender_id, self.answer = (
            state, telegram, chat_id, sender_id, answer
        )
        self.provider_configured = provider_configured

    def _send(self, text: str) -> int:
        intent = self.state.begin_send(self._active_update, self._active_event_key)
        try:
            message_id = self.telegram.send_message(self.chat_id, bounded_reply(text))
        except Exception:
            self.state.finish_send(intent, sent=False)
            raise
        self.state.finish_send(intent, sent=True)
        return message_id

    def _notice(self, kind: str, text: str) -> None:
        self.state.invalidate_notice(kind)
        notice_id = self._send(text)
        self.state.record_notice(kind, notice_id)

    def _reply_id(self, message: dict) -> int | None:
        reply = message.get("reply_to_message")
        if not isinstance(reply, dict) or not isinstance(reply.get("chat"), dict):
            return None
        if reply["chat"].get("id") != self.chat_id:
            return None
        return reply.get("message_id")

    def handle_update(self, update: dict) -> None:
        update_id = update["update_id"]
        message = update.get("message")
        chat = message.get("chat") if isinstance(message, dict) else None
        message_id = message.get("message_id") if isinstance(message, dict) else None
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        event_key = None
        if type(chat_id) is int and type(message_id) is int and message_id > 0:
            event_key = hashlib.sha256(f"{chat_id}:{message_id}".encode()).hexdigest()
        if not self.state.claim_update(update_id, event_key):
            # A claimed-but-unfinished operation may already have sent a request.
            self.state.finish_update(update_id, event_key)
            return
        self._active_update = update_id
        self._active_event_key = event_key
        try:
            if self.state.status()["purging"]:
                return
            message = update.get("message")
            if not isinstance(message, dict):
                return
            chat, sender = message.get("chat"), message.get("from")
            if (not isinstance(chat, dict) or not isinstance(sender, dict)
                    or chat.get("type") != "private" or chat.get("id") != self.chat_id
                    or sender.get("id") != self.sender_id or sender.get("is_bot") is not False):
                return
            text = message.get("text")
            if not isinstance(text, str) or not text.strip():
                return
            text = text.strip()
            if text == "/start":
                self._notice("privacy", PRIVACY_NOTICE)
            elif text == "/privacy accept v1":
                ok = self.state.confirm_notice("privacy", self._reply_id(message))
                self._send("Privacy notice accepted." if ok else "Reply to the current privacy notice to accept it. Send /start for a new notice.")
            elif not self.state.status()["privacy_accepted"]:
                self._send("Send /start and reply to the privacy notice before asking questions.")
            elif text == "/ai on":
                self._notice("ai", AI_NOTICE)
            elif text == AI_CONFIRM_COMMAND:
                ok = self.state.confirm_notice("ai", self._reply_id(message))
                self._send("OpenAI consent enabled. Answers require a configured provider." if ok else "Reply to the current OpenAI notice. Send /ai on for a new notice.")
            elif text == "/ai off":
                self.state.disable_ai()
                self._send("OpenAI consent is off. Earlier sent or uncertain replies may still arrive.")
            elif text == "/status":
                status = self.state.status()
                self._send("OpenAI consent: " + ("on" if status["ai_enabled"] else "off") +
                           ". OpenAI provider: " + ("configured (connection not verified)" if self.provider_configured else "not configured") +
                           ". Last delivery: " + self.state.delivery_status() +
                           ". An uncertain reply is not resent; resubmitting is a new request and may incur a charge.")
            elif text == "/budget":
                month = datetime.now(ZoneInfo("Asia/Singapore")).strftime("%Y-%m")
                amounts = self.state.budget(month)
                self._send(f"Bot-local budget for {month}: accounted up to ${amounts['settled_nanodollars'] / 1_000_000_000:.9f}; "
                           f"unresolved reservations across all months ${amounts['reserved_nanodollars'] / 1_000_000_000:.9f}; "
                           "per-answer limit $0.03; monthly limit $5. "
                           "Unresolved reservations count against each month. Actual charges may be higher or lower than these estimates; "
                           "other API use and invoice timing are outside this limit.")
            elif text.startswith("/"):
                self._send(f"Commands: /start, /privacy accept v1, /ai on, {AI_CONFIRM_COMMAND}, /ai off, /status, /budget.")
            else:
                self._send(self.answer(text, self.state.status()["ai_enabled"]))
        except TelegramError:
            # A timeout may mean Telegram received the message. Never resend it.
            pass
        finally:
            self.state.finish_update(update_id, event_key)
