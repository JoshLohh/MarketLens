"""Durable, content-free state for the local Telegram prototype.

The caller owns a single process lock for the life of the bot.  No message,
question, answer, passage, token, or provider response belongs in this ledger.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import sqlite3
from typing import Iterator


ANSWER_LIMIT_NANODOLLARS = 30_000_000
MONTH_LIMIT_NANODOLLARS = 5_000_000_000
AI_CONSENT_VERSION = 4


class StateError(RuntimeError):
    """The local safety state cannot permit the requested operation."""


class State:
    """Own a locked SQLite ledger; all state transitions are transactions."""

    def __init__(self, directory: Path):
        directory = Path(directory).expanduser()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise StateError("State directory must be a real directory")
        os.chmod(directory, 0o700)
        self._lock = open(directory / "bot.lock", "a+b")
        os.chmod(directory / "bot.lock", 0o600)
        try:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock.close()
            raise StateError("Another bot process owns the state directory") from exc
        try:
            self.db = sqlite3.connect(directory / "state.sqlite3", isolation_level=None)
            os.chmod(directory / "state.sqlite3", 0o600)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    generation INTEGER NOT NULL DEFAULT 0,
                    purging INTEGER NOT NULL DEFAULT 0 CHECK (purging IN (0, 1)),
                    privacy_accepted INTEGER NOT NULL DEFAULT 0 CHECK (privacy_accepted IN (0, 1)),
                    ai_enabled INTEGER NOT NULL DEFAULT 0 CHECK (ai_enabled IN (0, 1)),
                    ai_consent_version INTEGER NOT NULL DEFAULT 1,
                    identity_hash TEXT NOT NULL DEFAULT ''
                );
                INSERT OR IGNORE INTO settings(singleton) VALUES(1);
                CREATE TABLE IF NOT EXISTS notices (
                    kind TEXT PRIMARY KEY CHECK (kind IN ('privacy', 'ai')),
                    generation INTEGER NOT NULL,
                    message_id INTEGER NOT NULL CHECK (message_id > 0)
                );
                CREATE TABLE IF NOT EXISTS updates (
                    update_id INTEGER NOT NULL CHECK (update_id >= 0),
                    state TEXT NOT NULL CHECK (state IN ('claimed', 'terminal')),
                    event_key TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (update_id, event_key)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    update_id INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('sending', 'sent', 'uncertain'))
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id TEXT PRIMARY KEY,
                    answer_id TEXT NOT NULL,
                    month TEXT NOT NULL,
                    reserved INTEGER NOT NULL CHECK (reserved > 0),
                    charged INTEGER CHECK (charged >= 0),
                    state TEXT NOT NULL CHECK (state IN ('reserved', 'settled', 'released'))
                );
            """)
            if "identity_hash" not in {row[1] for row in self.db.execute("PRAGMA table_info(settings)")}:
                self.db.execute("ALTER TABLE settings ADD COLUMN identity_hash TEXT NOT NULL DEFAULT ''")
            if "ai_consent_version" not in {row[1] for row in self.db.execute("PRAGMA table_info(settings)")}:
                self.db.execute("ALTER TABLE settings ADD COLUMN ai_consent_version INTEGER NOT NULL DEFAULT 1")
            columns = {row[1]: row for row in self.db.execute("PRAGMA table_info(updates)")}
            if columns["update_id"][5] == 1 and ("event_key" not in columns or columns["event_key"][5] == 0):
                if self.db.execute("SELECT 1 FROM updates LIMIT 1").fetchone():
                    raise StateError("Legacy update records need recovery before the bot can resume")
                # An empty old table can be upgraded without losing safety state.
                key = "COALESCE(event_key, '')" if "event_key" in columns else "''"
                self.db.execute("BEGIN IMMEDIATE")
                try:
                    self.db.execute("ALTER TABLE updates RENAME TO updates_old")
                    self.db.execute("CREATE TABLE updates (update_id INTEGER NOT NULL CHECK (update_id >= 0), "
                                    "state TEXT NOT NULL CHECK (state IN ('claimed', 'terminal')), "
                                    "event_key TEXT NOT NULL DEFAULT '', PRIMARY KEY (update_id, event_key))")
                    self.db.execute(f"INSERT INTO updates SELECT update_id, state, {key} FROM updates_old")
                    self.db.execute("DROP TABLE updates_old")
                    self.db.execute("COMMIT")
                except BaseException:
                    self.db.execute("ROLLBACK")
                    raise
            with self._transaction() as db:
                version = db.execute("SELECT ai_consent_version FROM settings WHERE singleton=1").fetchone()[0]
                if version != AI_CONSENT_VERSION:
                    db.execute("UPDATE settings SET ai_enabled=0, ai_consent_version=? WHERE singleton=1",
                               (AI_CONSENT_VERSION,))
                    db.execute("DELETE FROM notices WHERE kind='ai'")
        except Exception:
            if hasattr(self, "db"):
                self.db.close()
            self._lock.close()
            raise

    def close(self) -> None:
        self.db.close()
        self._lock.close()

    def __enter__(self) -> "State":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def status(self) -> dict:
        generation, purging, privacy, ai = self.db.execute(
            "SELECT generation, purging, privacy_accepted, ai_enabled FROM settings WHERE singleton=1"
        ).fetchone()
        return {"generation": generation, "purging": bool(purging),
                "privacy_accepted": bool(privacy), "ai_enabled": bool(ai)}

    def bind_identity(self, identity_hash: str) -> None:
        """Revoke consent when the configured chat or sender changes."""
        if not isinstance(identity_hash, str) or len(identity_hash) != 64:
            raise StateError("Invalid private identity")
        with self._transaction() as db:
            current = db.execute("SELECT identity_hash FROM settings WHERE singleton=1").fetchone()[0]
            if current != identity_hash:
                db.execute("UPDATE settings SET identity_hash=?, generation=generation+1, "
                           "privacy_accepted=0, ai_enabled=0 WHERE singleton=1", (identity_hash,))
                db.execute("DELETE FROM notices")

    def claim_update(self, update_id: int, event_key: str | None = None) -> bool:
        """Persist a content-free message identity before any side effect."""
        if type(update_id) is not int or update_id < 0:
            raise StateError("Invalid update ID")
        if event_key is not None and (not isinstance(event_key, str) or len(event_key) != 64):
            raise StateError("Invalid event identity")
        event_key = event_key or ""
        with self._transaction() as db:
            if not event_key and db.execute("SELECT 1 FROM updates WHERE update_id=?", (update_id,)).fetchone():
                return False
            cursor = db.execute("INSERT OR IGNORE INTO updates VALUES (?, 'claimed', ?)", (update_id, event_key))
            return cursor.rowcount == 1

    def finish_update(self, update_id: int, event_key: str | None = None) -> None:
        with self._transaction() as db:
            cursor = db.execute("UPDATE updates SET state='terminal' WHERE update_id=? AND event_key=?",
                                (update_id, event_key or ""))
            if cursor.rowcount == 0 and event_key:
                cursor = db.execute("UPDATE updates SET state='terminal' WHERE update_id=? AND event_key=''", (update_id,))
            if cursor.rowcount == 0 and not event_key:
                cursor = db.execute("UPDATE updates SET state='terminal' WHERE update_id=?", (update_id,))
            if cursor.rowcount < 1:
                raise StateError("Update was not claimed")

    def begin_send(self, update_id: int, event_key: str | None = None) -> int:
        """Persist delivery intent before contacting Telegram."""
        with self._transaction() as db:
            row = db.execute("SELECT state FROM updates WHERE update_id=? AND event_key=?",
                             (update_id, event_key or "")).fetchone()
            if row != ("claimed",):
                raise StateError("Delivery requires a claimed update")
            return db.execute("INSERT INTO deliveries(update_id, state) VALUES (?, 'sending')", (update_id,)).lastrowid

    def finish_send(self, sequence: int, *, sent: bool) -> None:
        with self._transaction() as db:
            cursor = db.execute("UPDATE deliveries SET state=? WHERE sequence=? AND state='sending'",
                                ("sent" if sent else "uncertain", sequence))
            if cursor.rowcount != 1:
                raise StateError("Delivery intent was not active")

    def delivery_status(self) -> str:
        row = self.db.execute("SELECT state FROM deliveries ORDER BY sequence DESC LIMIT 1").fetchone()
        return "none" if row is None else "uncertain" if row[0] == "sending" else row[0]

    def invalidate_notice(self, kind: str) -> None:
        if kind not in ("privacy", "ai"):
            raise StateError("Invalid notice kind")
        with self._transaction() as db:
            db.execute("DELETE FROM notices WHERE kind=?", (kind,))

    def record_notice(self, kind: str, message_id: int) -> None:
        """Record only a positively confirmed Telegram notice message."""
        if kind not in ("privacy", "ai") or type(message_id) is not int or message_id <= 0:
            raise StateError("Invalid notice")
        with self._transaction() as db:
            generation, purging, privacy = db.execute(
                "SELECT generation, purging, privacy_accepted FROM settings WHERE singleton=1"
            ).fetchone()
            if purging or (kind == "ai" and not privacy):
                raise StateError("Notice unavailable in the current state")
            db.execute("INSERT OR REPLACE INTO notices VALUES (?, ?, ?)", (kind, generation, message_id))

    def confirm_notice(self, kind: str, reply_to_id: int) -> bool:
        """Accept only a reply to the current notice in the current generation."""
        if kind not in ("privacy", "ai") or type(reply_to_id) is not int or reply_to_id <= 0:
            return False
        with self._transaction() as db:
            generation, purging, privacy = db.execute(
                "SELECT generation, purging, privacy_accepted FROM settings WHERE singleton=1"
            ).fetchone()
            notice = db.execute("SELECT generation, message_id FROM notices WHERE kind=?", (kind,)).fetchone()
            if purging or notice != (generation, reply_to_id) or (kind == "ai" and not privacy):
                return False
            column = "privacy_accepted" if kind == "privacy" else "ai_enabled"
            db.execute(f"UPDATE settings SET {column}=1 WHERE singleton=1")
            db.execute("DELETE FROM notices WHERE kind=?", (kind,))
            return True

    def disable_ai(self) -> None:
        with self._transaction() as db:
            db.execute("UPDATE settings SET ai_enabled=0 WHERE singleton=1")
            db.execute("DELETE FROM notices WHERE kind='ai'")

    def begin_purge(self) -> None:
        """Reset consent and enter durable drain mode; safety records survive."""
        with self._transaction() as db:
            current = db.execute("SELECT purging FROM settings WHERE singleton=1").fetchone()[0]
            if not current:
                db.execute("UPDATE settings SET generation=generation+1, purging=1, "
                           "privacy_accepted=0, ai_enabled=0 WHERE singleton=1")
                db.execute("DELETE FROM notices")

    def finish_purge(self) -> None:
        """Resume only after the caller has observed an empty Telegram queue."""
        with self._transaction() as db:
            db.execute("UPDATE settings SET purging=0 WHERE singleton=1")

    def reserve(self, attempt_id: str, answer_id: str, month: str, amount: int) -> None:
        """Reserve integer nanodollars against both answer and month caps."""
        if (not attempt_id or not answer_id or len(month) != 7 or month[4] != "-"
                or not (month[:4] + month[5:]).isdigit() or not 1 <= int(month[5:]) <= 12
                or type(amount) is not int or amount <= 0):
            raise StateError("Invalid budget reservation")
        with self._transaction() as db:
            self._reserve_in_transaction(db, attempt_id, answer_id, month, amount)

    @staticmethod
    def _reserve_in_transaction(db, attempt_id: str, answer_id: str, month: str, amount: int) -> None:
        purging, privacy, ai = db.execute(
            "SELECT purging, privacy_accepted, ai_enabled FROM settings WHERE singleton=1"
        ).fetchone()
        if purging or not privacy or not ai:
            raise StateError("AI requests are not enabled")
        usage = "CASE state WHEN 'reserved' THEN reserved WHEN 'settled' THEN charged ELSE 0 END"
        answer_total = db.execute(f"SELECT COALESCE(SUM({usage}),0) FROM attempts WHERE answer_id=?", (answer_id,)).fetchone()[0]
        # An unresolved transmission could be billed in another month after a
        # pause or restart, so keep it reserved against every month.
        month_total = db.execute(
            "SELECT COALESCE(SUM(CASE WHEN state='reserved' THEN reserved "
            "WHEN state='settled' AND month=? THEN charged ELSE 0 END),0) FROM attempts",
            (month,),
        ).fetchone()[0]
        if answer_total + amount > ANSWER_LIMIT_NANODOLLARS or month_total + amount > MONTH_LIMIT_NANODOLLARS:
            raise StateError("Budget cap would be exceeded")
        try:
            db.execute("INSERT INTO attempts VALUES (?, ?, ?, ?, NULL, 'reserved')",
                       (attempt_id, answer_id, month, amount))
        except sqlite3.IntegrityError as exc:
            raise StateError("Attempt ID already exists") from exc

    def reserve_for_dispatch(self, attempt_id: str, answer_id: str, amount: int, clock_guard):
        """Sample the guarded clock inside the transaction that reserves funds."""
        if not attempt_id or not answer_id or type(amount) is not int or amount <= 0:
            raise StateError("Invalid budget reservation")
        with self._transaction() as db:
            ticket = clock_guard.ticket()
            month = getattr(ticket, "month", None)
            if (not isinstance(month, str) or len(month) != 7 or month[4] != "-"
                    or not (month[:4] + month[5:]).isdigit() or not 1 <= int(month[5:]) <= 12):
                raise StateError("Invalid dispatch month")
            self._reserve_in_transaction(db, attempt_id, answer_id, month, amount)
        return ticket

    def settle(self, attempt_id: str, charged: int) -> None:
        if type(charged) is not int or charged < 0:
            raise StateError("Invalid charge")
        with self._transaction() as db:
            row = db.execute("SELECT reserved, state FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if not row or row[1] != "reserved" or charged > row[0]:
                raise StateError("Cannot settle this reservation")
            db.execute("UPDATE attempts SET state='settled', charged=? WHERE attempt_id=?", (charged, attempt_id))

    def release_undispatched(self, attempt_id: str) -> None:
        """Call only when the caller can prove transmission did not occur."""
        with self._transaction() as db:
            cursor = db.execute("UPDATE attempts SET state='released' WHERE attempt_id=? AND state='reserved'", (attempt_id,))
            if cursor.rowcount != 1:
                raise StateError("Reservation is not releasable")

    def budget(self, month: str) -> dict:
        row = self.db.execute("SELECT COALESCE(SUM(CASE WHEN state='settled' AND month=? THEN charged ELSE 0 END),0), "
                              "COALESCE(SUM(CASE WHEN state='reserved' THEN reserved ELSE 0 END),0) "
                              "FROM attempts", (month,)).fetchone()
        return {"month": month, "settled_nanodollars": row[0], "reserved_nanodollars": row[1]}
