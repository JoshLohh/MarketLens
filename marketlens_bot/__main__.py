"""Command-line entry point for the private local prototype."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

from .answer import FilingAnswerer
from .bot import Bot
from .pilot import check_parity, load_pilot, parity_identity
from .provider import build_runtime_provider
from .state import State, StateError
from .telegram import Poller, TelegramClient, TelegramError, TelegramSetupError, private_start_identity
from .trusted_clock import ChronyNTSClock, ClockSourceError
from .weaviate_client import WeaviateClient, WeaviateError, bootstrap_collection
from .weaviate_index import IndexError, WeaviateIndex


def state_directory() -> Path:
    return Path(os.environ.get("MARKETLENS_STATE_DIR", "~/.local/share/marketlens")).expanduser()


def weaviate_client() -> WeaviateClient:
    return WeaviateClient(os.environ.get("WEAVIATE_API_KEY", ""))


def telegram_bot(state: State, answer, *, provider_configured=False) -> tuple[TelegramClient, Bot]:
    try:
        chat_id = int(os.environ.get("MARKETLENS_CHAT_ID", ""))
        sender_id = int(os.environ.get("MARKETLENS_SENDER_ID", ""))
    except ValueError:
        raise ValueError("Set positive private chat and sender IDs") from None
    client = TelegramClient(os.environ.get("MARKETLENS_TELEGRAM_TOKEN", ""))
    return client, Bot(state, client, chat_id=chat_id, sender_id=sender_id, answer=answer,
                       provider_configured=provider_configured)


def save_parity(directory: Path, identity: dict, count: int) -> None:
    target = directory / "retrieval-parity.json"
    temporary = directory / "retrieval-parity.tmp"
    content = {"identity": identity, "development_cases_passed": count}
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(content, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)


def require_parity(directory: Path, identity: dict) -> None:
    try:
        result = json.loads((directory / "retrieval-parity.json").read_text())
    except (OSError, ValueError):
        raise IndexError("Development retrieval parity has not been recorded") from None
    if result != {"identity": identity, "development_cases_passed": 21}:
        raise IndexError("Retrieval parity is stale or incomplete")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m marketlens_bot")
    commands = parser.add_subparsers(dest="command", required=True)
    data = commands.add_parser("data")
    data.add_argument("action", choices=("status", "purge"))
    commands.add_parser("bootstrap")
    parity = commands.add_parser("parity")
    parity.add_argument("--split", choices=("development", "holdout"), default="development")
    commands.add_parser("drain")
    commands.add_parser("telegram-ids")
    commands.add_parser("clock-check")
    commands.add_parser("run")
    args = parser.parse_args(argv)
    if args.command == "clock-check":
        try:
            ChronyNTSClock()()
        except ClockSourceError as exc:
            print(f"Authenticated time check failed: {exc}", file=sys.stderr)
            return 1
        print("Authenticated time check passed; the Mac clock is within one second. The clock was not changed.")
        return 0
    if args.command == "telegram-ids":
        try:
            token = os.environ.get("MARKETLENS_TELEGRAM_TOKEN", "")
            if not token:
                raise ValueError("Telegram token is missing")
            identity = private_start_identity(TelegramClient(token).get_update_snapshot())
        except TelegramSetupError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except (TelegramError, ValueError):
            print("Telegram identity lookup failed. Check the token and try again.", file=sys.stderr)
            return 1
        if identity is None:
            print("No unique private /start sender is pending. Send /start to the bot, then retry.",
                  file=sys.stderr)
            return 1
        print("Private chat ID:", identity[0])
        print("Sender ID:", identity[1])
        print("The /start update remains pending for the bot to process.")
        return 0
    directory = state_directory()
    try:
        with State(directory) as state:
            if args.command == "data":
                if args.action == "purge":
                    state.begin_purge()
                    print("Consent was reset and purge mode is active. Run the drain command to discard queued Telegram updates.")
                else:
                    current = state.status()
                    print("Local state:", json.dumps(current, sort_keys=True))
                    print("Budget and replay-safety records are retained. The filing corpus, cache and Weaviate volume are separate.")
                    print("Telegram history and OpenAI retention are outside this local reset.")
                return 0
            if args.command == "drain":
                if not state.status()["purging"]:
                    print("No purge is active.")
                    return 0
                telegram, bot = telegram_bot(state, lambda *_: "")
                poller = Poller(telegram, bot)
                while state.status()["purging"]:
                    if not poller.poll_once() and state.status()["purging"]:
                        time.sleep(2)
                print("Purge drain completed. Consent remains off.")
                return 0
            manifest, chunks, local = load_pilot()
            client = weaviate_client()
            if args.command == "bootstrap":
                bootstrap_collection(client, local)
                print("Weaviate collection matches all 8,279 existing cached windows.")
                return 0
            index = WeaviateIndex(local, client)
            index.verify_collection()
            identity = parity_identity(manifest, local, client)
            if args.command == "parity":
                count = check_parity(manifest, chunks, local, index, split=args.split)
                if args.split == "development":
                    save_parity(directory, identity, count)
                print(f"{args.split} retrieval parity: {count} cases matched.")
                if args.split == "holdout":
                    print("These previously exposed cases are regression checks, not a blind evaluation.")
                return 0
            require_parity(directory, identity)
            if state.status()["purging"]:
                raise StateError("Finish the purge drain before starting the bot")
            provider = build_runtime_provider(state)
            answerer = FilingAnswerer(manifest, chunks, index, provider=provider)
            telegram, bot = telegram_bot(state, answerer, provider_configured=provider is not None)
            poller = Poller(telegram, bot)
            print("MarketLens is polling. " + ("OpenAI provider configured; consent is required."
                  if provider is not None else "OpenAI provider not configured; local answers only."))
            while True:
                if not poller.poll_once():
                    time.sleep(2)
    except KeyboardInterrupt:
        return 0
    except (StateError, WeaviateError, IndexError, ValueError, TelegramSetupError) as exc:
        print(f"MarketLens stopped: {exc}", file=sys.stderr)
        return 1
    except Exception:
        # Never print a token, question, filing excerpt or provider response.
        print("MarketLens stopped because setup or a local operation failed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
