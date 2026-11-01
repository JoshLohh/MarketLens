"""Offline checks of the bot's durable safety state; no services are used."""

import tempfile
import unittest
import sqlite3
from pathlib import Path

from marketlens_bot.state import (AI_CONSENT_VERSION, ANSWER_LIMIT_NANODOLLARS,
                                  MONTH_LIMIT_NANODOLLARS, State, StateError)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state"
        self.state = State(self.path)

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def enable_ai(self):
        self.state.record_notice("privacy", 10)
        self.assertTrue(self.state.confirm_notice("privacy", 10))
        self.state.record_notice("ai", 20)
        self.assertTrue(self.state.confirm_notice("ai", 20))

    def test_notice_reply_must_match_current_notice_and_generation(self):
        self.state.record_notice("privacy", 10)
        self.state.invalidate_notice("privacy")
        self.state.record_notice("privacy", 11)
        self.assertFalse(self.state.confirm_notice("privacy", 10))
        self.assertTrue(self.state.confirm_notice("privacy", 11))
        self.state.record_notice("ai", 20)
        self.state.disable_ai()
        self.assertFalse(self.state.confirm_notice("ai", 20))
        self.state.record_notice("ai", 21)
        self.state.begin_purge()
        self.assertFalse(self.state.confirm_notice("ai", 21))
        self.assertEqual(self.state.status(), {"generation": 1, "purging": True,
                                                "privacy_accepted": False, "ai_enabled": False})

    def test_purge_survives_restart_and_retains_replay_and_budget_records(self):
        self.enable_ai()
        self.assertTrue(self.state.claim_update(123))
        self.state.finish_update(123)
        self.state.reserve("attempt-1", "answer-1", "2026-10", 500)
        self.state.begin_purge()
        self.state.close()
        self.state = State(self.path)
        self.assertTrue(self.state.status()["purging"])
        self.assertFalse(self.state.claim_update(123))
        self.assertEqual(self.state.budget("2026-10")["reserved_nanodollars"], 500)
        self.state.begin_purge()
        self.assertEqual(self.state.status()["generation"], 1)
        self.state.finish_purge()
        self.assertFalse(self.state.status()["purging"])
        self.assertFalse(self.state.status()["privacy_accepted"])

    def test_claimed_update_is_not_replayed_after_restart(self):
        self.assertTrue(self.state.claim_update(999))
        self.state.close()
        self.state = State(self.path)
        self.assertFalse(self.state.claim_update(999))
        self.state.finish_update(999)
        self.assertTrue(self.state.claim_update(4))

    def test_retry_in_new_month_still_obeys_per_answer_cap(self):
        self.enable_ai()
        self.state.reserve("first", "answer", "2026-10", 26_000_000)
        self.state.settle("first", 26_000_000)
        with self.assertRaises(StateError):
            self.state.reserve("retry", "answer", "2026-11", 4_000_001)
        self.state.reserve("retry", "answer", "2026-11", 4_000_000)
        self.assertEqual(self.state.budget("2026-10")["settled_nanodollars"], 26_000_000)
        self.assertEqual(self.state.budget("2026-11")["reserved_nanodollars"], 4_000_000)

    def test_unknown_charge_remains_reserved_and_cannot_be_settled_above_cap(self):
        self.enable_ai()
        self.state.reserve("unknown", "answer", "2026-10", ANSWER_LIMIT_NANODOLLARS)
        with self.assertRaises(StateError):
            self.state.settle("unknown", ANSWER_LIMIT_NANODOLLARS + 1)
        with self.assertRaises(StateError):
            self.state.reserve("again", "answer", "2026-10", 1)
        self.assertEqual(self.state.budget("2026-10")["reserved_nanodollars"], ANSWER_LIMIT_NANODOLLARS)

    def test_monthly_limit_counts_unresolved_reservations(self):
        self.enable_ai()
        full_answers = MONTH_LIMIT_NANODOLLARS // ANSWER_LIMIT_NANODOLLARS
        for number in range(full_answers):
            self.state.reserve(f"attempt-{number}", f"answer-{number}", "2026-10", ANSWER_LIMIT_NANODOLLARS)
        with self.assertRaises(StateError):
            self.state.reserve("over", "another-answer", "2026-10", ANSWER_LIMIT_NANODOLLARS)
        self.state.reserve("next-month", "new-answer", "2026-11", 1)

    def test_unresolved_reservations_count_in_every_month_after_restart(self):
        self.enable_ai()
        self.state.reserve("unknown", "first-answer", "2026-10", 20_000_000)
        self.state.close()
        self.state = State(self.path)
        self.assertEqual(self.state.budget("2026-11")["reserved_nanodollars"], 20_000_000)
        self.state.settle("unknown", 10_000_000)
        self.assertEqual(self.state.budget("2026-10")["settled_nanodollars"], 10_000_000)
        self.assertEqual(self.state.budget("2026-11")["reserved_nanodollars"], 0)
        self.assertEqual(self.state.budget("2026-11")["settled_nanodollars"], 0)

    def test_prior_month_unresolved_reservation_reduces_new_month_limit(self):
        self.enable_ai()
        self.state.reserve("unknown", "old-answer", "2026-10", 30_000_000)
        rows = [(f"settled-{i}", f"answer-{i}", "2026-11", 30_000_000,
                 30_000_000, "settled") for i in range(165)]
        self.state.db.executemany("INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?)", rows)
        with self.assertRaises(StateError):
            self.state.reserve("new", "new-answer", "2026-11", 25_000_000)
        self.assertEqual(self.state.budget("2026-11")["reserved_nanodollars"], 30_000_000)

    def test_old_ai_consent_is_revoked_after_policy_version_change(self):
        self.enable_ai()
        self.state.record_notice("ai", 21)
        self.state.reserve("old-attempt", "old-answer", "2026-10", 500)
        self.state.db.execute("UPDATE settings SET ai_consent_version=3 WHERE singleton=1")
        self.state.close()
        self.state = State(self.path)
        self.assertEqual(AI_CONSENT_VERSION, 4)
        self.assertTrue(self.state.status()["privacy_accepted"])
        self.assertFalse(self.state.status()["ai_enabled"])
        self.assertIsNone(self.state.db.execute("SELECT 1 FROM notices WHERE kind='ai'").fetchone())
        self.assertEqual(self.state.budget("2026-10")["reserved_nanodollars"], 500)

    def test_ai_disabled_or_purging_refuses_reservation(self):
        with self.assertRaises(StateError):
            self.state.reserve("a", "answer", "2026-10", 1)
        self.enable_ai()
        self.state.begin_purge()
        with self.assertRaises(StateError):
            self.state.reserve("a", "answer", "2026-10", 1)

    def test_second_process_owner_is_refused(self):
        with self.assertRaises(StateError):
            State(self.path)

    def test_nonempty_legacy_update_ledger_fails_closed(self):
        self.state.close()
        db = sqlite3.connect(self.path / "state.sqlite3")
        db.execute("DROP TABLE updates")
        db.execute("CREATE TABLE updates(update_id INTEGER PRIMARY KEY, state TEXT NOT NULL)")
        db.execute("INSERT INTO updates VALUES(4, 'terminal')")
        db.commit()
        db.close()
        with self.assertRaises(StateError):
            State(self.path)
        # Restore a closable handle for fixture cleanup after the failed open.
        self.state = sqlite3.connect(":memory:")


if __name__ == "__main__":
    unittest.main()
