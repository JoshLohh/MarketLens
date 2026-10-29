"""Shared, source-linked answer records for the fixed-corpus Step 3 experiment.

Validity here means well-formed records and genuine evidence locations. Semantic
claim support is a separate source-review task, never inferred from valid JSON.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json


STATUSES = {"answered", "abstained", "error", "unavailable"}
ANSWER_SCHEMA_VERSION = 2
COMPANIES = {"NBIS": "Nebius Group", "IREN": "IREN", "ASTS": "AST SpaceMobile"}
DIMENSIONS = ("correctness", "support", "citations", "company", "period", "basis", "units", "vintage", "abstention")
NUMERIC_METRIC_ALIASES = {
    "revenue": frozenset({"total_revenue", "consolidated_revenue"}),
    "revenue_millions": frozenset({"revenue"}),
    "cash": frozenset({"cash_and_cash_equivalents"}),
    "capex": frozenset({"capital_expenditures", "capital_expenditure"}),
    "net_loss_common": frozenset({"net_loss_attributable_to_common_stockholders"}),
    "q3_share": frozenset({"q3_share_of_nine_month_capex"}),
    "annualization_multiplier": frozenset({
        "annualization_factor", "arr_annualization_multiplier", "arr annualization multiplier",
    }),
}


def same_metric_name(expected: str, observed: str) -> bool:
    """Match exact labels or a small explicit evaluation alias; never fuzzy-match."""
    expected, observed = expected.casefold(), observed.casefold()
    return expected == observed or observed in NUMERIC_METRIC_ALIASES.get(expected, ())


def value_in_unit(value: str, unit: str, target_unit: str) -> Decimal | None:
    """Convert explicit USD scales and the exact % spelling of percent."""
    amount = decimal(value)
    unit = "percent" if unit == "%" else unit
    target_unit = "percent" if target_unit == "%" else target_unit
    if unit == target_unit:
        return amount
    scales = {"USD": Decimal(1), "USD thousand": Decimal(1000), "USD million": Decimal(1_000_000)}
    if unit in scales and target_unit in scales:
        return amount * scales[unit] / scales[target_unit]
    return None


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("Financial values must be decimal strings")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("Invalid decimal") from error
    if not result.is_finite():
        raise ValueError("Non-finite decimal")
    return result


def answer_record(method: str, ticker: str, pack: dict, status: str, reason: str = "") -> dict:
    return {
        "answer_schema_version": ANSWER_SCHEMA_VERSION,
        "method": method, "ticker": ticker, "company": COMPANIES[ticker],
        "status": status, "reason": reason, "evidence_hash": pack["evidence_hash"],
        "claims": [], "numbers": [], "facts": [], "calculations": [],
        "timing_ms": {}, "usage": None,
        "provider_cost_usd": "0" if method == "structured" else None,
        "cost_scope": "Provider requests only; local infrastructure cost is unmeasured",
        "support_status": "UNKNOWN; requires source review",
        "execution_kind": "deterministic" if method == "structured" else "unavailable",
    }


def citation(pack: dict, chunk_id: str, quote: str) -> dict:
    if not isinstance(chunk_id, str) or not chunk_id.strip():
        raise ValueError("Citation chunk ID is missing or invalid")
    passage = next((p for p in pack["passages"] if p["chunk_id"] == chunk_id), None)
    if not passage:
        raise ValueError("Citation chunk ID is not in supplied evidence")
    if not isinstance(quote, str) or not quote.strip():
        raise ValueError("Citation quote is missing or invalid")
    if quote not in passage["text"]:
        raise ValueError("Citation quote is not a verbatim substring of the supplied passage")
    source = pack["sources"][passage["source_id"]]
    return {
        "source_id": passage["source_id"], "chunk_id": chunk_id,
        "block_start": passage["block_start"], "block_end": passage["block_end"],
        "quote": quote, **source,
    }


def validate_answer(answer: dict, pack: dict) -> None:
    schema_version = answer.get("answer_schema_version", 1)
    if type(schema_version) is not int or schema_version not in {1, ANSWER_SCHEMA_VERSION}:
        raise ValueError("Unsupported answer schema version")
    if answer.get("status") not in STATUSES or answer.get("ticker") != pack["ticker"]:
        raise ValueError("Invalid answer identity or status")
    if answer.get("company") != COMPANIES[pack["ticker"]] or answer.get("evidence_hash") != pack["evidence_hash"]:
        raise ValueError("Answer does not refer to this evidence bundle")
    claims = answer.get("claims")
    if not isinstance(answer.get("reason"), str) or len(answer["reason"]) > 1500:
        raise ValueError("Invalid answer reason")
    if not isinstance(claims, list) or len(claims) > 12:
        raise ValueError("Invalid claim count")
    if answer["status"] == "answered" and not claims:
        raise ValueError("An answered result needs claims")
    if answer["status"] == "answered" and answer["reason"]:
        raise ValueError("Answered prose must belong to cited claims")
    if answer["status"] != "answered" and (claims or answer.get("numbers") or answer.get("calculations")):
        raise ValueError("An abstention/error cannot contain an answer")
    if answer["status"] != "answered" and not answer.get("reason"):
        raise ValueError("Missing abstention/error reason")
    for claim in claims:
        if schema_version == 1:
            # Read compatibility for saved Step 3A records whose claim.company
            # field contained the ticker. New answers use schema version 2.
            if claim.get("company") != pack["ticker"]:
                raise ValueError("Claim company mismatch")
        else:
            if claim.get("ticker") != pack["ticker"]:
                raise ValueError("Claim ticker mismatch")
            if claim.get("company_name") != COMPANIES[pack["ticker"]]:
                raise ValueError("Claim company name mismatch")
        if not all(isinstance(claim.get(k), str) and 0 < len(claim[k]) <= 1500 for k in ("text", "period", "accounting_basis")):
            raise ValueError("Claim lacks text/period/accounting basis")
        if not isinstance(claim.get("citations"), list) or not claim["citations"]:
            raise ValueError("Uncited claim")
        for cited in claim["citations"]:
            if len(cited.get("quote", "")) > 900:
                raise ValueError("Citation quote is too long")
            trusted = citation(pack, cited["chunk_id"], cited["quote"])
            if cited != trusted:
                raise ValueError("Citation metadata was changed")
        if not isinstance(claim.get("numbers"), list) or len(claim["numbers"]) > 12:
            raise ValueError("Invalid numeric claim list")
        for number in claim["numbers"]:
            decimal(number["value"])
            if not all(isinstance(number.get(k), str) and 0 < len(number[k]) <= 100 for k in ("name", "unit")):
                raise ValueError("Numeric claim lacks metric or units")
    if answer.get("numbers") != [n for claim in claims for n in claim.get("numbers", [])]:
        raise ValueError("Numeric results must belong to cited claims")


# This provider schema contains no benchmark references. It asks for evidence
# handles; the local adapter reconstructs URLs and filing identity itself.
RAG_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["answered", "abstained"]},
        "reason": {"type": "string"},
        "claims": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "text": {"type": "string"}, "ticker": {"type": "string"},
                "company_name": {"type": "string"},
                "period": {"type": "string"}, "accounting_basis": {"type": "string"},
                "numbers": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {k: {"type": "string"} for k in ("name", "value", "unit")},
                    "required": ["name", "value", "unit"],
                }},
                "citations": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {k: {"type": "string"} for k in ("chunk_id", "quote")},
                    "required": ["chunk_id", "quote"],
                }},
            },
            "required": ["text", "ticker", "company_name", "period", "accounting_basis", "numbers", "citations"],
        }},
    },
    "required": ["status", "reason", "claims"],
}
