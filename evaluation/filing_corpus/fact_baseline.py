"""Source-derived numeric facts and ordinary-code calculations (Step 3A).

The selectors describe filing layout, not reference answers. This module never
reads the Q&A benchmark, uses case IDs, or retrieves additional hidden evidence.
"""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import re
import time

from qa_contract import COMPANIES, answer_record, citation, decimal, validate_answer
from rag_experiment import verify_evidence


SPECS = Path(__file__).with_name("fact_specs.json")
NUMBER = re.compile(r"\(\s*[+-]?\d[\d,]*(?:\.\d+)?\s*\)|[+-]?\d[\d,]*(?:\.\d+)?|[—-](?=\s|$)")


def number_cells(text: str) -> list[Decimal]:
    cells = NUMBER.findall(text.replace("$", ""))
    values = []
    for cell in cells:
        cell = cell.strip()
        if cell in {"—", "-"}:
            values.append(Decimal(0))  # explicit table dash; never a missing row
        elif cell.startswith("("):
            values.append(-decimal(cell[1:-1].strip().replace(",", "")))
        else:
            values.append(decimal(cell.replace(",", "")))
    return values


def readable(text: str) -> str:
    return "\n".join(" ".join(line.replace("|", " ").split()) for line in text.splitlines())


def fact_row_lines(fact: dict, cited: dict) -> list[str]:
    """Locate a fact's value-bearing lines inside one exact citation quote."""
    matched = []
    for row in fact.get("row_evidence", []):
        if cited["source_id"] != row["source_id"] or cited["chunk_id"] not in row["chunk_ids"]:
            continue
        prefix, suffix = row["prefix"].casefold(), row["suffix"].casefold()
        if not (prefix or suffix):
            continue
        for text in readable(cited["quote"]).splitlines():
            line = text.casefold()
            source_row = row.get("text", "")
            if source_row:
                if source_row.casefold() in line:
                    matched.append(source_row)
                    continue
                # A short quote may stop after the requested value. Match its
                # prefix against this exact extracted row, never a later row
                # elsewhere in the same flattened PDF paragraph.
                if not prefix:
                    continue  # Number-first rows need their trailing label.
                for occurrence in re.finditer(re.escape(prefix), line):
                    fragment = text[occurrence.start():]
                    common = 0
                    for a, b in zip(fragment.casefold(), source_row.casefold()):
                        if a != b:
                            break
                        common += 1
                    if common < len(fragment) and common and fragment[common - 1].isdigit() and fragment[common] in "0123456789,.":
                        continue  # A partial numeric token is not a value match.
                    quoted = fragment[:common]
                    if decimal(fact["raw_value"]) in number_cells(quoted[len(prefix):]):
                        matched.append(quoted)
                continue
            start = line.find(prefix) if prefix else 0
            if start < 0:
                continue
            start += len(prefix)
            end = line.find(suffix, start) if suffix else len(line)
            if end >= start and any(c.isdigit() for c in line[start:end]) and text not in matched:
                matched.append(text)
    return matched


def fact_row_citations(fact: dict, citations: list[dict]) -> list[dict]:
    """Find quoted value rows/sentences, rather than unrelated co-located facts."""
    matched = [c for c in citations if fact_row_lines(fact, c)]
    original = {c["chunk_id"]: c for c in fact["citations"]}
    for row in fact.get("row_evidence", []):
        for left_id, right_id in zip(row["chunk_ids"], row["chunk_ids"][1:]):
            left, right = original[left_id], original[right_id]
            if (left["source_id"] != right["source_id"]
                    or int(right_id.rsplit(":", 1)[1]) != int(left_id.rsplit(":", 1)[1]) + 1
                    or left["block_end"] != right["block_start"]):
                continue
            for a in citations:
                if a["chunk_id"] != left_id or a["source_id"] != row["source_id"] or not left["quote"].endswith(a["quote"]):
                    continue
                for b in citations:
                    if b["chunk_id"] != right_id or b["source_id"] != row["source_id"] or not right["quote"].startswith(b["quote"]):
                        continue
                    joined = dict(a, quote=a["quote"] + " " + b["quote"])
                    if fact_row_lines(fact, joined):
                        matched.extend((a, b))
    return list({(c["chunk_id"], c["quote"]): c for c in matched}.values())


def extract_facts(pack: dict, specification: dict | None = None) -> tuple[dict, list[str]]:
    verify_evidence(pack)
    specification = specification or json.loads(SPECS.read_text())
    if pack["corpus_version"] != specification["corpus_version"]:
        raise ValueError("Fact selectors belong to a different fixed corpus")
    passages = {p["chunk_id"]: p for p in pack["passages"]}
    facts, rejected = {}, []
    for spec in specification["facts"]:
        if spec["ticker"] != pack["ticker"]:
            continue
        matches = []
        for selector in spec["alternatives"]:
            ids = selector["chunk_ids"]
            if not all(i in passages for i in ids):
                continue
            sid = selector["source_id"]
            source = pack["sources"][sid]
            if source["ticker"] != spec["ticker"] or any(passages[i]["source_id"] != sid for i in ids):
                raise ValueError("Selector source/company mismatch")
            # A split prose block is joined with a space; distinct HTML blocks
            # keep their newline. Only explicitly required, adjacent chunks join.
            text = ""
            last = None
            for i in ids:
                p = passages[i]
                separator = " " if last and last["block_end"] == p["block_start"] else "\n"
                text += separator + p["text"]
                last = p
            text = readable(text)
            if not all(re.search(g, text, re.I | re.M) for g in selector["guards"]):
                continue
            rows = list(re.finditer(selector["row_pattern"], text, re.I | re.M))
            if len(rows) != 1:
                if rows:
                    rejected.append(f"{spec['id']}: ambiguous rows")
                continue
            row = rows[0]
            cells = number_cells(row.groupdict().get("values") or row.group(1))
            if len(cells) <= selector["column"]:
                rejected.append(f"{spec['id']}: missing numeric column")
                continue
            raw = cells[selector["column"]]
            converted = (abs(raw) if spec.get("absolute") else raw) * decimal(spec["factor"])
            cites = [citation(pack, i, passages[i]["text"]) for i in ids]
            value_group = "values" if row.groupdict().get("values") is not None else 1
            # Keep the label surrounding the captured values. A selector may
            # include earlier table headers; only the value row's prefix counts.
            prefix = text[row.start():row.start(value_group)].rsplit("\n", 1)[-1].strip()
            suffix = text[row.end(value_group):row.end()].split("\n", 1)[0].strip()
            local_start = text.rfind("\n", row.start(), row.start(value_group)) + 1
            local_start = max(local_start, row.start())
            local_end = text.find("\n", row.end(value_group), row.end())
            if local_end < 0:
                local_end = row.end()
            row_text = text[local_start:local_end].strip()
            matches.append({
                "fact_id": spec["id"], "company": spec["ticker"], "metric": spec["metric"],
                "period": spec["period"], "accounting_basis": spec["accounting_basis"],
                "value": str(converted), "unit": spec["unit"], "currency": spec["currency"],
                "raw_value": str(raw), "raw_unit": spec["raw_unit"],
                "scale_factor": spec["factor"], "expense_magnitude": bool(spec.get("absolute")),
                "row_evidence": [{"source_id": sid, "chunk_ids": ids, "prefix": prefix, "suffix": suffix, "text": row_text}],
                "citations": cites,
            })
        if matches:
            if len({(m["value"], m["period"], m["unit"], m["accounting_basis"]) for m in matches}) != 1:
                rejected.append(f"{spec['id']}: conflicting supplied values")
                continue
            fact = matches[0]
            fact["citations"] = list({c["chunk_id"]: c for m in matches for c in m["citations"]}.values())
            fact["row_evidence"] = [row for match in matches for row in match["row_evidence"]]
            facts[spec["id"]] = fact
    return facts, rejected


def calculate(operation: str, first: dict, second: dict, name: str, *, vintage_comparison: bool = False, same_filing: bool = False) -> dict:
    for key in ("company", "metric", "accounting_basis", "unit", "currency"):
        if first[key] != second[key]:
            raise ValueError(f"Incompatible calculation {key}")
    a, b = decimal(first["value"]), decimal(second["value"])
    if same_filing and not ({c["accession"] for c in first["citations"]} & {c["accession"] for c in second["citations"]}):
        raise ValueError("Calculation requires comparatives from the same filing")
    if first["period"] == second["period"] and not vintage_comparison:
        raise ValueError("A period comparison needs different periods")
    if vintage_comparison and first["period"] != second["period"]:
        raise ValueError("A vintage comparison needs the same reporting period")
    if operation == "difference":
        value, unit, formula = a - b, first["unit"], "first - second"
    elif operation in {"growth_percent", "share_percent"}:
        if b == 0:
            raise ValueError("Zero denominator")
        if operation == "share_percent":
            start_a, end_a = first["period"].split("/")
            start_b, end_b = second["period"].split("/")
            if not start_b <= start_a <= end_a == end_b:
                raise ValueError("Share requires a contained reporting period")
        value = (a - b) / b * 100 if operation == "growth_percent" else a / b * 100
        unit = "percent"
        formula = "(first - second) / second * 100" if operation == "growth_percent" else "first / second * 100"
    else:
        raise ValueError("Unsupported calculation")
    period = (f"{first['period']} within {second['period']}" if operation == "share_percent"
              else first["period"] if vintage_comparison
              else f"{second['period']} to {first['period']}")
    return {
        "output": name, "operation": operation, "formula": formula,
        "inputs": [{"fact_id": f["fact_id"], "value": f["value"], "unit": f["unit"], "period": f["period"]} for f in (first, second)],
        "value": str(value), "unit": unit, "period": period,
        "accounting_basis": first["accounting_basis"],
    }


def route_question(ticker: str, question: str) -> dict:
    """Conservative metric/period routing; no prompt or case-ID answer table."""
    q = question.casefold()
    for alias, company in (("nbis", "NBIS"), ("nebius", "NBIS"), ("iren", "IREN"), ("asts", "ASTS"), ("ast spacemobile", "ASTS")):
        if company != ticker and re.search(r"\b" + alias + r"\b", q):
            return {"reason": "Question company conflicts with the selected ticker; multi-company questions are unsupported."}
    if re.search(r"\b(?:eur|aud|gbp|cad|euros|pounds)\b|\bin (?:usd )?thousands\b", q):
        return {"reason": "The requested currency/unit is outside the structured prototype's supported output units."}
    if "ifrs" in q:
        return {"reason": "The structured prototype cannot supply an IFRS figure for this request or mix IFRS with US GAAP comparatives."}
    if re.search(r"\b20(?:[01]\d|2[0-3]|2[6-9]|[3-9]\d)\b", q):
        return {"reason": "The requested period is outside the supported fact selectors."}
    if any(term in q for term in ("per named", "per customer", "vodafone")) and any(term in q for term in ("profit", "margin")):
        return {"reason": "Cannot establish this customer calculation from the supplied evidence. Matched customer revenue and expense inputs are required."}
    if ticker == "NBIS":
        if "revenue" in q and re.search(r"(?:fy\s*2024|full.year\s*2024)", q) and "arr" not in q and not re.search(r"(?:fy\s*2025|full.year\s*2025)", q):
            return {"facts": ["nbis-revenue-fy24"]}
        if "cash" in q and "equivalents" in q:
            if "restricted" in q and not any(t in q for t in ("excluding", "exclude", "separate")):
                return {"reason": "Combined/restricted cash calculations are unsupported; cash and cash equivalents is a separate metric."}
            sep, dec = "september 30" in q, "december 31" in q
            if sep and dec and any(t in q for t in ("change", "difference")):
                return {"facts": ["nbis-cash-dec24", "nbis-cash-sep24"], "calculations": [("difference", "cash_change")]}
            if sep != dec:
                return {"facts": ["nbis-cash-sep24" if sep else "nbis-cash-dec24"]}
        if "capital expenditure" in q or "capex" in q:
            if ("q3" in q or "three months ended september 30" in q) and "2024" in q:
                if "nine" in q and any(t in q for t in ("percentage", "percent", "share")):
                    return {"facts": ["nbis-capex-q3", "nbis-capex-nine-months"], "calculations": [("share_percent", "q3_share")]}
                if "nine" not in q:
                    return {"facts": ["nbis-capex-q3"]}
    if ticker == "IREN":
        if "revenue" in q and "2025" in q and ("10-k" in q or "gaap" in q):
            if "2024" in q and "growth" in q and "ifrs" not in q:
                return {"facts": ["iren-revenue-fy25", "iren-revenue-fy24-gaap"], "calculations": [("growth_percent", "revenue_growth")], "same_filing": True}
            if "2024" not in q:
                return {"facts": ["iren-revenue-fy25"]}
        if "october" in q:
            if "2025" in q or ("november 6" in q and "december 6" in q and not any(t in q for t in ("change", "decrease"))):
                return {"reason": "Unsupported or ambiguous October period/vintage request."}
            if "electricity cost" in q and "bitcoin" in q:
                if "november 6" in q and "december 6" in q and any(t in q for t in ("decrease", "change")):
                    return {"facts": ["iren-cost-oct-original", "iren-cost-oct-revised"], "calculations": [("difference", "cost_decrease"), ("growth_percent", "cost_change_percent")], "vintage_comparison": True, "reverse_growth": True}
                if "original" in q or ("november 6" in q and "december 6" not in q):
                    return {"facts": ["iren-cost-oct-original"]}
                if "revised" in q or "december 6" in q:
                    return {"facts": ["iren-cost-oct-revised"]}
            if "original" in q or "november 6" in q:
                if "bitcoin" in q and "mined" in q:
                    return {"facts": ["iren-bitcoin-oct-original"]}
                if "installed" in q and ("capacity" in q or "hashrate" in q):
                    return {"facts": ["iren-installed-oct-original", "iren-average-oct-original"]}
    if ticker == "ASTS":
        if "cash" in q and "equivalents" in q and "december 31, 2024" in q:
            if "september" in q or "change" in q or ("restricted" in q and not any(t in q for t in ("excluding", "exclude", "separate"))):
                return {"reason": "Unsupported combined cash or period comparison request."}
            return {"facts": ["asts-cash-dec24"]}
        if "q3 2024" in q:
            if "net loss" in q and "common stockholders" in q:
                return {"facts": ["asts-loss-common-q3"]}
            if "revenue" in q:
                return {"facts": ["asts-revenue-q3"]}
            if "adjusted operating expenses" in q and "q2" in q and any(t in q for t in ("change", "increase", "growth")):
                return {"facts": ["asts-adjusted-opex-q3", "asts-adjusted-opex-q2"], "calculations": [("difference", "opex_increase"), ("growth_percent", "opex_growth")]}
    return {"reason": "Unsupported or ambiguous question for the structured prototype. It supports selected financial metrics/calculations; this is not evidence that the filing lacks the answer."}


def structured_answer(pack: dict) -> dict:
    start = time.perf_counter()
    request = route_question(pack["ticker"], pack["question"])
    result = answer_record("structured", pack["ticker"], pack, "abstained", request.get("reason", "Required row/header/vintage evidence is missing or conflicting in this bundle."))
    if not pack["passages"]:
        result["reason"] = "No retrieved evidence; no answer produced."
    elif request.get("facts"):
        catalog, rejected = extract_facts(pack)
        result["extraction_rejections"] = rejected
        if all(i in catalog for i in request["facts"]):
            selected = [catalog[i] for i in request["facts"]]
            result.update(status="answered", reason="", facts=selected)
            if request.get("calculations"):
                for operation, name in request["calculations"]:
                    inputs = list(reversed(selected)) if request.get("reverse_growth") and operation == "growth_percent" else selected
                    calculated = calculate(operation, inputs[0], inputs[1], name, vintage_comparison=request.get("vintage_comparison", False), same_filing=request.get("same_filing", False))
                    result["calculations"].append(calculated)
                    text = f"{name}: {calculated['value']} {calculated['unit']}; {calculated['formula']} using {inputs[0]['value']} and {inputs[1]['value']} {inputs[0]['unit']}."
                    if name == "cash_change":
                        text += " This is a cash-balance difference."
                    elif name == "cost_decrease":
                        text += " This is the positive decrease magnitude from the original to the revised expense."
                    elif name == "cost_change_percent":
                        text += " This is the signed percentage change from original to revised; the negative sign means a decrease."
                    result["claims"].append({"text": text, "ticker": pack["ticker"], "company_name": COMPANIES[pack["ticker"]], "period": calculated["period"], "accounting_basis": calculated["accounting_basis"], "numbers": [{"name": name, "value": calculated["value"], "unit": calculated["unit"]}], "citations": list({c["chunk_id"]: c for f in selected for c in f["citations"]}.values())})
            else:
                for fact in selected:
                    result["claims"].append({"text": f"{fact['metric']}: {fact['value']} {fact['unit']} for {fact['period']} ({fact['accounting_basis']}).", "ticker": pack["ticker"], "company_name": COMPANIES[pack["ticker"]], "period": fact["period"], "accounting_basis": fact["accounting_basis"], "numbers": [{"name": fact["metric"], "value": fact["value"], "unit": fact["unit"]}], "citations": fact["citations"]})
            for fact in selected:
                if fact["raw_unit"] == "USD thousand" and fact["unit"] == "USD million":
                    result["calculations"].append({"output": fact["metric"] + "_millions", "operation": "divide", "formula": "raw value / 1000", "inputs": [{"fact_id": fact["fact_id"], "value": fact["raw_value"], "unit": fact["raw_unit"]}, {"constant": "scale_divisor", "value": "1000", "unit": "dimensionless"}], "value": fact["value"], "unit": fact["unit"], "period": fact["period"]})
            result["numbers"] = [n for c in result["claims"] for n in c["numbers"]]
            for i, claim in enumerate(result["claims"]):
                claim["claim_id"] = f"claim-{i + 1}"
            validate_answer(result, pack)
    result["timing_ms"]["answer_ms"] = round((time.perf_counter() - start) * 1000, 3)
    return result
