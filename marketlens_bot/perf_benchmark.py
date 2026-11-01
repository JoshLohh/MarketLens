"""Measure local Mac startup, retrieval, serial-answer latency, and memory."""

from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUERIES = (ROOT / "evaluation" / "filing_corpus" / "local" / "retrieval_runs"
                   / "hybrid-exact-row-hints-2026-10-29-v1" / "queries.json")
FROZEN_QUERY_SHA256 = "9f86b504798eb8f10fc6a28033381634e1fb7d5fb4a6250b6d51e85a87834c1c"
STARTUP_LIMIT_MS = 60_000
RETRIEVAL_P95_LIMIT_MS = 500
QUEUE_P95_LIMIT_MS = 1_500
MEMORY_LIMIT_BYTES = 2 * 1024**3
SAMPLE_INTERVAL_SECONDS = 1.0
WORKER_TIMEOUT_SECONDS = 30 * 60
MEMORY_PATTERN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(B|kB|MB|GB|TB|KiB|MiB|GiB|TiB)\s*/")
MEMORY_UNITS = {"B": 1, "kB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12,
                "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}


class BenchmarkError(RuntimeError):
    """A safe-to-display benchmark setup or execution error."""


def decode_queries(raw: bytes, expected_sha256: str = FROZEN_QUERY_SHA256) -> tuple[list[dict], str]:
    """Validate a query-only projection against its pinned content digest."""
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise BenchmarkError("The saved query-only artifact changed; refusing this benchmark")
    try:
        rows = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise BenchmarkError("The saved query-only artifact is invalid") from None
    if (not isinstance(rows, list) or len(rows) != 21
            or any(type(row) is not dict or set(row) != {"case_id", "ticker", "question"}
                   or not all(isinstance(row.get(key), str) and row[key]
                              for key in ("case_id", "ticker", "question"))
                   or row["ticker"] not in {"NBIS", "IREN", "ASTS"} for row in rows)
            or len({row["case_id"] for row in rows}) != 21):
        raise BenchmarkError("The saved query-only artifact does not contain the fixed 21-question set")
    return rows, digest


def load_queries(path: Path) -> tuple[list[dict], str]:
    """Load only the pinned query projection; benchmark labels are never read."""
    try:
        raw = path.read_bytes()
    except OSError:
        raise BenchmarkError("The saved query-only artifact is unavailable") from None
    return decode_queries(raw)


def nearest_rank_percentile(values: list[float], percentile: float) -> float:
    """Return a reproducible nearest-rank percentile, including p95."""
    if not values or not 0 < percentile <= 100 or any(not math.isfinite(value) for value in values):
        raise ValueError("A percentile needs finite values and a percentile in (0, 100]")
    ordered = sorted(values)
    rank = math.ceil((percentile / 100) * len(ordered))
    return ordered[max(0, rank - 1)]


def parse_memory_bytes(value: str) -> int:
    """Parse the used-memory portion printed by `docker stats`."""
    match = MEMORY_PATTERN.match(value)
    if match is None:
        raise ValueError("Unrecognized Docker memory value")
    return int(float(match.group(1)) * MEMORY_UNITS[match.group(2)])


def memory_limit_bytes(physical_memory_bytes: int) -> int:
    """Apply the reviewed cap: the smaller of 2 GiB or one quarter of RAM."""
    if type(physical_memory_bytes) is not int or physical_memory_bytes <= 0:
        raise ValueError("Physical memory must be a positive integer")
    return min(MEMORY_LIMIT_BYTES, physical_memory_bytes // 4)


def pair_schedule(rows: list[dict], repetitions_per_case: int):
    """Yield FIFO pairs so every case is first position equally often."""
    if len(rows) != 21 or type(repetitions_per_case) is not int or repetitions_per_case < 1:
        raise ValueError("The queue schedule requires 21 cases and positive repetitions")
    for _ in range(repetitions_per_case):
        for index, row in enumerate(rows):
            yield row, rows[(index + 1) % len(rows)]


def synthetic_updates(rows: list[dict], repetitions_per_case: int) -> list[dict]:
    """Build transient private updates for the local Poller/Bot path."""
    updates = []
    for first, second in pair_schedule(rows, repetitions_per_case):
        for row in (first, second):
            update_id = len(updates) + 1
            updates.append({"update_id": update_id,
                            "message": {"message_id": update_id,
                                        "chat": {"id": 1, "type": "private"},
                                        "from": {"id": 1, "is_bot": False},
                                        "text": f"{row['ticker']}: {row['question']}"}})
    return updates


class _NoNetworkTelegram:
    """Return transient updates and discard every sent answer body."""

    def __init__(self, updates: list[dict] | None = None):
        self._updates = deque(updates or [])
        self._next_message_id = 100_000

    def set_updates(self, updates: list[dict]) -> None:
        self._updates = deque(updates)

    def get_updates(self, offset: int | None = None) -> list[dict]:
        del offset
        return [self._updates.popleft()] if self._updates else []

    def send_message(self, chat_id: int, text: str) -> int:
        if type(chat_id) is not int or not isinstance(text, str):
            raise ValueError("Invalid synthetic Telegram message")
        self._next_message_id += 1
        return self._next_message_id


def _run_quiet(command: list[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise BenchmarkError("A required local Docker or macOS command failed") from None


def _physical_memory_bytes() -> int:
    if platform.system() != "Darwin":
        raise BenchmarkError("This performance run is defined for the local Mac")
    result = _run_quiet(["sysctl", "-n", "hw.memsize"])
    try:
        value = int(result.stdout.strip())
    except ValueError:
        value = 0
    if result.returncode != 0 or value <= 0:
        raise BenchmarkError("Could not read physical Mac memory")
    return value


def _docker_container_id() -> str:
    result = _run_quiet(["docker", "compose", "ps", "-q", "weaviate"])
    ident = result.stdout.strip()
    if result.returncode != 0 or not ident:
        raise BenchmarkError("Start the configured Weaviate service before measuring performance")
    return ident


def _restart_weaviate() -> None:
    result = _run_quiet(["docker", "compose", "restart", "--timeout", "10", "weaviate"], timeout=45)
    if result.returncode != 0:
        raise BenchmarkError("Weaviate could not be restarted; no data volume was removed")


def _process_rss_bytes(pid: int) -> int | None:
    result = _run_quiet(["ps", "-o", "rss=", "-p", str(pid)], timeout=3)
    try:
        value_kib = int(result.stdout.strip())
    except ValueError:
        return None
    return value_kib * 1024 if result.returncode == 0 and value_kib >= 0 else None


def _docker_rss_bytes(container_id: str) -> int | None:
    result = _run_quiet(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container_id],
                        timeout=5)
    if result.returncode != 0:
        return None
    try:
        return parse_memory_bytes(result.stdout.strip())
    except ValueError:
        return None


class _MemorySampler:
    """Sample app RSS and Weaviate container memory during one cold start."""

    def __init__(self, container_id: str):
        self.container_id = container_id
        self.app_pid: int | None = None
        self.samples: list[tuple[int | None, int | None]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def set_app_pid(self, pid: int) -> None:
        self.app_pid = pid

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=8)
        self._sample_once()

    def _sample_once(self) -> None:
        try:
            app = 0 if self.app_pid is None else _process_rss_bytes(self.app_pid)
            weaviate = _docker_rss_bytes(self.container_id)
            self.samples.append((app, weaviate))
        except BenchmarkError:
            self.samples.append((None, None))

    def _sample_loop(self) -> None:
        while not self._stop.is_set():
            self._sample_once()
            self._stop.wait(SAMPLE_INTERVAL_SECONDS)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _read_weaviate_meta(port: int) -> dict:
    key = os.environ.get("WEAVIATE_API_KEY", "")
    if not key:
        raise BenchmarkError("WEAVIATE_API_KEY is not available in this shell")
    request = Request(f"http://127.0.0.1:{port}/v1/meta",
                      headers={"Authorization": "Bearer " + key})
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=3) as response:
            payload = response.read(1_000_001)
        if len(payload) > 1_000_000:
            raise ValueError
        meta = json.loads(payload)
    except Exception:
        raise BenchmarkError("Weaviate readiness check failed") from None
    if not isinstance(meta, dict):
        raise BenchmarkError("Weaviate readiness response is invalid")
    return meta


def _wait_for_weaviate(port: int, timeout_seconds: float = 60.0) -> tuple[float, str]:
    start = time.perf_counter()
    deadline = start + timeout_seconds
    while time.perf_counter() < deadline:
        try:
            meta = _read_weaviate_meta(port)
            version = meta.get("version") if isinstance(meta, dict) else None
            if isinstance(version, str) and version:
                return (time.perf_counter() - start) * 1000, version
        except Exception:
            pass
        time.sleep(0.5)
    raise BenchmarkError("Weaviate did not become ready within 60 seconds")


def _check_answer(answer: str, ticker: str, company: str) -> None:
    if (not isinstance(answer, str) or not answer.startswith(f"{company} ({ticker})")
            or "I could not produce a validated answer" in answer):
        raise BenchmarkError("The local checked-answer path failed during the queue measurement")


def _worker(queries_path: Path, repetitions: int, port: int, state_dir: Path | None) -> int:
    def emit(value: dict) -> None:
        print(json.dumps(value, separators=(",", ":")), flush=True)

    stage = "query artifact"
    state = None
    try:
        rows, digest = load_queries(queries_path)
        stage = "application startup"
        started = time.perf_counter()
        from . import answer as answer_module
        from .answer import FilingAnswerer
        # Match product initialization without opening the user's state DB or
        # contacting Telegram.
        from .bot import Bot
        from .pilot import load_pilot
        from .state import State
        from .telegram import Poller, TelegramClient
        from .weaviate_client import WeaviateClient
        from .weaviate_index import WeaviateIndex
        from qa_contract import COMPANIES

        if not isinstance(state_dir, Path):
            raise BenchmarkError("The temporary benchmark state directory is unavailable")
        state = State(state_dir)
        manifest, chunks, local = load_pilot()
        client = WeaviateClient(os.environ.get("WEAVIATE_API_KEY", ""), port=port)
        index = WeaviateIndex(local, client)
        index.verify_collection()
        meta = client._request("/v1/meta")
        version = meta.get("version") if isinstance(meta, dict) else None
        if not isinstance(version, str) or not version:
            raise BenchmarkError("Weaviate version could not be read")
        answerer = FilingAnswerer(manifest, chunks, index, provider=None)
        from .answer import route_question

        def answer_request(question: str, ai_enabled: bool) -> str:
            answer = answerer(question, ai_enabled)
            ticker = route_question(question)
            if ticker is None:
                raise BenchmarkError("The synthetic question did not route to one company")
            _check_answer(answer, ticker, COMPANIES[ticker])
            return answer

        telegram = TelegramClient("marketlens-performance-no-network")
        no_network = _NoNetworkTelegram()
        telegram.get_updates = no_network.get_updates
        telegram.send_message = no_network.send_message
        bot = Bot(state, telegram, chat_id=1, sender_id=1, answer=answer_request)
        poller = Poller(telegram, bot)
        state.record_notice("privacy", 1)
        if not state.confirm_notice("privacy", 1):
            raise BenchmarkError("Temporary local consent could not be initialized")
        emit({"event": "ready", "startup_ms": (time.perf_counter() - started) * 1000,
              "weaviate_version": version, "cache_key": local.cache_key, "query_sha256": digest,
              "poller_initialized": poller is not None})

        stage = "warmup"
        for row in rows:
            answer = answerer(f"{row['ticker']}: {row['question']}", ai_enabled=False)
            _check_answer(answer, row["ticker"], COMPANIES[row["ticker"]])

        original_query_result = answer_module.query_result
        retrieval_calls: list[float] = []

        def timed_query_result(*args, **kwargs):
            retrieval_started = time.perf_counter()
            result = original_query_result(*args, **kwargs)
            retrieval_calls.append((time.perf_counter() - retrieval_started) * 1000)
            return result

        answer_module.query_result = timed_query_result
        retrieval_by_case = {row["case_id"]: [] for row in rows}
        queue_by_case = {row["case_id"]: [] for row in rows}
        no_network.set_updates(synthetic_updates(rows, repetitions))
        try:
            stage = "retrieval and queue samples"
            for pair_number, (first, _second) in enumerate(pair_schedule(rows, repetitions), 1):
                case_before = len(retrieval_calls)
                queue_started = time.perf_counter()
                if not poller.poll_once() or not poller.poll_once():
                    raise BenchmarkError("Synthetic serial request processing did not complete")
                queue_ms = (time.perf_counter() - queue_started) * 1000
                if len(retrieval_calls) != case_before + 2:
                    raise BenchmarkError("A queue request did not complete retrieval and evidence packing")
                retrieval_by_case[first["case_id"]].append(retrieval_calls[case_before])
                queue_by_case[first["case_id"]].append(queue_ms)
                if pair_number % len(rows) == 0:
                    emit({"event": "progress", "completed_repetitions": pair_number // len(rows),
                          "total_repetitions": repetitions})
        finally:
            answer_module.query_result = original_query_result

        emit({"event": "result", "retrieval_by_case_ms": retrieval_by_case,
              "queue_by_case_ms": queue_by_case, "query_sha256": digest,
              "cache_key": local.cache_key, "weaviate_version": version})
        return 0
    except Exception:
        # Never return raw exceptions: they can include question, source, or credential details.
        emit({"event": "error", "stage": stage})
        return 1
    finally:
        if state is not None:
            try:
                state.close()
            except Exception:
                pass


def _worker_command(args, per_start: int, state_dir: Path) -> list[str]:
    return [sys.executable, "-m", "marketlens_bot.perf_benchmark", "--worker",
            "--queries", str(args.queries.resolve()), "--per-start", str(per_start),
            "--port", str(args.port), "--state-dir", str(state_dir)]


def _stdout_reader(stream, lines: queue.Queue) -> None:
    try:
        for line in stream:
            lines.put(line)
    finally:
        lines.put(None)


def _readline_until(lines: queue.Queue, process: subprocess.Popen, deadline: float) -> str:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        process.kill()
        process.wait(timeout=10)
        raise BenchmarkError("The local benchmark worker exceeded its time limit")
    try:
        line = lines.get(timeout=remaining)
    except queue.Empty:
        process.kill()
        process.wait(timeout=10)
        raise BenchmarkError("The local benchmark worker exceeded its time limit") from None
    return line or ""


def _run_trial(args, container_id: str, trial: int, per_start: int) -> dict:
    sampler = _MemorySampler(container_id)
    process = None
    reader = None
    temporary_state = tempfile.TemporaryDirectory(prefix="marketlens-perf-state-")
    sampler_started = False
    try:
        sampler.start()
        sampler_started = True
        restart_started = time.perf_counter()
        _restart_weaviate()
        weaviate_ready_ms, _ = _wait_for_weaviate(args.port)
        weaviate_restart_ms = (time.perf_counter() - restart_started) * 1000
        launch_started = time.perf_counter()
        try:
            process = subprocess.Popen(_worker_command(args, per_start, Path(temporary_state.name)), stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, text=True, bufsize=1)
        except OSError:
            raise BenchmarkError("Could not start the local benchmark worker") from None
        sampler.set_app_pid(process.pid)
        if process.stdout is None:
            process.kill()
            raise BenchmarkError("The local benchmark worker could not report readiness")
        lines: queue.Queue = queue.Queue()
        reader = threading.Thread(target=_stdout_reader, args=(process.stdout, lines), daemon=True)
        reader.start()
        worker_deadline = time.monotonic() + WORKER_TIMEOUT_SECONDS
        first_line = _readline_until(lines, process, min(worker_deadline,
                                                        time.monotonic() + STARTUP_LIMIT_MS / 1000 + 5))
        try:
            first_event = json.loads(first_line)
        except (ValueError, TypeError):
            first_event = {}
        if first_event.get("event") != "ready" or first_event.get("poller_initialized") is not True:
            process.wait(timeout=30)
            raise BenchmarkError("The local benchmark worker failed during startup")
        app_launch_ready_ms = (time.perf_counter() - launch_started) * 1000
        result_event = None
        while True:
            line = _readline_until(lines, process, worker_deadline)
            if not line:
                break
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("event") == "progress":
                print(f"Cold start {trial}: {event['completed_repetitions']}/"
                      f"{event['total_repetitions']} measurement rounds", file=sys.stderr, flush=True)
            elif event.get("event") == "result":
                result_event = event
            elif event.get("event") == "error":
                process.wait(timeout=30)
                raise BenchmarkError(f"The local benchmark worker failed during {event.get('stage', 'a step')}")
        if process.wait(timeout=30) != 0 or result_event is None:
            raise BenchmarkError("The local benchmark worker did not finish successfully")
        if (result_event.get("query_sha256") != FROZEN_QUERY_SHA256
                or first_event.get("query_sha256") != FROZEN_QUERY_SHA256):
            raise BenchmarkError("The worker used an unexpected query artifact")
        return {"trial": trial, "weaviate_restart_ms": weaviate_restart_ms,
                "weaviate_ready_ms": weaviate_ready_ms, "app_launch_ready_ms": app_launch_ready_ms,
                "worker_startup_ms": float(first_event["startup_ms"]),
                "weaviate_version": result_event["weaviate_version"],
                "cache_key": result_event["cache_key"],
                "retrieval_by_case_ms": result_event["retrieval_by_case_ms"],
                "queue_by_case_ms": result_event["queue_by_case_ms"],
                "memory_pairs": sampler.samples}
    finally:
        try:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            if reader is not None:
                reader.join(timeout=2)
            if sampler_started:
                sampler.stop()
        finally:
            temporary_state.cleanup()


def _summarize_by_case(samples: dict[str, list[float]], expected_per_case: int) -> dict:
    if not samples or any(len(values) != expected_per_case for values in samples.values()):
        raise BenchmarkError("The benchmark did not collect the required samples for every case")
    flattened = [value for values in samples.values() for value in values]
    return {"sample_count": len(flattened), "repetitions_per_case": expected_per_case,
            "p95_ms": nearest_rank_percentile(flattened, 95),
            "max_ms": max(flattened),
            "per_case_p95_ms": {case_id: nearest_rank_percentile(values, 95)
                                for case_id, values in samples.items()}}


def _memory_summary(trials: list[dict], physical_bytes: int) -> dict:
    pairs = [pair for trial in trials for pair in trial["memory_pairs"]]
    complete = [(app, weaviate) for app, weaviate in pairs if app is not None and weaviate is not None]
    limit = memory_limit_bytes(physical_bytes)
    complete_per_trial = [sum(app is not None and weaviate is not None
                              for app, weaviate in trial["memory_pairs"]) for trial in trials]
    if not complete or any(count == 0 for count in complete_per_trial):
        return {"status": "UNKNOWN", "sample_count": len(pairs),
                "complete_sample_count": len(complete), "complete_samples_per_trial": complete_per_trial,
                "physical_ram_bytes": physical_bytes, "limit_bytes": limit}
    app_peak = max(app for app, _ in complete)
    weaviate_peak = max(weaviate for _, weaviate in complete)
    combined_peak = max(app + weaviate for app, weaviate in complete)
    return {"status": "PASS" if combined_peak <= limit else "FAIL", "sample_count": len(pairs),
            "complete_sample_count": len(complete), "sampling_interval_seconds": SAMPLE_INTERVAL_SECONDS,
            "complete_samples_per_trial": complete_per_trial,
            "app_peak_rss_bytes": app_peak, "weaviate_peak_bytes": weaviate_peak,
            "sampled_combined_peak_bytes": combined_peak, "physical_ram_bytes": physical_bytes,
            "limit_bytes": limit}


def _make_report(args, rows: list[dict], digest: str, trials: list[dict], physical_bytes: int) -> dict:
    retrieval = {row["case_id"]: [] for row in rows}
    queue = {row["case_id"]: [] for row in rows}
    for trial in trials:
        for case_id in retrieval:
            retrieval[case_id].extend(trial["retrieval_by_case_ms"][case_id])
            queue[case_id].extend(trial["queue_by_case_ms"][case_id])
    retrieval_report = _summarize_by_case(retrieval, args.repetitions)
    queue_report = _summarize_by_case(queue, args.repetitions)
    startup_samples = [trial["app_launch_ready_ms"] for trial in trials]
    startup_status = "PASS" if max(startup_samples) <= STARTUP_LIMIT_MS else "FAIL"
    retrieval_report["status"] = "PASS" if retrieval_report["p95_ms"] <= RETRIEVAL_P95_LIMIT_MS else "FAIL"
    retrieval_report["limit_p95_ms"] = RETRIEVAL_P95_LIMIT_MS
    queue_report["status"] = "PASS" if queue_report["p95_ms"] <= QUEUE_P95_LIMIT_MS else "FAIL"
    queue_report["limit_p95_ms"] = QUEUE_P95_LIMIT_MS
    memory_report = _memory_summary(trials, physical_bytes)
    statuses = [startup_status, retrieval_report["status"], queue_report["status"], memory_report["status"]]
    overall = "FAIL" if "FAIL" in statuses else "UNKNOWN" if "UNKNOWN" in statuses else "PASS"
    versions = sorted({trial["weaviate_version"] for trial in trials})
    return {"benchmark": "marketlens-local-readiness-v1", "overall_status": overall,
            "environment": {"platform": platform.platform(), "python": platform.python_version(),
                            "weaviate_versions": versions},
            "input": {"query_sha256": digest, "query_count": len(rows),
                      "repetitions_per_case": args.repetitions, "cold_starts": args.cold_starts,
                      "cache_keys": sorted({trial["cache_key"] for trial in trials})},
            "gates": {"startup": {"status": startup_status, "sample_count": len(startup_samples),
                                   "launch_to_collection_ready_ms": startup_samples,
                                   "max_ms": max(startup_samples), "limit_ms": STARTUP_LIMIT_MS,
                                   "weaviate_restart_ms": [trial["weaviate_restart_ms"] for trial in trials],
                                   "weaviate_ready_ms": [trial["weaviate_ready_ms"] for trial in trials]},
                      "retrieval_through_pack": retrieval_report,
                      "two_request_serial_queue": queue_report, "combined_memory": memory_report},
            "method": {"cold_start": "Weaviate container restart plus a fresh app worker with temporary SQLite state and Poller wiring; host OS caches are retained.",
                       "startup_scope": "Ends after the local answerer, collection verification, and Poller objects are ready; excludes parity recheck and Telegram polling.",
                       "queue": "Two synthetic updates run serially through Poller and Bot, including SQLite claim/delivery bookkeeping; a local sender stub discards reply text.",
                       "provider_calls": 0, "telegram_calls": 0,
                       "corpus_or_vector_changes": 0,
                       "collection_check": "All cached window metadata and vectors are verified read-only on each start.",
                       "parity": "Retrieval parity is not rerun or claimed by this performance benchmark.",
                       "memory": "One-second sampled app-process RSS plus Weaviate container memory; Docker Desktop VM overhead is excluded."}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m marketlens_bot.perf_benchmark")
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--cold-starts", type=int, default=5)
    parser.add_argument("--repetitions", type=int, default=30,
                        help="total repetitions per query across all five cold starts")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--per-start", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--state-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        if args.per_start < 1:
            print(json.dumps({"event": "error", "stage": "worker arguments"}), flush=True)
            return 2
        return _worker(args.queries, args.per_start, args.port, args.state_dir)
    try:
        rows, digest = load_queries(args.queries)
        if args.cold_starts != 5 or args.repetitions < 30 or args.repetitions % args.cold_starts:
            raise BenchmarkError("Use five cold starts and at least 30 repetitions per case, divisible by five")
        if type(args.port) is not int or not 1 <= args.port <= 65535:
            raise BenchmarkError("The local Weaviate port is invalid")
        physical_bytes = _physical_memory_bytes()
        container_id = _docker_container_id()
        trials = []
        per_start = args.repetitions // args.cold_starts
        for trial_number in range(1, args.cold_starts + 1):
            print(f"Starting cold trial {trial_number}/{args.cold_starts}; Weaviate data volume is preserved.",
                  file=sys.stderr, flush=True)
            trials.append(_run_trial(args, container_id, trial_number, per_start))
        report = _make_report(args, rows, digest, trials, physical_bytes)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["overall_status"] == "PASS" else 1
    except (BenchmarkError, ValueError) as exc:
        message = str(exc) if isinstance(exc, BenchmarkError) else "The benchmark arguments or measurements are invalid"
        print(f"MarketLens performance benchmark stopped: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
