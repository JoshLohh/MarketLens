"""One opt-in Responses API experiment transport, not a product provider choice.

No automatic retries, model defaults, uploads, tools, or redirected requests.
The explicit config records a human review of pricing and count-endpoint access;
that declaration is not an independent account entitlement/billing check.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import os
import re
import ssl
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from qa_contract import canonical, decimal, fingerprint
from rag_experiment import MAX_REQUEST_BYTES


MAX_OUTPUT_TOKENS = 2400
MAX_PROVIDER_REQUESTS_PER_ARM = 42  # 21 answers plus at most one quote retry each.


class ProviderError(ValueError):
    def __init__(self, message: str, *, diagnostics: dict | None = None):
        super().__init__(message)
        # Diagnostics contain only bounded, allowlisted metadata. Never attach
        # response bodies, prompts, evidence text, exception messages or keys.
        self.diagnostics = diagnostics or {}


def _safe_provider_token(value: object, *, maximum: int = 80) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        return None
    return value if re.fullmatch(r"[A-Za-z0-9_.-]+", value) else None


def strict_json(text: str | bytes) -> object:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ProviderError("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ProviderError("Non-finite JSON")))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ProviderError("Provider redirect rejected")


class ResponsesProvider:
    execution_kind = "live"

    def __init__(self, config: dict, *, network_enabled: bool = False, key: str | None = None, today: date | None = None):
        allowed = {"network_authorized", "model", "input_usd_per_million", "output_usd_per_million", "count_usd_per_request", "max_spend_usd", "pricing_checked_on", "pricing_source_url", "count_endpoint_verified", "reviewed_by", "max_input_tokens", "max_output_tokens", "max_generation_requests", "timeout_seconds"}
        if not isinstance(config, dict) or set(config) - allowed:
            raise ProviderError("Unknown configuration fields; credentials belong only in the environment")
        self.config = dict(config)
        if network_enabled is not True or config.get("network_authorized") is not True:
            raise ProviderError("Live access requires explicit authorization")
        self.model = config.get("model")
        if not isinstance(self.model, str) or not self.model.strip() or len(self.model) > 100:
            raise ProviderError("An explicit model is required")
        self.key = key if key is not None else os.environ.get("OPENAI_API_KEY")
        if not self.key:
            raise ProviderError("OPENAI_API_KEY is unavailable")
        try:
            self.input_rate = decimal(config["input_usd_per_million"])
            self.output_rate = decimal(config["output_usd_per_million"])
            self.count_fee = decimal(config["count_usd_per_request"])
            self.cap = decimal(config["max_spend_usd"])
            checked = date.fromisoformat(config["pricing_checked_on"])
            source = urlparse(config["pricing_source_url"])
        except (KeyError, TypeError, ValueError) as error:
            raise ProviderError("Missing/invalid dated rate configuration") from error
        today = today or datetime.now(timezone(timedelta(hours=8))).date()
        if not 0 <= (today - checked).days <= 31 or source.scheme != "https" or source.hostname not in {"developers.openai.com", "platform.openai.com"} or any((source.username, source.password, source.query, source.fragment)):
            raise ProviderError("Rates need a recent official-source review")
        if min(self.input_rate, self.output_rate) <= 0 or self.count_fee < 0 or not 0 < self.cap <= 1:
            raise ProviderError("Invalid rates or spend ceiling (maximum USD 1)")
        if config.get("count_endpoint_verified") is not True or not config.get("reviewed_by"):
            raise ProviderError("Count-endpoint eligibility and billing require a recorded review")
        self.input_limit = self._bounded(config.get("max_input_tokens", 8000), 8000)
        # Keep the original default; callers can explicitly reserve more room
        # for reasoning plus the visible cited answer, under the same USD cap.
        self.output_limit = self._bounded(config.get("max_output_tokens", 1200), MAX_OUTPUT_TOKENS)
        self.call_limit = self._bounded(config.get("max_generation_requests", MAX_PROVIDER_REQUESTS_PER_ARM), MAX_PROVIDER_REQUESTS_PER_ARM)
        if self.call_limit != MAX_PROVIDER_REQUESTS_PER_ARM:
            raise ProviderError("Paired retry evaluation must reserve 42 generation requests per arm")
        self.count_limit = MAX_PROVIDER_REQUESTS_PER_ARM
        self.timeout = self._bounded(config.get("timeout_seconds", 45), 45)
        self.count_requests = self.generation_requests = 0
        self.reserved = Decimal(0)
        self.attempts: list[dict] = []
        self.halted = False

    @staticmethod
    def _bounded(value: object, maximum: int) -> int:
        if type(value) is not int or not 1 <= value <= maximum:
            raise ProviderError("Request/token/timeout limit is invalid")
        return value

    def _post(self, path: str, payload: dict) -> dict:
        endpoint = "input_tokens" if path == "/responses/input_tokens" else "responses"
        if path not in {"/responses", "/responses/input_tokens"}:
            raise ProviderError("Unapproved endpoint", diagnostics={"endpoint": "unknown", "failure_code": "unapproved_endpoint"})
        body = canonical(payload)
        if len(body) > MAX_REQUEST_BYTES:
            raise ProviderError("Provider request exceeds 32000 bytes", diagnostics={"endpoint": endpoint, "failure_code": "request_too_large"})
        request = Request("https://api.openai.com/v1" + path, data=body, headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self.key,
        }, method="POST")
        opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
        try:
            with opener.open(request, timeout=self.timeout) as response:
                if response.geturl() != request.full_url:
                    raise ProviderError("Provider redirect rejected")
                raw = response.read(256_001)
                if len(raw) > 256_000:
                    raise ProviderError("Provider response too large", diagnostics={"endpoint": endpoint, "failure_code": "response_too_large"})
                try:
                    result = strict_json(raw)
                except Exception:
                    raise ProviderError("Invalid provider response", diagnostics={"endpoint": endpoint, "failure_code": "invalid_response_json"}) from None
                if not isinstance(result, dict):
                    raise ProviderError("Invalid provider response", diagnostics={"endpoint": endpoint, "failure_code": "invalid_response_shape"})
                return result
        except HTTPError as error:
            details = {"endpoint": endpoint, "http_status": int(error.code)}
            request_id = _safe_provider_token(error.headers.get("x-request-id") if error.headers else None, maximum=128)
            if request_id:
                details["request_id"] = request_id
            # Parse only provider error type/code from a bounded body. Discard
            # message, prompt, evidence, and every other response field.
            try:
                raw_error = error.read(16_385)
                if len(raw_error) <= 16_384:
                    envelope = strict_json(raw_error)
                    provider_error = envelope.get("error") if isinstance(envelope, dict) else None
                    if isinstance(provider_error, dict):
                        for field in ("type", "code"):
                            value = _safe_provider_token(provider_error.get(field))
                            if value:
                                details["provider_error_" + field] = value
            except Exception:
                pass
            raise ProviderError("Provider returned an HTTP error", diagnostics=details) from None
        except ProviderError as error:
            if not error.diagnostics:
                error.diagnostics = {"endpoint": endpoint, "failure_code": "provider_response_rejected"}
            raise
        except Exception as error:
            # Exception messages may contain URLs or response text. Keep only a
            # coarse class label from a fixed allowlist.
            known_types = {"URLError", "TimeoutError", "SSLError", "ConnectionError", "gaierror", "OSError"}
            exception_type = type(error).__name__
            details = {
                "endpoint": endpoint,
                "failure_code": "transport_failure",
                "transport_exception": exception_type if exception_type in known_types else "Other",
            }
            raise ProviderError("Provider transport failed", diagnostics=details) from None

    def generate(self, request_input: dict) -> dict:
        if self.halted or self.generation_requests >= self.call_limit or self.count_requests >= self.count_limit:
            raise ProviderError("Provider request limit reached or session halted")
        if set(request_input) != {"instructions", "input", "text"}:
            raise ProviderError("Unexpected generation-input fields")
        count_body = {"model": self.model, **request_input}
        generation_body = {**count_body, "max_output_tokens": self.output_limit, "store": False, "background": False}
        if max(len(canonical(count_body)), len(canonical(generation_body))) > MAX_REQUEST_BYTES:
            raise ProviderError("Request byte budget exceeded")
        # Count calls can also have a reviewed fee. Reserve that BEFORE sending.
        if self.reserved + self.count_fee > self.cap:
            raise ProviderError("Count fee exceeds remaining budget")
        self.reserved += self.count_fee
        self.count_requests += 1
        attempt = {"ordinal": len(self.attempts) + 1, "request_input_hash": fingerprint(count_body), "status": "error", "usage": None, "estimated_cost_usd": None}
        self.attempts.append(attempt)
        start = time.perf_counter()
        phase = "token_count_request"
        try:
            counted = self._post("/responses/input_tokens", count_body)
            count_ms = (time.perf_counter() - start) * 1000
            phase = "token_count_validation"
            attempt["phase"] = phase
            tokens = counted.get("input_tokens")
            if counted.get("object") != "response.input_tokens" or type(tokens) is not int or not 0 < tokens <= self.input_limit:
                details = {"failure_code": "invalid_or_oversized_token_count", "input_token_limit": self.input_limit}
                if type(tokens) is int:
                    details["input_tokens"] = tokens
                raise ProviderError("Invalid or oversized provider token count", diagnostics=details)
            reservation = (Decimal(tokens) * self.input_rate + Decimal(self.output_limit) * self.output_rate) / 1_000_000
            if self.reserved + reservation > self.cap:
                raise ProviderError("Generation would exceed reserved spend ceiling", diagnostics={"failure_code": "spend_limit_before_generation"})
            self.reserved += reservation
            self.generation_requests += 1
            generation_start = time.perf_counter()
            phase = "answer_generation_request"
            attempt["phase"] = phase
            response = self._post("/responses", generation_body)
            generation_ms = (time.perf_counter() - generation_start) * 1000
            phase = "answer_response_validation"
            attempt["phase"] = phase
            attempt["timing_ms"] = {"token_count_ms": round(count_ms, 3), "generation_ms": round(generation_ms, 3)}
            usage = response.get("usage")
            if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ("input_tokens", "output_tokens")):
                raise ProviderError("Provider usage is unknown", diagnostics={"failure_code": "usage_missing_or_invalid"})
            if usage["input_tokens"] > tokens or usage["output_tokens"] > self.output_limit:
                raise ProviderError("Provider usage exceeded reserved token bounds", diagnostics={"failure_code": "usage_exceeded_reserved_bounds"})
            cost = self.count_fee + (Decimal(usage["input_tokens"]) * self.input_rate + Decimal(usage["output_tokens"]) * self.output_rate) / 1_000_000
            measured = {"usage": usage, "estimated_cost_usd": str(cost), "timing_ms": {"token_count_ms": round(count_ms, 3), "generation_ms": round(generation_ms, 3)}}
            attempt.update(measured)
            if response.get("status") != "completed":
                details = {"failure_code": "response_not_completed"}
                incomplete = response.get("incomplete_details")
                reason = incomplete.get("reason") if isinstance(incomplete, dict) else None
                if reason in {"max_output_tokens", "content_filter"}:
                    details["incomplete_reason"] = reason
                if reason == "max_output_tokens":
                    details["output_token_limit"] = self.output_limit
                raise ProviderError("Incomplete or failed response", diagnostics=details)
            messages = [o for o in response.get("output", []) if o.get("type") == "message"]
            content = [c for o in messages for c in o.get("content", [])]
            if any(c.get("type") == "refusal" for c in content):
                raise ProviderError("Provider refusal", diagnostics={"failure_code": "provider_refusal"})
            texts = [c.get("text") for c in content if c.get("type") == "output_text"]
            if len(texts) != 1 or not isinstance(texts[0], str):
                raise ProviderError("Missing structured response", diagnostics={"failure_code": "structured_output_missing"})
            payload = strict_json(texts[0])
            if not isinstance(payload, dict):
                raise ProviderError("Structured answer is not an object", diagnostics={"failure_code": "structured_output_invalid"})
            attempt["status"] = "completed"
            attempt["phase"] = "completed"
            return {**measured, "answer": payload}
        except ProviderError as error:
            # All reservations survive failure; no retries or next requests with
            # uncertain charges. The run manifest still records the attempt.
            self.halted = True
            attempt["phase"] = phase
            failure = {"phase": phase, **error.diagnostics}
            if not error.diagnostics:
                failure["failure_code"] = "provider_request_failed"
            attempt["failure"] = failure
            raise ProviderError("Provider request failed or exceeded a bound; session halted", diagnostics=failure) from None
        except Exception:
            self.halted = True
            attempt["phase"] = phase
            attempt["failure"] = {"phase": phase, "failure_code": "unexpected_internal_error"}
            raise ProviderError("Provider request failed or exceeded a bound; session halted", diagnostics=attempt["failure"]) from None

    def run_state(self) -> dict:
        return {
            "model": self.model, "config_hash": fingerprint(self.config), "rate_configuration": {k: self.config.get(k) for k in (
                "input_usd_per_million", "output_usd_per_million", "count_usd_per_request", "pricing_checked_on", "pricing_source_url", "reviewed_by", "count_endpoint_verified",
            )}, "limits": {"max_spend_usd": str(self.cap), "max_input_tokens": self.input_limit, "max_output_tokens": self.output_limit, "max_generation_requests": self.call_limit, "max_count_requests": self.count_limit, "timeout_seconds": self.timeout},
            "count_requests": self.count_requests, "generation_requests": self.generation_requests,
            "reserved_usd": str(self.reserved), "halted": self.halted, "attempts": self.attempts,
            "cost_status": "Rate-based estimate; not an invoice or independent entitlement check",
        }
