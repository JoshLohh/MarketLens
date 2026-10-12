"""Build or inspect SEC filing sources for the Q&A experiment.

This is evaluation tooling. It does not select the product's runtime or database.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


HERE = Path(__file__).resolve().parent
SEED = HERE / "sources.json"
LOCAL = HERE / "local"
EXTRACTION_VERSION = "html-blocks-v1"
MAX_DOCUMENT_BYTES = 6_000_000
MAX_TOTAL_BYTES = 40_000_000
MIN_REQUEST_INTERVAL_SECONDS = 0.5  # well below the SEC's 10 requests/second ceiling
BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr"}
SKIP_TAGS = {"head", "script", "style", "noscript", "ix:hidden"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
SOURCE_ID_PATTERN = re.compile(r"[A-Z0-9]+(?:-[A-Z0-9]+)+\Z")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


class FilingText(HTMLParser):
    """Keep visible filing text and table cells; skip iXBRL hidden data."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.current: list[str] = []
        self.skip_stack: list[str] = []
        self.table_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        hidden = ("hidden" in attributes or attributes.get("aria-hidden") == "true" or
                  bool(re.search(r"display\s*:\s*none", attributes.get("style") or "", re.I)))
        if self.skip_stack:
            if tag not in VOID_TAGS:
                self.skip_stack.append(tag)
            return
        if tag in SKIP_TAGS or hidden:
            self.flush()
            if tag not in VOID_TAGS:
                self.skip_stack.append(tag)
            return
        if tag == "table":
            self.flush()
            self.table_depth += 1
        elif tag == "tr":
            self.flush()
        elif self.table_depth and tag in BLOCK_TAGS:
            self.current.append(" ")
        elif tag in BLOCK_TAGS:
            self.flush()
        elif tag in {"td", "th"}:
            self.current.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if self.skip_stack:
            if tag == self.skip_stack[-1]:
                self.skip_stack.pop()
            return
        if tag == "table":
            self.flush()
            self.table_depth -= 1
        elif tag == "tr":
            self.flush()
        elif self.table_depth and tag in BLOCK_TAGS:
            self.current.append(" ")
        elif tag in BLOCK_TAGS:
            self.flush()
        elif tag in {"td", "th"}:
            self.current.append(" | ")

    def handle_data(self, data: str) -> None:
        if not self.skip_stack:
            self.current.append(data)

    def flush(self) -> None:
        value = " ".join("".join(self.current).split())
        if len(value) >= 8 or re.search(r"\d", value):
            self.blocks.append(value)
        self.current.clear()


def extract_blocks(raw: bytes) -> list[str]:
    parser = FilingText()
    parser.feed(raw.decode("utf-8", errors="replace"))
    parser.flush()
    return parser.blocks


def make_chunks(blocks: list[str], source: dict) -> list[dict]:
    chunks: list[dict] = []
    pending: list[str] = []
    first = 0

    def emit(last: int) -> None:
        nonlocal pending, first
        if not pending:
            return
        value = "\n".join(pending)
        chunks.append({
            "chunk_id": f"{source['source_id']}:{len(chunks):05d}",
            "source_id": source["source_id"],
            "ticker": source["ticker"],
            "block_start": first,
            "block_end": last,
            "text": value,
            "text_sha256": sha256(value.encode("utf-8")),
        })
        pending = []

    for number, block in enumerate(blocks):
        remaining = block
        while remaining:
            if len(remaining) <= 900:
                part, remaining = remaining, ""
            else:
                boundary = remaining.rfind(" ", 0, 901)
                boundary = boundary if boundary >= 450 else 900
                part, remaining = remaining[:boundary].strip(), remaining[boundary:].strip()
            if pending and sum(map(len, pending)) + len(pending) + len(part) > 900:
                emit(number if first == number else number - 1)
            if not pending:
                first = number
            pending.append(part)
            if remaining:
                emit(number)
    emit(len(blocks) - 1)
    return chunks


def sec_document_url(source: dict) -> str:
    cik = source["cik"]
    accession = source["accession"]
    document = source["document"]
    if not (cik.isdigit() and re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession)):
        raise ValueError(f"Invalid SEC identity for {source['source_id']}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.htm", document):
        raise ValueError(f"Invalid SEC document name for {source['source_id']}")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{document}"


class SecClient:
    def __init__(self, user_agent: str) -> None:
        if "@" not in user_agent or len(user_agent) < 15:
            raise ValueError("SEC_USER_AGENT must identify the project and a real contact email")
        self.user_agent = user_agent
        self.last_request = 0.0

    def get(self, url: str, max_bytes: int) -> bytes:
        if not (url.startswith("https://www.sec.gov/Archives/edgar/data/") or
                url.startswith("https://data.sec.gov/submissions/")):
            raise ValueError(f"Non-SEC URL rejected: {url}")
        delay = MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        request = Request(url, headers={"User-Agent": self.user_agent, "Accept": "text/html,application/json"})
        self.last_request = time.monotonic()
        try:
            with urlopen(request, timeout=30) as response:
                if response.geturl() != url:
                    raise ValueError(f"Redirected SEC request rejected: {url}")
                payload = response.read(max_bytes + 1)
                if len(payload) > max_bytes:
                    raise ValueError(f"Response too large: {url}")
                return payload
        except (HTTPError, URLError) as error:
            raise RuntimeError(f"SEC request failed for {url}: {error}") from error


def filings_for_cik(client: SecClient, cik: str, needed: set[str]) -> dict[str, dict]:
    url = f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"
    data = json.loads(client.get(url, 5_000_000))
    if int(data["cik"]) != int(cik):
        raise ValueError(f"Submissions CIK mismatch for {cik}")
    filings = data["filings"]
    result: dict[str, dict] = {}
    def add_group(group: dict) -> None:
        for index, accession in enumerate(group["accessionNumber"]):
            if accession in needed:
                result[accession] = {key: values[index] for key, values in group.items() if isinstance(values, list) and len(values) > index}

    add_group(filings["recent"])
    for older in filings.get("files", []):
        if needed <= result.keys():
            break
        name = older["name"]
        if not re.fullmatch(r"CIK\d{10}-submissions-[A-Za-z0-9-]+\.json", name):
            raise ValueError(f"Unexpected SEC older-submissions filename: {name}")
        older_url = f"https://data.sec.gov/submissions/{name}"
        add_group(json.loads(client.get(older_url, 5_000_000)))
    if needed - result.keys():
        raise ValueError(f"SEC submissions missing accessions for {cik}: {sorted(needed - result.keys())}")
    return result


def validate_spec(seed: dict) -> None:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*-v[0-9]+", seed["corpus_version"]):
        raise ValueError("Corpus version must be a safe, explicit v-number")
    documents = seed["documents"]
    if len(documents) != 12 or len({item["source_id"] for item in documents}) != 12:
        raise ValueError("Pilot must contain exactly twelve unique source IDs")
    benchmark_ids = {item["source_id"] for item in documents if item.get("benchmark_url")}
    if len(benchmark_ids) != 10:
        raise ValueError("Pilot must map exactly ten benchmark source IDs")
    for item in documents:
        if not SOURCE_ID_PATTERN.fullmatch(item["source_id"]):
            raise ValueError(f"Unsafe source ID: {item['source_id']}")
        if item["ticker"] not in {"NBIS", "IREN", "ASTS"}:
            raise ValueError(f"Unexpected ticker: {item['source_id']}")
        sec_document_url(item)
        for hash_key in ("audit_raw_sha256", "expected_raw_sha256"):
            raw_hash = item.get(hash_key)
            if raw_hash is not None and (
                not isinstance(raw_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", raw_hash)
            ):
                raise ValueError(f"Invalid {hash_key}: {item['source_id']}")
        metadata = item.get("sec_metadata", {})
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", metadata.get("acceptance", "")):
            raise ValueError(f"Missing SEC acceptance: {item['source_id']}")
        for key in ("filing_date", "report_date"):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", metadata.get(key, "")):
                raise ValueError(f"Missing SEC {key}: {item['source_id']}")
        if not item.get("validation_markers"):
            raise ValueError(f"Missing source markers: {item['source_id']}")
    by_id = {item["source_id"]: item for item in documents}
    amended = by_id["IREN-FY24-AMENDED"]
    if amended.get("supersedes") != "IREN-FY24-ORIGINAL":
        raise ValueError("IREN annual amendment link is missing")
    if by_id["IREN-FY24-ORIGINAL"]["form"] != "20-F" or amended["form"] != "20-F/A":
        raise ValueError("IREN annual filing versions are mislabeled")


def build(seed_path: Path = SEED, local_root: Path = LOCAL) -> Path:
    seed = json.loads(seed_path.read_text())
    validate_spec(seed)
    user_agent = os.environ.get("SEC_USER_AGENT", "")
    client = SecClient(user_agent)
    version = seed["corpus_version"]
    release = local_root / "releases" / version
    if release.exists():
        raise FileExistsError(f"Immutable release already exists: {release}")
    staging = local_root / "staging" / f"{version}-{uuid4().hex}"
    staging.mkdir(parents=True)
    try:
        (staging / "raw").mkdir()
        ciks = sorted({item["cik"] for item in seed["documents"]})
        metadata_by_cik = {cik: filings_for_cik(
            client, cik, {item["accession"] for item in seed["documents"] if item["cik"] == cik}
        ) for cik in ciks}
        records: list[dict] = []
        total_bytes = 0
        chunk_count = 0
        with (staging / "chunks.jsonl").open("w") as chunk_file:
            for item in seed["documents"]:
                source_id = item["source_id"]
                filing = metadata_by_cik[item["cik"]].get(item["accession"])
                if not filing or filing["form"] != item["form"] or not filing.get("acceptanceDateTime"):
                    raise ValueError(f"SEC accession/form/acceptance mismatch: {source_id}")
                expected_metadata = item["sec_metadata"]
                if any(filing.get(actual) != expected_metadata[expected] for actual, expected in (
                    ("acceptanceDateTime", "acceptance"), ("filingDate", "filing_date"),
                    ("reportDate", "report_date"),
                )):
                    raise ValueError(f"SEC timing metadata changed: {source_id}")
                url = sec_document_url(item)
                raw = client.get(url, MAX_DOCUMENT_BYTES)
                total_bytes += len(raw)
                if total_bytes > MAX_TOTAL_BYTES or b"<html" not in raw[:5000].lower():
                    raise ValueError(f"Invalid or oversized filing HTML: {source_id}")
                raw_hash = sha256(raw)
                # A current pin can supersede an older audit observation while retaining it in provenance.
                expected_raw_hash = item.get("expected_raw_sha256", item.get("audit_raw_sha256"))
                if expected_raw_hash and raw_hash != expected_raw_hash:
                    raise ValueError(
                        f"SEC raw hash mismatch for {source_id}: expected {expected_raw_hash}; "
                        f"received {raw_hash} ({len(raw)} bytes)"
                    )
                blocks = extract_blocks(raw)
                if len(blocks) < 5:
                    raise ValueError(f"Incomplete extraction: {source_id}")
                full_text = "\n".join(blocks)
                compact_text = normalized(full_text)
                missing = [marker for marker in item["validation_markers"] if normalized(marker) not in compact_text]
                if missing:
                    raise ValueError(f"Source markers missing for {source_id}: {missing}")
                (staging / "raw" / f"{source_id}.htm").write_bytes(raw)
                chunks = make_chunks(blocks, item)
                for chunk in chunks:
                    chunk_file.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                chunk_count += len(chunks)
                records.append({
                    **{key: value for key, value in item.items() if key != "validation_markers"},
                    "sec_url": url,
                    "acceptance_datetime_raw": filing["acceptanceDateTime"],
                    "sec_filing_date": filing.get("filingDate"),
                    "sec_report_date": filing.get("reportDate"),
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "raw_sha256": raw_hash,
                    "raw_bytes": len(raw),
                    "extracted_sha256": sha256(full_text.encode("utf-8")),
                    "blocks": len(blocks),
                    "chunks": len(chunks),
                })
        chunk_bytes = (staging / "chunks.jsonl").read_bytes()
        manifest = {
            "schema_version": seed["schema_version"],
            "corpus_version": version,
            "extraction_version": EXTRACTION_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "seed_sha256": sha256(seed_path.read_bytes()),
            "rights_policy": seed["rights_policy"],
            "availability_rule": "SEC acceptance is an earliest possible bound, not observed public availability",
            "raw_total_bytes": total_bytes,
            "chunk_count": chunk_count,
            "chunks_sha256": sha256(chunk_bytes),
            "documents": records,
        }
        manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        (staging / "manifest.json").write_bytes(manifest_bytes)
        (staging / "manifest.sha256").write_text(sha256(manifest_bytes) + "\n")
        release.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, release)
        return release
    except BaseException:
        shutil.rmtree(staging)
        raise


def audit_sources(source_ids: list[str] | None = None, seed_path: Path = SEED) -> list[dict]:
    """Check SEC identity, text markers, sizes and hashes without saving filing bodies."""
    seed = json.loads(seed_path.read_text())
    validate_spec(seed)
    if source_ids is None:
        selected = [
            item for item in seed["documents"]
            if re.search(r"-S[12]\Z", item["source_id"])
        ]
        if len(selected) != 6 or {item["ticker"] for item in selected} != {"NBIS", "IREN", "ASTS"}:
            raise ValueError("Expected S1 and S2 sources for NBIS, IREN, and ASTS")
    else:
        requested = set(source_ids)
        by_id = {item["source_id"]: item for item in seed["documents"]}
        missing_ids = requested - by_id.keys()
        if missing_ids:
            raise ValueError(f"Unknown source IDs: {sorted(missing_ids)}")
        if not requested:
            raise ValueError("At least one source ID is required")
        selected = [item for item in seed["documents"] if item["source_id"] in requested]

    client = SecClient(os.environ.get("SEC_USER_AGENT", ""))
    metadata_by_cik: dict[str, dict[str, dict]] = {}
    metadata_errors: dict[str, str] = {}
    for cik in sorted({item["cik"] for item in selected}):
        try:
            needed = {item["accession"] for item in selected if item["cik"] == cik}
            metadata_by_cik[cik] = filings_for_cik(client, cik, needed)
        except (OSError, RuntimeError, ValueError) as error:
            metadata_errors[cik] = str(error)

    reports: list[dict] = []
    for item in selected:
        expected_hash = item.get("expected_raw_sha256", item.get("audit_raw_sha256"))
        report = {
            "source_id": item["source_id"],
            "ticker": item["ticker"],
            "sec_url": sec_document_url(item),
            "audit_raw_sha256": item.get("audit_raw_sha256"),
            "expected_raw_sha256": expected_hash,
            "current_raw_sha256": None,
            "current_raw_bytes": None,
            "hash_match": None,
            "identity_timing_match": False,
            "content_markers_match": False,
        }
        try:
            if item["cik"] in metadata_errors:
                raise RuntimeError(metadata_errors[item["cik"]])
            filing = metadata_by_cik[item["cik"]].get(item["accession"])
            expected_metadata = item["sec_metadata"]
            if not filing or filing.get("form") != item["form"] or any(
                filing.get(actual) != expected_metadata[expected]
                for actual, expected in (
                    ("acceptanceDateTime", "acceptance"),
                    ("filingDate", "filing_date"),
                    ("reportDate", "report_date"),
                )
            ):
                raise ValueError("SEC accession, form, or timing metadata mismatch")
            report["identity_timing_match"] = True

            raw = client.get(report["sec_url"], MAX_DOCUMENT_BYTES)
            if b"<html" not in raw[:5000].lower():
                raise ValueError("SEC response is not filing HTML")
            report["current_raw_bytes"] = len(raw)
            report["current_raw_sha256"] = sha256(raw)
            if expected_hash:
                report["hash_match"] = report["current_raw_sha256"] == expected_hash

            blocks = extract_blocks(raw)
            compact_text = normalized("\n".join(blocks))
            missing = [
                marker for marker in item["validation_markers"]
                if normalized(marker) not in compact_text
            ]
            report["content_markers_match"] = len(blocks) >= 5 and not missing
            report["missing_markers"] = missing
            if len(blocks) < 5:
                report["error"] = "Fewer than five visible text blocks were extracted"
        except (OSError, RuntimeError, ValueError) as error:
            report["error"] = str(error)
        reports.append(report)
    return reports


def audit_s1_s2(seed_path: Path = SEED) -> list[dict]:
    """Check all six NBIS/IREN/ASTS S1/S2 sources in manifest order."""
    return audit_sources(seed_path=seed_path)


def audit_all(seed_path: Path = SEED) -> list[dict]:
    """Check every filing in the pilot source list in one run."""
    seed = json.loads(seed_path.read_text())
    return audit_sources([item["source_id"] for item in seed["documents"]], seed_path)


def verify(release: Path) -> dict:
    manifest_bytes = (release / "manifest.json").read_bytes()
    if sha256(manifest_bytes) != (release / "manifest.sha256").read_text().strip():
        raise ValueError("Manifest hash mismatch")
    manifest = json.loads(manifest_bytes)
    if manifest["extraction_version"] != EXTRACTION_VERSION:
        raise ValueError("Extraction version differs from this collector")
    seed = json.loads(SEED.read_text())
    if manifest["corpus_version"] == seed["corpus_version"]:
        if manifest["seed_sha256"] != sha256(SEED.read_bytes()):
            raise ValueError("Source seed changed without a new corpus version")
        expected = {item["source_id"]: item for item in seed["documents"]}
        for item in manifest["documents"]:
            original = expected.get(item["source_id"])
            if not original or any(item.get(key) != value for key, value in original.items() if key != "validation_markers"):
                raise ValueError(f"Manifest source differs from tracked seed: {item['source_id']}")
            if item["sec_url"] != sec_document_url(original):
                raise ValueError(f"Manifest SEC URL mismatch: {item['source_id']}")
            if (item["acceptance_datetime_raw"] != original["sec_metadata"]["acceptance"] or
                    item["sec_filing_date"] != original["sec_metadata"]["filing_date"] or
                    item["sec_report_date"] != original["sec_metadata"]["report_date"]):
                raise ValueError(f"Manifest SEC timing mismatch: {item['source_id']}")
    if len(manifest["documents"]) != 12:
        raise ValueError("Release is missing documents")
    chunk_bytes = (release / "chunks.jsonl").read_bytes()
    if sha256(chunk_bytes) != manifest["chunks_sha256"]:
        raise ValueError("Chunk file hash mismatch")
    by_id = {item["source_id"]: item for item in manifest["documents"]}
    if len(by_id) != 12 or any(not SOURCE_ID_PATTERN.fullmatch(key) for key in by_id):
        raise ValueError("Release source identities are invalid")
    counts: Counter[str] = Counter()
    chunks = [json.loads(line) for line in chunk_bytes.splitlines()]
    if len(chunks) != manifest["chunk_count"]:
        raise ValueError("Release chunk count mismatch")
    for chunk in chunks:
        source = by_id.get(chunk["source_id"])
        if not source or chunk["ticker"] != source["ticker"]:
            raise ValueError("Chunk source identity mismatch")
        if not (0 <= chunk["block_start"] <= chunk["block_end"] < source["blocks"]):
            raise ValueError(f"Invalid block locator: {chunk['chunk_id']}")
        if sha256(chunk["text"].encode("utf-8")) != chunk["text_sha256"]:
            raise ValueError(f"Chunk text hash mismatch: {chunk['chunk_id']}")
        counts[chunk["source_id"]] += 1
    if any(counts[source_id] != item["chunks"] for source_id, item in by_id.items()):
        raise ValueError("Per-source chunk count mismatch")
    total_bytes = 0
    for item in manifest["documents"]:
        raw = (release / "raw" / f"{item['source_id']}.htm").read_bytes()
        total_bytes += len(raw)
        if sha256(raw) != item["raw_sha256"]:
            raise ValueError(f"Raw hash mismatch: {item['source_id']}")
        if len(raw) != item["raw_bytes"]:
            raise ValueError(f"Raw size mismatch: {item['source_id']}")
        blocks = extract_blocks(raw)
        if len(blocks) != item["blocks"] or sha256("\n".join(blocks).encode("utf-8")) != item["extracted_sha256"]:
            raise ValueError(f"Extraction hash mismatch: {item['source_id']}")
    if total_bytes != manifest["raw_total_bytes"]:
        raise ValueError("Release raw byte total mismatch")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["build", "verify", "audit-all", "audit-s1-s2", "audit-source"])
    parser.add_argument("--release", type=Path, help="release directory for verify")
    parser.add_argument("--source-id", help="manifest source ID for audit-source")
    args = parser.parse_args()
    if args.source_id and args.action != "audit-source":
        parser.error("--source-id is only valid with audit-source")
    if args.action == "audit-source" and not args.source_id:
        parser.error("audit-source requires --source-id, for example ASTS-S3")
    try:
        if args.action == "build":
            path = build()
            print(f"Built {path}")
        elif args.action in {"audit-all", "audit-s1-s2", "audit-source"}:
            if args.action == "audit-all":
                reports = audit_all()
            elif args.action == "audit-s1-s2":
                reports = audit_s1_s2()
            else:
                reports = audit_sources([args.source_id])
            print(json.dumps(reports, indent=2))
            if any(
                report.get("error") or not report["identity_timing_match"] or
                not report["content_markers_match"]
                for report in reports
            ):
                return 1
        elif args.action == "verify":
            path = args.release or LOCAL / "releases" / json.loads(SEED.read_text())["corpus_version"]
            manifest = verify(path)
            print(f"Verified {len(manifest['documents'])} documents and {manifest['chunk_count']} chunks in {path}")
        return 0
    except (ValueError, FileExistsError, OSError, RuntimeError, json.JSONDecodeError) as error:
        print(f"Corpus {args.action} failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
