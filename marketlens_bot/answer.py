"""Route questions and render source-linked answers from bounded evidence."""

from __future__ import annotations

from pathlib import Path
import re
import sys


# The evaluated code currently uses sibling imports rather than a package.
_EVALUATION = Path(__file__).resolve().parent.parent / "evaluation" / "filing_corpus"
if str(_EVALUATION) not in sys.path:
    sys.path.insert(0, str(_EVALUATION))

from fact_baseline import structured_answer  # noqa: E402
from qa_contract import COMPANIES, citation, validate_answer  # noqa: E402
from rag_experiment import rag_answer, verify_evidence  # noqa: E402
from retrieval_compare import query_result  # noqa: E402


ALIASES = {
    "NBIS": (r"\bnbis\b", r"\bnebius\b"),
    "IREN": (r"\biren\b", r"\biris energy\b"),
    "ASTS": (r"\basts\b", r"\bast spacemobile\b", r"\bast space ?mobile\b"),
}


def route_question(question: str) -> str | None:
    """Resolve exactly one covered company; do not retain ambiguous text."""
    if not isinstance(question, str):
        return None
    found = [ticker for ticker, aliases in ALIASES.items()
             if any(re.search(pattern, question, re.IGNORECASE) for pattern in aliases)]
    return found[0] if len(found) == 1 else None


def coverage_note(manifest: dict, ticker: str) -> str:
    dates = []
    for document in manifest["documents"]:
        if document.get("ticker") != ticker:
            continue
        sec_metadata = document.get("sec_metadata")
        dates.extend(date for date in (
            document.get("issuer_publication_date"),
            document.get("sec_filing_date"),
            sec_metadata.get("filing_date") if isinstance(sec_metadata, dict) else None,
        ) if isinstance(date, str) and date)
    return (f"Fixed pilot: selected {ticker} filings through {max(dates)}; coverage is not complete."
            if dates else f"Fixed pilot: selected {ticker} filings; coverage date is unavailable.")


def render_checked_answer(answer: dict, pack: dict) -> str:
    """Validate every claim and then show its source metadata and link."""
    validate_answer(answer, pack)
    if answer["status"] == "abstained":
        return "The supplied evidence was insufficient for a checked answer."
    if answer["status"] != "answered":
        return "I could not produce a validated answer from the available evidence."
    lines = []
    for claim in answer["claims"]:
        if lines:
            lines.append("")
        lines.append(claim["text"])
        lines.append("")
        shown_sources = set()
        for item in claim["citations"]:
            source_line = (f"Source: {item['source_id']} · {item.get('form') or 'filing'} · "
                           f"period {item.get('reporting_period') or 'unspecified'} · "
                           f"{item.get('sec_url') or 'source URL unavailable'}")
            if source_line not in shown_sources:
                lines.append(source_line)
                shown_sources.add(source_line)
    return "\n".join(lines)


def render_excerpts(pack: dict) -> str:
    """Display bounded exact excerpts as leads, not as synthesized answers."""
    lines = ["Generated explanations are unavailable. These excerpts may help; they may not answer the question:"]
    for passage in pack["passages"][:2]:
        quote = passage["text"][:800].strip()
        if not quote:
            continue
        source = citation(pack, passage["chunk_id"], quote)
        lines.append(f"\n“{quote}”\nSource: {source['source_id']} · {source.get('form') or 'filing'} · "
                     f"period {source.get('reporting_period') or 'unspecified'} · "
                     f"{source.get('sec_url') or 'source URL unavailable'}")
    if len(lines) == 1:
        return "No suitable filing evidence was found; I cannot answer this question."
    return "\n".join(lines)


def render_local_answer(pack: dict) -> str:
    """Return a checked local calculation or clearly labeled cited excerpts."""
    answer = structured_answer(pack)
    if answer["status"] == "answered":
        return "Generated explanations are unavailable. This answer was checked locally.\n" + render_checked_answer(answer, pack)
    return render_excerpts(pack)


def safe_failure_explanation(answer: dict, provider) -> str:
    """Describe a failure using fixed messages, never provider or question text."""
    diagnostic = answer.get("failure_diagnostic")
    stage = diagnostic.get("stage") if isinstance(diagnostic, dict) else None
    if stage == "provider_unavailable":
        return "The OpenAI provider was unavailable for this answer."
    provider_messages = {
        "request": "The OpenAI request could not be completed.",
        "response": "The OpenAI response could not be processed.",
        "output_limit": "The OpenAI response reached its output limit before completing.",
        "incomplete": "The OpenAI response did not finish.",
    }
    category = getattr(provider, "safe_failure_category", None)
    if isinstance(category, str) and category in provider_messages:
        return provider_messages[category]
    if isinstance(stage, str) and stage in {"model_payload_shape", "claim_shape", "citation_reconstruction",
                                           "answer_integrity", "financial_consistency"}:
        return "The generated answer did not pass the evidence or format checks."
    return "The cause could not be determined."


class FilingAnswerer:
    """Connect the frozen retrieval and validation contracts to a bot request."""

    def __init__(self, manifest: dict, chunks: list[dict], index, provider=None):
        self.manifest, self.chunks, self.index, self.provider = manifest, chunks, index, provider

    def __call__(self, question: str, ai_enabled: bool) -> str:
        ticker = route_question(question)
        if ticker is None:
            return "Please resend the full question with exactly one ticker: NBIS, IREN, or ASTS."
        if len(question.encode("utf-8")) > 2000:
            return "Please shorten the question and resend it with the ticker."
        try:
            result = query_result(self.index, self.manifest, self.chunks,
                                  {"case_id": "bot-request", "ticker": ticker, "question": question},
                                  weights=(0.75, 0.25), lexical_floor=3, floor_policy="fixed",
                                  packing_policy="context-anchored-tail-v1", lexical_tail_depth=15)
            pack = result["pack"]
            verify_evidence(pack)
            if ai_enabled and self.provider is not None:
                factory = getattr(self.provider, "for_answer", None)
                provider = factory() if callable(factory) else self.provider
                answer = rag_answer(pack, provider)
                if answer.get("status") in {"error", "unavailable"}:
                    charge_note = ("A request may have been charged."
                                   if answer.get("status") != "unavailable" and getattr(provider, "transmission_entered", True)
                                   else "No OpenAI request was sent.")
                    detail = safe_failure_explanation(answer, provider)
                    body = f"OpenAI explanation unavailable. {charge_note}\n{detail}\n" + render_local_answer(pack)
                else:
                    body = render_checked_answer(answer, pack)
            else:
                body = render_local_answer(pack)
            return f"{COMPANIES[ticker]} ({ticker})\n{body}\n\n{coverage_note(self.manifest, ticker)}"
        except Exception:
            # Exceptions may contain question or provider text; do not expose or log them.
            return (f"{COMPANIES[ticker]} ({ticker}): I could not produce a validated answer from the available evidence.\n\n"
                    f"{coverage_note(self.manifest, ticker)}")
