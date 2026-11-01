"""Authenticated, read-only clock checks for the optional provider path.

Chrony runs in query-only mode against an NTS server. It never adjusts the Mac
clock and returns no company question or filing content.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time


NTS_SERVER = "time.cloudflare.com"
CHRONY_TIMEOUT_SECONDS = 15
PROCESS_TIMEOUT_SECONDS = 17
MAX_LOCAL_OFFSET_SECONDS = 1.0
MAX_AUTHENTICATED_SAMPLE_AGE_SECONDS = 60.0
REFRESH_AUTHENTICATED_SAMPLE_AFTER_SECONDS = 45.0

_OFFSET_LINE = re.compile(
    r"^.*System clock wrong by "
    r"(?P<offset>[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?) "
    r"seconds \(ignored\)$"
)


class ClockSourceError(RuntimeError):
    """A trusted time sample could not be established safely."""


class ChronyNTSClock:
    """Supply wall/monotonic samples after a recent authenticated time check.

    The one-shot Chrony `-Q` mode reports the offset without changing the host
    clock. A sample is trusted only when the reported offset is within one
    second. The last successful check is reused briefly; the separate
    ClockGuard detects any wall-clock jump between samples.
    """

    def __init__(self, *, executable: str | None = None, runner=None,
                 wall_clock=None, monotonic_clock=None):
        resolved = (executable or os.environ.get("MARKETLENS_CHRONYD_PATH")
                    or shutil.which("chronyd"))
        if not isinstance(resolved, str) or not Path(resolved).is_absolute():
            raise ClockSourceError("chronyd is unavailable; install it with `brew install chrony`")
        self.executable = resolved
        self.runner = runner or subprocess.run
        self.wall_clock = wall_clock or time.time
        self.monotonic_clock = monotonic_clock or time.monotonic
        self._last_verified_monotonic: float | None = None

    def _measure_offset(self) -> float:
        command = [
            self.executable,
            "-Q",
            "-t", str(CHRONY_TIMEOUT_SECONDS),
            "-f", "/dev/null",
            f"server {NTS_SERVER} iburst nts",
        ]
        try:
            result = self.runner(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=PROCESS_TIMEOUT_SECONDS,
                check=False,
            )
        except (subprocess.TimeoutExpired, TimeoutError):
            raise ClockSourceError("Chrony did not return a time sample before the 15-second timeout") from None
        except OSError:
            raise ClockSourceError("Chrony could not be started; check its installation path") from None

        if getattr(result, "returncode", None) != 0:
            returncode = getattr(result, "returncode", None)
            if type(returncode) is int:
                raise ClockSourceError(f"Chrony exited with status {returncode}")
            raise ClockSourceError("Chrony exited unsuccessfully")
        output = "\n".join((getattr(result, "stdout", "") or "",
                             getattr(result, "stderr", "") or ""))
        offsets = []
        for line in output.splitlines():
            match = _OFFSET_LINE.fullmatch(line.strip())
            if match:
                try:
                    offsets.append(float(match.group("offset")))
                except (TypeError, ValueError):
                    raise ClockSourceError("authenticated time response was invalid") from None
        if len(offsets) != 1 or not math.isfinite(offsets[0]):
            raise ClockSourceError("authenticated time response was invalid")
        if abs(offsets[0]) > MAX_LOCAL_OFFSET_SECONDS:
            raise ClockSourceError("Mac clock differs from authenticated time by more than one second")
        return offsets[0]

    def __call__(self) -> tuple[float, float, bool]:
        monotonic = self.monotonic_clock()
        if (isinstance(monotonic, bool) or not isinstance(monotonic, (int, float))
                or not math.isfinite(monotonic)):
            raise ClockSourceError("monotonic clock is unavailable")
        if (self._last_verified_monotonic is None
                or monotonic < self._last_verified_monotonic
                or monotonic - self._last_verified_monotonic >= REFRESH_AUTHENTICATED_SAMPLE_AFTER_SECONDS):
            self._measure_offset()
            self._last_verified_monotonic = self.monotonic_clock()
            monotonic = self._last_verified_monotonic
            if (isinstance(monotonic, bool) or not isinstance(monotonic, (int, float))
                    or not math.isfinite(monotonic)):
                raise ClockSourceError("monotonic clock is unavailable")
        age = monotonic - self._last_verified_monotonic
        if age < 0 or age > MAX_AUTHENTICATED_SAMPLE_AGE_SECONDS:
            raise ClockSourceError("authenticated time sample is stale")

        wall = self.wall_clock()
        if (isinstance(wall, bool) or not isinstance(wall, (int, float)) or not math.isfinite(wall)):
            raise ClockSourceError("wall clock is unavailable")
        return wall, monotonic, True
