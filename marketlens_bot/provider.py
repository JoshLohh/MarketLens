"""Bounded, optional OpenAI answer adapter with durable local accounting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import math
import os
from importlib.metadata import PackageNotFoundError, version
import sys
import time
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
import ssl
from uuid import uuid4
from zoneinfo import ZoneInfo

from .trusted_clock import ChronyNTSClock, ClockSourceError


MODEL = "gpt-6-luna"
PRICING_CHECKED_ON = date(2026, 11, 2)
MAX_OUTPUT_TOKENS = 25000
MAX_REQUEST_BYTES = 32000
# The request byte limit applies to the complete text-only Responses payload.
# Add a deliberately large allowance for provider-side message/schema framing.
# This is a local reservation bound, not a provider-published tokenizer guarantee.
INPUT_FORMATTING_ALLOWANCE_TOKENS = 8000
MAX_RESERVED_INPUT_TOKENS = MAX_REQUEST_BYTES + INPUT_FORMATTING_ALLOWANCE_TOKENS
MAX_RESPONSE_BYTES = 256000
# Standard short-context rates reviewed 2026-11-02. Input is charged
# conservatively at base input plus the highest published cache-write rate.
INPUT_BOUND_NANODOLLARS_PER_TOKEN = 350
OUTPUT_NANODOLLARS_PER_TOKEN = 500
ROLLOVER_GUARD_SECONDS = 300
DISPATCH_WINDOW_SECONDS = 2
CLOCK_DRIFT_TOLERANCE_SECONDS = 1
LIVE_TRANSPORT_ENABLED = True


class ProviderError(ValueError):
    """A safe failure without a question, response body, or credential."""


@dataclass(frozen=True)
class Pricing:
    checked_on: date
    source: str = "https://developers.openai.com/api/docs/pricing"
    input_bound_per_token: int = INPUT_BOUND_NANODOLLARS_PER_TOKEN
    output_bound_per_token: int = OUTPUT_NANODOLLARS_PER_TOKEN

    def validate(self, today: date) -> None:
        if (type(self.checked_on) is not date or not 0 <= (today - self.checked_on).days <= 31
                or self.source != "https://developers.openai.com/api/docs/pricing"
                or type(self.input_bound_per_token) is not int
                or not INPUT_BOUND_NANODOLLARS_PER_TOKEN <= self.input_bound_per_token <= 10_000_000
                or type(self.output_bound_per_token) is not int
                or not OUTPUT_NANODOLLARS_PER_TOKEN <= self.output_bound_per_token <= 10_000_000
                ):
            raise ProviderError("Recent, conservative model pricing is required")


@dataclass(frozen=True)
class DispatchTicket:
    month: str
    local_date: date
    wall: float
    monotonic: float
    deadline: float


class ClockGuard:
    """Check injected trusted wall/monotonic samples around each dispatch.

    The injected clock must return (Unix seconds, monotonic seconds, trusted).
    `trusted_clock.ChronyNTSClock` is the runtime source when configured.
    """

    def __init__(self, clock):
        self.clock = clock
        self._last = None

    def _sample(self, *, guard_rollover=True):
        wall, monotonic, trusted = self.clock()
        if (trusted is not True or isinstance(wall, bool) or isinstance(monotonic, bool)
                or not isinstance(wall, (int, float)) or not isinstance(monotonic, (int, float))
                or not math.isfinite(wall) or not math.isfinite(monotonic)):
            raise ProviderError("Clock is untrusted")
        if self._last is not None:
            old_wall, old_monotonic = self._last
            elapsed = monotonic - old_monotonic
            if elapsed < 0 or abs((wall - old_wall) - elapsed) > CLOCK_DRIFT_TOLERANCE_SECONDS:
                raise ProviderError("Clock changed unexpectedly")
        self._last = (wall, monotonic)
        local = datetime.fromtimestamp(wall, timezone.utc).astimezone(ZoneInfo("Asia/Singapore"))
        start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if local.month == 12:
            following = start.replace(year=local.year + 1, month=1)
        else:
            following = start.replace(month=local.month + 1)
        if guard_rollover and ((local - start).total_seconds() < ROLLOVER_GUARD_SECONDS
                               or (following - local).total_seconds() <= ROLLOVER_GUARD_SECONDS):
            raise ProviderError("Clock is within the monthly rollover guard")
        return wall, monotonic, local

    def ticket(self) -> DispatchTicket:
        wall, monotonic, local = self._sample()
        return DispatchTicket(local.strftime("%Y-%m"), local.date(), wall, monotonic,
                              monotonic + DISPATCH_WINDOW_SECONDS)

    def check(self, ticket: DispatchTicket) -> None:
        wall, monotonic, local = self._sample()
        if (monotonic < ticket.monotonic or monotonic > ticket.deadline
                or local.strftime("%Y-%m") != ticket.month
                or abs((wall - ticket.wall) - (monotonic - ticket.monotonic)) > CLOCK_DRIFT_TOLERANCE_SECONDS):
            raise ProviderError("Dispatch deadline or month changed")

    def check_completion(self, ticket: DispatchTicket) -> None:
        """Confirm a finished request remained in the reserved month."""
        wall, monotonic, local = self._sample(guard_rollover=False)
        if (monotonic < ticket.monotonic or local.strftime("%Y-%m") != ticket.month
                or abs((wall - ticket.wall) - (monotonic - ticket.monotonic)) > CLOCK_DRIFT_TOLERANCE_SECONDS):
            raise ProviderError("Provider dispatch month is uncertain")


def _strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ProviderError("Duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise ProviderError("Invalid JSON constant")

    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)


def _encode(payload: dict) -> bytes:
    try:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ProviderError("Invalid provider request") from None
    if len(raw) > MAX_REQUEST_BYTES:
        raise ProviderError("Provider request exceeds the byte limit")
    return raw


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ProviderError("Provider redirect rejected")


def _openai_https_handler():
    """Use verified macOS system trust without changing global TLS settings."""
    if sys.platform != "darwin":
        return HTTPSHandler(context=ssl.create_default_context())
    try:
        if version("truststore") != "0.10.5":
            raise ProviderError("OpenAI TLS requires truststore==0.10.5")
        import truststore
    except (ImportError, PackageNotFoundError):
        raise ProviderError("OpenAI TLS requires truststore==0.10.5") from None
    return HTTPSHandler(context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))


class OpenAITransport:
    """Send to the fixed HTTPS endpoint when the source lock permits it."""

    def __init__(self, key: str):
        if not isinstance(key, str) or not key or any(char.isspace() for char in key):
            raise ProviderError("OpenAI credential is unavailable")
        self._key = key
        self._https_handler = _openai_https_handler()

    def post(self, path: str, payload: dict, ticket: DispatchTicket, guard: ClockGuard) -> dict:
        if not LIVE_TRANSPORT_ENABLED:
            raise ProviderError("Live OpenAI transport is disabled pending readiness checks")
        if path != "/responses":
            raise ProviderError("Provider endpoint is not allowed")
        raw = _encode(payload)
        request = Request("https://api.openai.com/v1" + path, data=raw,
                          headers={"Content-Type": "application/json", "Authorization": "Bearer " + self._key},
                          method="POST")
        try:
            opener = build_opener(_NoRedirect(), self._https_handler)
            guard.check(ticket)
            with opener.open(request, timeout=45) as response:
                if response.geturl() != request.full_url:
                    raise ProviderError("Provider redirect rejected")
                data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise ProviderError("Provider response exceeds the byte limit")
            result = _strict_json(data)
            if not isinstance(result, dict):
                raise ProviderError("Invalid provider response")
            return result
        except Exception:
            # The request may have been transmitted. The caller keeps its reservation.
            raise ProviderError("Provider transport failed or its outcome is uncertain") from None


class ResponsesProvider:
    """Factory for independent, durably budgeted answers."""

    def __init__(self, state, transport, clock_guard: ClockGuard, pricing: Pricing):
        self.state, self.transport, self.clock_guard, self.pricing = state, transport, clock_guard, pricing

    def for_answer(self):
        return _AnswerProvider(self.state, self.transport, self.clock_guard, self.pricing)


def build_runtime_provider(state, *, environment=None):
    """Build an optional provider; missing setup leaves local answers usable."""
    if not LIVE_TRANSPORT_ENABLED:
        return None
    source = os.environ if environment is None else environment
    key = source.get("OPENAI_API_KEY", "")
    if not isinstance(key, str) or not key or any(char.isspace() for char in key):
        return None
    try:
        clock = ChronyNTSClock()
        transport = OpenAITransport(key)
    except (ClockSourceError, ProviderError, OSError):
        return None
    return ResponsesProvider(
        state,
        transport,
        ClockGuard(clock),
        Pricing(checked_on=PRICING_CHECKED_ON),
    )


class _AnswerProvider:
    execution_kind = "live"

    def __init__(self, state, transport, clock_guard, pricing):
        self.state, self.transport, self.clock_guard, self.pricing = state, transport, clock_guard, pricing
        self.answer_id = uuid4().hex
        self.attempts = []
        self.halted = False
        self.transmission_entered = False
        self.safe_failure_category = None

    def _dispatch(self, path: str, payload: dict, bound: int):
        attempt_id = uuid4().hex
        ticket = self.state.reserve_for_dispatch(attempt_id, self.answer_id, bound, self.clock_guard)
        try:
            self.pricing.validate(ticket.local_date)
            self.clock_guard.check(ticket)
        except Exception:
            self.state.release_undispatched(attempt_id)
            raise ProviderError("Provider dispatch was blocked before transmission") from None
        try:
            self.transmission_entered = True
            response = self.transport.post(path, payload, ticket, self.clock_guard)
            self.clock_guard.check_completion(ticket)
        except Exception:
            # Once transport is entered, transmission or its month may be uncertain.
            raise ProviderError("Provider request outcome or month is uncertain") from None
        return attempt_id, response

    def generate(self, request_input: dict) -> dict:
        if self.halted or len(self.attempts) >= 2:
            raise ProviderError("Provider is halted or the answer attempt limit was reached")
        self.safe_failure_category = "request"
        record = {"usage": None, "estimated_cost_usd": None, "timing_ms": {}}
        self.attempts.append(record)
        try:
            if not isinstance(request_input, dict) or set(request_input) != {"instructions", "input", "text"}:
                raise ProviderError("Invalid generation input")
            generation_body = {"model": MODEL, **request_input, "max_output_tokens": MAX_OUTPUT_TOKENS,
                               "store": False, "background": False, "service_tier": "default"}
            _encode(generation_body)
            # Reserve for the full request byte limit plus a large formatting
            # allowance before sending any question or evidence. At default
            # rates this is $0.0265 per attempt. A retry is allowed only if
            # the first settled cost leaves room under the $0.03 answer cap.
            bound = (MAX_RESERVED_INPUT_TOKENS * self.pricing.input_bound_per_token
                     + MAX_OUTPUT_TOKENS * self.pricing.output_bound_per_token)
            generation_start = time.perf_counter()
            generation_id, response = self._dispatch("/responses", generation_body, bound)
            generation_ms = (time.perf_counter() - generation_start) * 1000
            self.safe_failure_category = "response"
            if not isinstance(response, dict) or response.get("service_tier") != "default":
                raise ProviderError("Provider service tier is unknown or unexpected")
            usage = response.get("usage") if isinstance(response, dict) else None
            if (not isinstance(usage, dict) or type(usage.get("input_tokens")) is not int
                    or type(usage.get("output_tokens")) is not int
                    or not 0 < usage["input_tokens"] <= MAX_RESERVED_INPUT_TOKENS
                    or not 0 < usage["output_tokens"] <= MAX_OUTPUT_TOKENS):
                raise ProviderError("Provider usage is unknown or exceeds its reservation")
            charge = (usage["input_tokens"] * self.pricing.input_bound_per_token
                      + usage["output_tokens"] * self.pricing.output_bound_per_token)
            self.state.settle(generation_id, charge)
            record.update(usage={"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"]},
                          estimated_cost_usd=f"{charge // 1_000_000_000}.{charge % 1_000_000_000:09d}",
                          timing_ms={"generation_ms": round(generation_ms, 3)})
            if response.get("status") != "completed" or not isinstance(response.get("output"), list):
                details = response.get("incomplete_details")
                if response.get("status") == "incomplete" and isinstance(details, dict):
                    if details.get("reason") == "max_output_tokens":
                        self.safe_failure_category = "output_limit"
                    else:
                        self.safe_failure_category = "incomplete"
                raise ProviderError("Provider did not complete the answer")
            contents = [item for output in response["output"] if isinstance(output, dict)
                        and output.get("type") == "message" and isinstance(output.get("content"), list)
                        for item in output["content"] if isinstance(item, dict)]
            if any(item.get("type") == "refusal" for item in contents):
                raise ProviderError("Provider refused the answer")
            texts = [item.get("text") for item in contents if item.get("type") == "output_text"]
            if len(texts) != 1 or not isinstance(texts[0], str):
                raise ProviderError("Provider answer is missing")
            answer = _strict_json(texts[0])
            if not isinstance(answer, dict):
                raise ProviderError("Provider answer has an invalid shape")
            self.safe_failure_category = None
            return {"answer": answer, **record}
        except Exception:
            self.halted = True
            record["failure"] = {"failure_code": "provider_request_failed"}
            raise ProviderError("Provider request or validation failed") from None
