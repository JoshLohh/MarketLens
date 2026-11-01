"""Offline checks for the authenticated, read-only macOS clock adapter."""

from types import SimpleNamespace
import os
import unittest
from unittest.mock import patch

from marketlens_bot.trusted_clock import (
    CHRONY_TIMEOUT_SECONDS,
    ClockSourceError,
    ChronyNTSClock,
    MAX_LOCAL_OFFSET_SECONDS,
    NTS_SERVER,
    PROCESS_TIMEOUT_SECONDS,
    REFRESH_AUTHENTICATED_SAMPLE_AFTER_SECONDS,
)


class FakeRunner:
    def __init__(self, output="2026-11-01T00:00:00Z System clock wrong by 0.000100 seconds (ignored)",
                 returncode=0, error=None):
        self.output = output
        self.returncode = returncode
        self.error = error
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(returncode=self.returncode, stdout=self.output, stderr="")


class TrustedClockTests(unittest.TestCase):
    def make_clock(self, runner, wall=None, monotonic=None):
        return ChronyNTSClock(
            executable="/opt/homebrew/opt/chrony/sbin/chronyd",
            runner=runner,
            wall_clock=wall or (lambda: 1_798_776_000.0),
            monotonic_clock=monotonic or (lambda: 100.0),
        )

    def test_uses_query_only_authenticated_nts_without_changing_the_host_clock(self):
        runner = FakeRunner()
        wall, monotonic, trusted = self.make_clock(runner)()
        command, options = runner.calls[0]
        self.assertEqual(command[1:4], ["-Q", "-t", str(CHRONY_TIMEOUT_SECONDS)])
        self.assertIn("-f", command)
        self.assertIn("/dev/null", command)
        self.assertEqual(command[-1], f"server {NTS_SERVER} iburst nts")
        self.assertEqual(options["timeout"], PROCESS_TIMEOUT_SECONDS)
        self.assertIs(trusted, True)
        self.assertEqual(wall, 1_798_776_000.0)
        self.assertEqual(monotonic, 100.0)

    def test_rejects_offset_outside_one_second(self):
        runner = FakeRunner(
            "2026-11-01T00:00:00Z System clock wrong by 1.000001 seconds (ignored)"
        )
        with self.assertRaises(ClockSourceError):
            self.make_clock(runner)()
        self.assertEqual(MAX_LOCAL_OFFSET_SECONDS, 1.0)

    def test_rejects_missing_ambiguous_or_nonfinite_offset(self):
        for output in (
            "chronyd version 4.9 starting",
            "System clock wrong by 0.1 seconds (ignored)\nSystem clock wrong by 0.1 seconds (ignored)",
            "System clock wrong by nan seconds (ignored)",
        ):
            with self.subTest(output=output), self.assertRaises(ClockSourceError):
                self.make_clock(FakeRunner(output))()

    def test_rejects_failed_or_timed_out_chrony_process(self):
        with self.assertRaisesRegex(ClockSourceError, "Chrony exited with status 1"):
            self.make_clock(FakeRunner(returncode=1))()
        with self.assertRaisesRegex(ClockSourceError, "15-second timeout"):
            self.make_clock(FakeRunner(error=TimeoutError("private process detail")))()

    def test_reports_startup_failure_without_echoing_os_error(self):
        with self.assertRaisesRegex(ClockSourceError, "check its installation path") as caught:
            self.make_clock(FakeRunner(error=OSError("private path detail")))()
        self.assertNotIn("private path detail", str(caught.exception))

    def test_reuses_only_a_recent_verified_sample(self):
        runner = FakeRunner()
        now = [100.0]
        clock = self.make_clock(runner, monotonic=lambda: now[0])
        clock()
        now[0] += REFRESH_AUTHENTICATED_SAMPLE_AFTER_SECONDS - 1
        clock()
        self.assertEqual(len(runner.calls), 1)
        now[0] += 2
        clock()
        self.assertEqual(len(runner.calls), 2)

    def test_missing_executable_fails_closed(self):
        with patch.dict(os.environ, {"MARKETLENS_CHRONYD_PATH": ""}), \
                patch("marketlens_bot.trusted_clock.shutil.which", return_value=None):
            with self.assertRaises(ClockSourceError):
                ChronyNTSClock()

    def test_uses_explicit_homebrew_executable_path(self):
        path = "/opt/homebrew/opt/chrony/sbin/chronyd"
        with patch.dict(os.environ, {"MARKETLENS_CHRONYD_PATH": path}), \
                patch("marketlens_bot.trusted_clock.shutil.which", return_value=None):
            clock = ChronyNTSClock(runner=FakeRunner())
        self.assertEqual(clock.executable, path)


if __name__ == "__main__":
    unittest.main()
