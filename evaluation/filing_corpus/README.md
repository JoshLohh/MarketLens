# SEC filing corpus and Q&A retrieval pilot

This evaluation slice uses twelve SEC-hosted documents: the ten source IDs in
`evaluation/qa_benchmark.jsonl`, plus IREN's original FY2024 Form 20-F and its
later 20-F/A. Four benchmark entries originally cite issuer pages; `sources.json`
maps them to issuer-authored SEC exhibits. It does not collect issuer websites,
publisher feeds, market prices, or news.

## Access and retention decision

The [SEC's reuse FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions)
says public EDGAR filing content is free to access and reuse. Its
[developer guidance](https://www.sec.gov/about/developer-resources) permits
scripted access with an identifying User-Agent and an aggregate ceiling of ten
requests per second. This collector uses at most two requests per second.
These sources were reviewed on 2026-10-11. The pilot keeps issuer-authored
filing HTML and excludes linked pages, graphics, and third-party exhibits.
The substitutions below were checked against the issuer releases recorded in
the [milestone 1 audit](../source_audit.md). They match the release dates and
the benchmark's relevant facts; they are not claimed to be byte-identical to
the issuer pages. The collector checks each accession and form against SEC
submissions, then checks document-specific content markers before publishing.
All twelve SEC responses were audited on 2026-10-12. Each matched its expected
accession, form, filing/acceptance/report dates, and content markers. All current
response hashes are pinned in `sources.json`; the six earlier IREN/ASTS audit
hashes remain alongside their current pins. NBIS-S3, IREN-S3, and both FY2024
filings had no earlier SEC hash in this manifest and received first-observation
pins. The earlier IREN/ASTS response bodies were temporary and are unavailable,
so the exact cause of their byte differences is unknown. Matching selected
markers does not prove the entire filing is substantively unchanged.

The first `sec-qa-pilot-v1` release was built and verified on 2026-10-12:
12 documents, 17,272,038 raw bytes, and 3,922 passages. Raw HTML is now retained
locally so future responses can be compared against an actual saved snapshot.
Initial retrieval evaluation and passage inspection were completed on
2026-10-12. At top five, development recall was 20/21 cases for at least one
expected source and 19/21 for all expected sources. The exposed holdout results
were 9/9 and 7/9. A top-ten development diagnostic raised recall to 21/21 and
20/21. These reports measure source retrieval, not answer quality. In
ASTS-QA-04, the exact balance-sheet row exists in the release but ranks ninth,
below the default five-passage cutoff; top ten includes it along with more
similarly worded passages. The reports are retained under ignored `local/`.

| Benchmark source | SEC exhibit and corroborating content |
|---|---|
| NBIS-S1 | [Q3 2024 release](https://www.sec.gov/Archives/edgar/data/1513845/000110465924112875/tm2427209d1_ex99-1.htm): October 31, USD 43.3m revenue, USD 2,288.2m cash. |
| NBIS-S2 | [Q4/FY2024 release](https://www.sec.gov/Archives/edgar/data/1513845/000110465925015480/tm257185d1_ex99-1.htm): February 20, USD 117.5m FY revenue, USD 2,449.6m cash. |
| NBIS-S3 | [Financing release](https://www.sec.gov/Archives/edgar/data/1513845/000110465924124570/tm2429858d1_ex99-1.htm): December 2, USD 700m definitive agreements, subject to closing. |
| IREN-S3 | [November update](https://www.sec.gov/Archives/edgar/data/1878848/000187884824000043/nov2024monthlyupdatevdc.htm): December 6, October cost revised from USD 20,834 to 18,896 per BTC, operating capacity 460 versus 360 MW. |

Raw HTML, extracted chunks, and evaluation reports live in ignored
`local/releases/` and `local/reports/`. A release directory is immutable. A
failed build removes its staging directory and leaves any prior release
untouched. Retain a release while it is used for this
private evaluation. If a source is excluded or the experiment ends, delete its
local release and reports using normal file removal; the tracked source
manifest keeps no raw filing body. A source exclusion requires a new source
manifest and corpus version; never silently edit an existing release. Back up
`local/releases/` separately if byte-for-byte replay across machines is needed.
The release verifier checks a manifest checksum, the tracked source identities,
SEC timing metadata, raw and chunk hashes, and block locators. This detects accidental changes and
ordinary one-file edits; the local checksum is not an adversarial signature.

## Run

Set an identifying contact in your environment, without putting it in Git:

```sh
export SEC_USER_AGENT='MarketLens Research YourName contact@example.com'
python3 evaluation/filing_corpus/corpus.py audit-all
python3 evaluation/filing_corpus/corpus.py build
python3 evaluation/filing_corpus/corpus.py verify
python3 evaluation/filing_corpus/test_corpus.py -v
python3 evaluation/filing_corpus/qa_baseline.py evaluate --split development
python3 evaluation/filing_corpus/qa_baseline.py evaluate --split holdout
python3 evaluation/filing_corpus/qa_baseline.py evaluate --split development --top-k 10
python3 evaluation/filing_corpus/qa_baseline.py ask --ticker IREN --question 'What changed in October electricity cost per Bitcoin?'
```

`audit-all` checks all twelve manifest entries in one run. `audit-s1-s2`
checks just NBIS-S1/S2, IREN-S1/S2, and ASTS-S1/S2. They print each response's
byte count and SHA-256, the
saved historical/current hashes, and whether filing metadata and expected text
markers match. It does not save filing bodies or publish a release. A changed
hash is reported for review; a filing-identity or content-marker failure makes
the command exit with an error.

`audit-source --source-id ID` runs the same read-only checks for one manifest
entry, such as ASTS-S3. Use it when a full build stops on a changed raw hash:
the audit checks the selected SEC filing's metadata and text markers against
the exact response fetched in that run before you decide whether to update its
expected hash.

On the current Mac setup, if Python reports a certificate-chain error, run the
SEC audit or build with the macOS system certificate bundle selected:

```sh
SSL_CERT_FILE=/etc/ssl/cert.pem python3 evaluation/filing_corpus/corpus.py audit-s1-s2
SSL_CERT_FILE=/etc/ssl/cert.pem python3 evaluation/filing_corpus/corpus.py audit-all
SSL_CERT_FILE=/etc/ssl/cert.pem python3 evaluation/filing_corpus/corpus.py audit-source --source-id ASTS-S3
SSL_CERT_FILE=/etc/ssl/cert.pem python3 evaluation/filing_corpus/corpus.py build
```

The holdout command records one report per corpus version, baseline version,
split, and retrieval depth. Its reference
answers were already visible in the repository, so its results are exposed
regression checks. `--top-k` controls how many passages are returned and
evaluated; top ten surfaced the ASTS-QA-04 balance-sheet passage, but also
returned more unrelated near-matches. Development cases can inform later changes, but each new
evaluated corpus needs a new version ID. `ask` returns ranked source passages
with SEC URLs and local block locators; it does **not** generate a financial
answer. The reports measure whether the cited source documents were retrieved,
not answer correctness, citation support, numeric accuracy, or abstention.
The reports also record hashes of the benchmark and baseline code. A changed
source list or extraction method requires a new corpus version; a changed
ranking method requires a new baseline version.

## Provenance rules

- Each manifest row preserves source ID, CIK, accession, form, SEC URL, filing
  and acceptance values from SEC submissions, issuer release date if known,
  benchmark vintage date, report period, accounting basis, observation time,
  raw hash, extraction hash, and block/chunk counts.
- `manifest.sha256` protects the metadata file against unnoticed local edits;
  SEC URLs are also reconstructed from the tracked CIK/accession/document.
- SEC acceptance is an earliest possible bound on public availability, not an
  observed publication time. The SEC says no timestamp records when a filing
  first becomes available on its website. The baseline does not make a
  point-in-time forecast. ASTS-S2 was issued on March 3, 2025 and furnished
  as an SEC exhibit on March 4; those are distinct events in the manifest.
- IREN's original and amended FY2024 filings remain separate. The amended
  document links back to the original; the FY2025 US GAAP filing is another
  accounting basis. Do not mix their figures without identifying the vintage.
- Missing markers, a mismatched accession/form, an oversized response, or a
  partial extraction fails the entire build before a release is published.

For the current hash observations and their basis, see the follow-up entries
in [`evaluation/source_audit.md`](../source_audit.md). Any future response
that differs from a current pinned hash fails the build until its provenance
is reviewed and the source manifest is deliberately updated.
The first release now exists: further source-manifest changes require a new
corpus version. Running `build` again for the same version fails rather than
overwriting the saved release; use `verify` to check the existing collection.

This tool is a Python standard-library evaluation harness. It does not select
the language, framework, database, or model for the planned Telegram product.
