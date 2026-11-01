"""Small, bounded Telegram transport and single-update polling loop."""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, version
import ssl
import sys
import time
from urllib.error import URLError
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener


class TelegramError(RuntimeError):
    """A Telegram operation failed or its outcome is uncertain."""


class TelegramSetupError(TelegramError):
    """The local Telegram transport is not configured safely."""


_TRUSTSTORE_SETUP = (
    "Telegram TLS setup requires truststore==0.10.5 in the project "
    "virtual environment; see README.md."
)


def _telegram_https_handler():
    """Use macOS system trust for Telegram without changing global TLS settings."""
    if sys.platform != "darwin":
        return HTTPSHandler()
    try:
        if version("truststore") != "0.10.5":
            raise TelegramSetupError(_TRUSTSTORE_SETUP)
        import truststore
    except (ImportError, PackageNotFoundError):
        raise TelegramSetupError(_TRUSTSTORE_SETUP) from None
    return HTTPSHandler(context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class TelegramClient:
    def __init__(self, token: str, *, timeout: float = 30):
        if not token or any(c.isspace() for c in token):
            raise ValueError("A Telegram bot token is required")
        self._token = token
        self._timeout = timeout
        self._opener = build_opener(_NoRedirect(), _telegram_https_handler())

    def _request(self, method: str, payload: dict) -> object:
        request = Request(f"https://api.telegram.org/bot{self._token}/{method}",
                          data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                body = response.read(1_000_001)
            if len(body) > 1_000_000:
                raise TelegramError("Telegram response exceeded the size limit")
            result = json.loads(body)
            if not isinstance(result, dict) or result.get("ok") is not True or "result" not in result:
                raise TelegramError("Telegram request failed")
            return result["result"]
        except (OSError, URLError, ValueError, TypeError) as exc:
            # Do not expose exception text: it may include a token-bearing URL.
            raise TelegramError("Telegram request failed or its outcome is uncertain") from None

    def get_updates(self, offset: int | None = None) -> list[dict]:
        payload = {"limit": 1, "timeout": 15, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = self._request("getUpdates", payload)
        if not isinstance(result, list) or len(result) > 1 or any(not isinstance(x, dict) for x in result):
            raise TelegramError("Invalid Telegram update response")
        return result

    def get_update_snapshot(self) -> list[dict]:
        """Read pending updates without an offset, so none are acknowledged."""
        result = self._request("getUpdates", {"limit": 100, "timeout": 0,
                                               "allowed_updates": ["message"]})
        if not isinstance(result, list) or len(result) > 100 or any(not isinstance(x, dict) for x in result):
            raise TelegramError("Invalid Telegram update response")
        return result

    def send_message(self, chat_id: int, text: str) -> int:
        result = self._request("sendMessage", {"chat_id": chat_id, "text": text})
        if not isinstance(result, dict) or type(result.get("message_id")) is not int or result["message_id"] <= 0:
            raise TelegramError("Telegram did not confirm the message")
        return result["message_id"]


def private_start_identity(updates: list[dict]) -> tuple[int, int] | None:
    """Return one unambiguous private /start chat/sender pair, without text."""
    if not isinstance(updates, list):
        return None
    identities = set()
    for update in updates:
        message = update.get("message") if isinstance(update, dict) else None
        if not isinstance(message, dict) or message.get("text") != "/start":
            continue
        chat, sender = message.get("chat"), message.get("from")
        if (not isinstance(chat, dict) or chat.get("type") != "private"
                or not isinstance(sender, dict) or sender.get("is_bot") is not False):
            continue
        chat_id, sender_id = chat.get("id"), sender.get("id")
        if type(chat_id) is int and chat_id > 0 and type(sender_id) is int and sender_id > 0:
            identities.add((chat_id, sender_id))
    return next(iter(identities)) if len(identities) == 1 else None


class Poller:
    """Acknowledge only the just-observed, durably completed update."""

    ACK_WINDOW_SECONDS = 23 * 60 * 60

    def __init__(self, client, bot, *, monotonic=time.monotonic):
        self.client, self.bot, self.monotonic = client, bot, monotonic
        self._pending = None

    def poll_once(self) -> bool:
        try:
            if self._pending is None:
                updates = self.client.get_updates()
                if not updates:
                    if self.bot.state.status()["purging"]:
                        self.bot.state.finish_purge()
                    return False
                update = updates[0]
                observed = self.monotonic()
            else:
                (update, observed), self._pending = self._pending, None
                age = self.monotonic() - observed
                if age < 0 or age >= self.ACK_WINDOW_SECONDS:
                    # Its acknowledgement window is no longer trustworthy.
                    # Discover again without an offset on the next poll.
                    return True
            update_id = update.get("update_id")
            if type(update_id) is not int or update_id < 0:
                raise TelegramError("Invalid Telegram update ID")
            self.bot.handle_update(update)
            elapsed = self.monotonic() - observed
            if elapsed < 0 or elapsed >= self.ACK_WINDOW_SECONDS:
                return True
            # A returned next update is retained in memory only. After an error
            # or restart the next request has no offset and redelivery is safe.
            following = self.client.get_updates(offset=update_id + 1)
            self._pending = (following[0], self.monotonic()) if following else None
            return True
        except TelegramError:
            self._pending = None
            return False
