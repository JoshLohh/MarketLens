# SEC filing corpus and Q&A retrieval pilot

**Current status (2026-10-30):** the hybrid lexical-plus-embedding retriever
with context-anchored evidence packing is the frozen candidate for the first
Telegram Q&A integration; lexical remains a regression control. The fixed
retrieval pilot and paired-answer evaluation are complete for this development
round. The latest graded pair, `paired-exact-row-hints-2026-10-29-v1`, scores
17 PASS / 4 UNKNOWN / 0 FAIL for both arms. The four UNKNOWNs per arm include
two expected abstentions with inapplicable claim metadata, an automatic
annualization-multiplier check despite a source-supported answer, and the
IREN-QA-07 units disagreement/rejected hybrid answer. These results do not
establish a general quality winner or production readiness. The next project
step is the private, on-demand Telegram Q&A MVP; no bot is integrated yet.
Scheduled monitoring and alerts are deferred. The app framework and any
persistent vector database remain undecided. Beginner-friendly retrieval
settings and flow are in the [hybrid retrieval study guide](../../.codex/marketlens-study-guide.md#hybrid-retrieval-study-guide).

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

## How the pilot extracts filing tables

SEC serves the selected filing documents as HTML. `corpus.py` downloads and
retains that source HTML; it does not turn another format into HTML. The custom
`FilingText` parser reads visible text from the HTML. It skips the document
head, scripts, styles, `noscript`, hidden iXBRL content, and elements marked
hidden, `aria-hidden="true"`, or `display:none`.

For a table, the parser puts `td` and `th` cell text into one plain-text block,
separated by `|`, and flushes the block at each table row. `make_chunks()` then
groups nearby blocks into text passages of at most about 900 characters while
keeping source IDs and block locations. The retrieval evidence packer can add
nearby table/header context later. This keeps many simple label-and-value rows
readable, but the parser does **not** emit structured row/column objects or
interpret complex merged headers, `rowspan`/`colspan`, or accounting meaning.

The current tests show that a simple visible row keeps its label beside its
numeric cells and that hidden/head content is omitted. Those checks do not
prove that every table in every filing is extracted correctly. The parser is a
small, auditable fit for this fixed pilot, not a validated general-purpose
financial-table parser. The raw HTML is retained in the immutable local release
so a future parser can be compared against the same source bytes without
expanding or rebuilding this pilot.

## Run

Set an identifying contact in your environment, without putting it in Git:

```sh
export SEC_USER_AGENT='MarketLens Research Contact contact@example.com'
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

### Earlier hybrid result: protect three lexical hits

The weighted hybrid tuning showed that dense results could push useful filing
rows out of the final ten. `retrieval_compare.py compare` now accepts
`--lexical-floor 3`: hybrid results begin with the top three ticker-filtered
lexical passages, then use weighted reciprocal-rank fusion to fill the remaining
slots. The lexical comparison arm and the table-context packer are unchanged.
The run saves a `lexical_protected` marker with those retained passages so the
ranking can be audited later.

On the same 21 development questions and unchanged `sec-qa-pilot-v1` release
(12 documents, 3,922 passages), the 0.75 lexical / 0.25 dense candidate matched
the lexical-plus-table baseline on packed expected-source presence (21/21 with
any expected source; 20/21 with all), financial row/header evidence (14/14),
and complete answerable evidence (17/19). It recovered the original IREN
October installed-capacity and average-hashrate passages that the earlier
weighted candidate dropped. One expected-abstention scope case also became
fully covered. No answerable case became newly complete, so the existing
generation gate did not pass and no LLM comparison was run.

In the final cache-hit run, warm median/p95 retrieval-plus-packing time was
12.00/29.00 ms for hybrid and 5.58/22.45 ms for lexical. Peak process memory
was about 537.2 MB versus 111.4 MB. The first run had a cache miss: local index
setup took 87.3 seconds, including 83.8 seconds of passage encoding; the
follow-up cache-hit setup took 3.45 seconds. Embedding API cost and generation
cost were zero; local compute cost is unmeasured. These figures are from one
machine and 21 exposed development questions, not product-scale performance or
answer correctness. The nine exposed holdout questions were not used.

Final saved report: `local/retrieval_runs/hybrid-floor3-2026-10-25-v2/run.json`.
The earlier `v1` report is preserved. The `v2` cache-hit rerun reproduces all
21 lexical and hybrid rankings and evidence-pack hashes; see its
`source-review.md` sidecar for case-difference inspection.

## Step 3: filing answers and comparison

`qa_compare.py` adds two answerer interfaces beside the unchanged retrieval-only
`qa_baseline.py`:

- **3A, `structured`:** source-specific row/header selectors extract financial
  facts from supplied evidence. Ordinary code handles unit conversion and Decimal
  calculations. Every fact retains its raw value/sign/units, normalized value,
  company, period, accounting basis, accession/form/amendment, publication/SEC
  dates, source URL and local locator. Unsupported questions or missing/conflicting
  row/header evidence produce an abstention. This is a bounded financial baseline;
  explanations are outside its supported routes.
- **3B, `rag`:** the optional OpenAI Responses adapter supplies the same evidence
  to a model specified by the caller. It accepts cited claims or an abstention and
  checks response/citation identity, exact quotes and covered numeric facts. These
  checks cannot establish general semantic support. A real model run and source
  review are required to measure LLM answer quality. With no explicit live
  configuration, RAG is **unavailable**, not a simulated successful model.

New answer records use schema version 2. Each claim carries both a canonical
`ticker` (for example, `NBIS`) and a display-ready `company_name` (for example,
`Nebius Group`); the validator checks both against the selected evidence bundle.
The answer record also retains its top-level `ticker` and `company`. Version-1
saved answers, whose claim `company` field held the ticker, remain readable by the
validator; historical run artifacts are not rewritten.

Python and this one provider adapter are experiment choices, not decisions about
the product's runtime, model, storage or permanent provider.

### Shared evidence and limits

Both answerers receive exactly the same ticker, question and frozen evidence
bundle/hash. The current `top10-table-context-v4` packer records the unchanged lexical
top-ten IDs separately. It packs the first raw hit, supplementary table rows and
their context, then applicable accounting-basis evidence before the remaining raw
hits and ordinary neighbors. A supplementary table search recognizes a small set of
financial row labels from the question (revenue, cash and equivalents, capex,
common-stockholder loss and adjusted operating expenses). It selects at most two
rows from distinct sources, with their immediate context. Explicit fiscal years
and source reporting periods rank the rows;
amendment dates do not extend a filing's fiscal year, and Q3 is never assumed to
end in September for every issuer. Date/header-only rows and incidental prose
cannot match the financial labels. The policy uses question, passage and source
text, without expected source IDs, reference answers or fact-selector lookup.
`table_retrieved_chunk_ids` records the supplements separately from the original
top-ten results. When selected-source metadata is missing or qualified by a
comparative/transition scope, `basis_support()` searches that same filing for
explicit financial-statement basis or transition text. It selects at most one
passage per source and two overall, recorded in `basis_retrieved_chunk_ids`.
Supporting evidence gets priority over lower-ranked passages under the fixed
byte cap; raw top-ten source recall is not a packed-evidence metric. No answerer
can fall back to unselected corpus facts.

Evidence is capped at 16,000 UTF-8 bytes, with whole retained passages rather than
silently truncated numeric rows. Missing headers still cause abstention. Irrelevant
contact sections are omitted. The request is capped at 32,000 bytes. Source recall
is reported for the raw top-ten results separately from packed source membership,
numeric matches, answer coverage and reviewed claim support.

For a multi-period calculation, the RAG prompt gives an explicit claim pattern:
put each reported input (such as FY2024 revenue) in its own claim with that exact
single-period label, then put only the calculated metric (such as revenue growth)
in a separate claim covering both periods. The financial consistency check rejects
a source-backed metric when the claim's period does not match the extracted fact;
rejected model text is deliberately not retained, so the safe diagnostic identifies
the rule that failed without showing the draft. Numeric fact names should match the
canonical extracted metric (for example, `adjusted_opex`); `average` is reserved for
actual average measures. If a table's unit scale is in a separate header, cite an
exact quote of that header as well as the value row. For a two-period adjusted-
expense comparison, the fact claims use `adjusted_opex` with one exact quarter
period each; the combined claim carries only `opex_increase` and/or `opex_growth`.

### Offline commands

```sh
python3 evaluation/filing_corpus/qa_compare.py ask --ticker ASTS --question 'What were cash and cash equivalents at December 31, 2024, excluding the separate restricted-cash line?'
python3 evaluation/filing_corpus/qa_compare.py evaluate --split development --run-id development-my-run-01
python3 -m unittest discover -s evaluation/filing_corpus -p 'test_*.py' -v
```

`ask` prints JSON answers, calculations and citations. `evaluate` processes all 21
development questions and saves an immutable run under ignored
`local/answer_runs/<run-id>/run.json`. It records the benchmark/corpus/code hashes,
evidence bundles, answers, numerical grading references, setup/query timings and
available cost/usage. Only ticker and question enter answer generation; reference
answers, expected values/sources and case IDs enter grading afterward. Use a fresh
run ID for a new run. No SEC rebuild or corpus expansion is involved.

Source review is an explicit second stage:

```sh
python3 evaluation/filing_corpus/qa_compare.py review-template --run evaluation/filing_corpus/local/answer_runs/development-my-run-01 --reviewer root
python3 evaluation/filing_corpus/qa_compare.py review-template --run evaluation/filing_corpus/local/answer_runs/development-my-run-01 --reviewer verifier
# Review source blocks for every answer/claim; fill the two local JSON templates.
python3 evaluation/filing_corpus/qa_compare.py grade --run evaluation/filing_corpus/local/answer_runs/development-my-run-01 --root-review evaluation/filing_corpus/local/answer_runs/development-my-run-01/root-review.json --verifier-review evaluation/filing_corpus/local/answer_runs/development-my-run-01/verifier-review.json
```

Each reviewer records PASS/FAIL/UNKNOWN for correctness, support, citations,
company, period, basis, units, vintage and abstention, plus claim-level support and
metadata judgments. Unknown, incomplete or disputed review cannot count as a
pass. The grader records explicit denominators, category/claim dimensions,
excessive abstentions, review coverage and unavailable/error cases. Valid quotes
and JSON do not prove that a claim follows from the evidence. A run checksum
detects accidental edits; it is not an adversarial signature or proof that human
judgments are correct. Offline transport fixtures have no live quality scores.
The source-review-v2 report also shows case-dimension counts and
`abstention_review`: safe refusals given the supplied bundle, excessive refusals,
correct expected refusals, and answerable cases missed because evidence was not
packed. This keeps a sound safety decision visible even when absent claim metadata
leaves the strict overall verdict UNKNOWN. Legacy `correct_abstentions` counts
only overall-PASS cases. Grading version and current grader code hashes are
recorded separately from the immutable generation run's original code hashes.
For a question with no expected numeric target, the automatic numeric status is
`NOT_APPLICABLE`; it is not counted as a numeric pass. Summary counts separately
show the numeric-target denominator and cases where numeric scoring does not
apply. Abstention quality remains a separate check.

Freeze the development code/policy before using the exposed holdout:

```sh
python3 evaluation/filing_corpus/qa_compare.py evaluate --split holdout --freeze-run evaluation/filing_corpus/local/answer_runs/development-my-run-01 --run-id exposed-regression-my-run-01
```

That command requires matching development code, benchmark and corpus hashes,
runs offline, and labels its nine cases exposed regression checks. Do not tune
selectors, prompts or packing against those outputs or call them a blind test.

### Optional live experiment

No live requests occur without `--live`, an explicit model, a reviewed local JSON
configuration and `OPENAI_API_KEY` in the process environment. Keep keys out of
commands saved to history, prompts, reports and Git. The config accepts only named
non-secret fields; it never stores the key. The reviewed scope requires:

- `network_authorized: true`, `model`, `max_spend_usd` (positive, at most 1.00);
- `input_usd_per_million`, `output_usd_per_million` as positive decimal strings;
- `count_usd_per_request` as a reviewed non-negative decimal string;
- `pricing_checked_on` (Singapore date, within 31 days), an official
  `pricing_source_url`, and a non-sensitive `reviewed_by` label;
- `count_endpoint_verified: true` after verifying that the selected account/model
  can use the token-count endpoint and its billing terms;
- optionally lower limits for `max_input_tokens` (at most 8,000),
  `max_output_tokens` (default 1,200, explicit maximum 2,400), `max_generation_requests` (at most 21), and
  `timeout_seconds` (at most 45).

These config fields record a human review; the code does not independently prove
account eligibility or pricing. Missing/unverified access or rates leave live
evaluation pending. The [official token-count guide](https://developers.openai.com/api/docs/guides/token-counting)
describes counting the same model/input/instructions/schema before generation.
The adapter allows at most 21 count requests plus 21 generation requests, reserves
reviewed count fees and worst-case generation cost before dispatch, keeps
reservations after errors, and halts on unknown usage or a bound violation. No
automatic retries, provider/model switching, uploads, tools or redirects are used.
Costs are rate-snapshot estimates, not invoices; local infrastructure cost remains
unmeasured. Failed-response usage/cost is retained where known.

Failed attempts also record a bounded diagnostic: whether the request was in token
counting, answer generation, or response validation; safe HTTP status and provider
error type/code when available; and a request ID when returned. Raw error bodies,
API keys, prompts, and filing passages are never written to these diagnostics.
If the provider responds but the answer is rejected locally, the answer record
includes a `failure_diagnostic.stage` such as `model_payload_shape`,
`citation_reconstruction`, `answer_integrity`, or `financial_consistency`; it does
not include the generated answer or raw exception text. When the failure matches
a known local validation rule, a short fixed `failure_diagnostic.code` is also
recorded (for example, `claim_company_mismatch`, `citation_chunk_not_in_evidence`,
`citation_quote_not_verbatim`, `numeric_period_mismatch`,
`numeric_metric_mismatch`, `numeric_unit_header_missing`,
`numeric_accounting_basis_mismatch`, `numeric_unit_mismatch`, or
`numeric_value_mismatch`). Unrecognized errors do not expose their exception
text. Incomplete provider responses now expose an allowlisted reason, such as
`max_output_tokens`, in the case diagnostic. Later skipped cases identify the
halted provider session instead of claiming it was never configured. Usage and
reservations survive a failure; no automatic retry is introduced. The output
allowance includes reasoning and visible answer tokens; see the
[official reasoning guide](https://developers.openai.com/api/docs/guides/reasoning).

The automatic numeric grader uses exact metric names plus a small explicit alias
list (including cash-and-equivalents and capital-expenditure labels). Explicit
USD/thousand/million scales can be converted exactly; other currencies and units
are not guessed. Unknown names remain `UNKNOWN`, generic increase/decrease labels
are not automatically equated to a particular metric, and a recognized label with
a wrong signed amount or scale is `FAIL`. Existing benchmark tolerances are
unchanged. These checks do not replace source, period, or citation review.

The v9 financial-consistency gate associates a claim with the exact quoted
value row or sentence (`row_evidence`), rather than every fact sharing its
passage. This fixes false metric rejections when a paragraph/table contains
revenue, cash, capex or several operating metrics together. A table row requires
its same-source scale header; a quoted sentence with a matching explicit money
amount, currency and scale can ground the units directly. Period, basis, amount,
row location, filing identity and genuine conflict checks remain enforced.
A model amount that disagrees with a known fact is a value error; it does not by
itself establish a conflict between filings. The grader accepts `%` as `percent`
without treating fractions as percentages.

**Correction (2026-10-24):** earlier notes incorrectly described the ASTS
40.760m reconciliation amount as Q3 from another filing vintage. It is Q4:
ASTS-S2:00037 starts the December-quarter table; :00038 finishes it, then starts
the September-quarter table. The Q3 row continues in :00039 at 45.293m, matching
ASTS-S1:00031. The fixed Q2/Q3 comparison is 34.627m to 45.293m, a 10.666m /
30.803% increase. The prompt now explicitly uses the nearest preceding applicable
table header and checks year columns before asserting a source conflict.

The saved v8 development run returned RAG 8 answered / 9 error / 4 abstained,
versus structured 14 answered / 7 abstained. Six error cases have reproducible
validator false positives; two failed exact quote checks and one failed a scale
citation check. Rejected drafts are absent, so their exact contents are unknown.
The manual review found correct underlying content in the eight accepted
answers, with a missing supporting quote for part of ASTS-QA-06 and a `%` grading
defect in IREN-QA-07. The ASTS expense abstention misread the Q4/Q3 boundary.
Detailed findings and before/after offline fixtures are saved alongside
`local/answer_runs/live-development-2026-10-23-v8-01/run.json`.

The saved v9 live run `live-development-2026-10-24-v9-01`, reviewed on 2026-10-25,
returns RAG 12 answered / 6 errors / 3 abstentions, with 7/15 numeric targets
passing automatically. Structured remains 14 answered / 7 abstained. All 21
provider generations completed and all 21 evidence packs match v8. Four Nebius
cases now answer, and ASTS-QA-06 now quotes its previously uncited service clause.
Median RAG query time is 6.811 seconds; configured-rate cost estimate is USD
0.0198573, not an invoice. Automatic success and source presence are not semantic
quality scores; paired formal grading remains pending.

Offline investigation reproduces two remaining validator gaps: exact Bitcoin
quotes split across adjacent passages fail row association, and the same-source
cash sentence in NBIS-S2:00001 is outside the registered cash selectors. IREN's
FY2025 revenue question has the revenue rows but lacks the current-year GAAP
transition passage in its evidence bundle; its abstention is safe given that
bundle but misses an answerable question. Rejected live drafts are unavailable,
so exact quote/period/value/calculation mistakes cannot be reconstructed. Findings
and a six-probe offline script are in the v9 run's ignored `review-v9.md` and
`review-checks-v9.json`. No implementation repair was made during this review.
The fixed corpus, benchmark and saved live runs are unchanged; no holdout was used.

The subsequent `filing-answers-v10` candidate repairs those reproduced citation
cases. Fact extraction records the exact normalized value row; matching a quote
requires its own row/value instead of unrelated numbers in a flattened paragraph.
Adjacent citation fragments can reconstruct a label only at the actual shared
source-block boundary, from the same source and consecutive chunks. Verified
alternate cash and number-first Bitcoin layouts are registered in `fact_specs.json`.
Exact raw quote validation remains mandatory. The packer also supplies the
current-year accounting-basis statement from the selected filing. Genuine
unspecified source conflicts now abstain even for derived-only answers.

Failure diagnostics include claim/number/citation ordinals, supplied chunk IDs,
allowlisted observed metadata and expected extracted facts. Calculation failures
distinguish value, unit, basis, period and citation checks. Rejected prose/quotes
and arbitrary metadata strings are not saved. The prompt now explicitly separates
measurement periods from release dates and requires detailed-row inputs for math;
these instructions have not been measured live under v10.

Twenty-one focused offline tests passed; all twelve accepted v9 payloads replay
under the new packs, structured coverage stays 14/21, and raw top-ten IDs remain
unchanged for all 21 development cases. Maximum observed evidence/request sizes
were 15,516/24,798 bytes. This establishes bounded contract behavior, not a new
RAG quality score. Verification artifacts are ignored under
`local/repair_checks/v10-2026-10-25/`. Next use targeted live questions to confirm
the repaired paths and inspect richer diagnostics before another complete batch.

After those prerequisites and live authorization are satisfied, a caller can run:

```sh
python3 evaluation/filing_corpus/qa_compare.py evaluate --split development --run-id live-development-my-run-01 --live --live-config /absolute/path/to/ignored-reviewed-config.json
```

Requests use `store=false` and foreground mode. This does not establish zero
provider retention: standard abuse monitoring can retain content under the
[official data controls](https://developers.openai.com/api/docs/guides/your-data).
Only the question and selected public filing evidence go to the provider. The
[Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)
also notes that structured output can contain mistakes. Prompt-boundary tests
verify request isolation, not live-model resistance to every injected instruction.

The actual live comparison remains incomplete until all 21 development cases
are processed and source-reviewed with a real configured model. Partial runs must
show partial coverage and unavailable/error cases. No news/Telegram monitoring,
forecasting, full-history ingestion or product app is implemented by this slice.

### Recorded offline result — 2026-10-15 (Singapore)

`step3-development-v3` processed all 21 development cases. The structured path
answered 12 and abstained on nine. Root and a fresh Codex verifier examined all
21 cases and all 15 returned claims against the supplied evidence/source blocks.
All 15 claims passed support, citation, company, period, basis, unit and vintage
checks. These are source-review judgments on a small, known pilot, not proof of
generalization or an automated entailment guarantee.

The strict case grader recorded **12 PASS / 7 FAIL / 2 UNKNOWN**. Five unsupported
explanation routes and two missing financial row/header bundles fail answer
completeness despite safe abstention. Both intended customer-calculation gaps
passed the abstention-decision check; unasserted citation/metadata dimensions stay
UNKNOWN, so neither counts as an overall pass. `reviewed_cases: 12` counts cases
with every dimension known, rather than the 21 cases actually examined.
`correct_abstentions: 0` counts only overall-PASS abstentions, not the two
appropriate abstention decisions. Claim denominators exclude absent claims.

Raw retrieval-source recall remains separate: any expected source **21/21**, all
expected sources **20/21**, at ten hits. Structured median query time was **3.867
ms** over 21 local queries; corpus/index setup took **852.308 ms** and is reported
separately. Provider-request cost was USD 0; local infrastructure cost is
unmeasured. The RAG path returned **unavailable for 21/21**. No environment API key
or experiment model was configured, and no live LLM requests were made; its answer
quality, latency and cost remain UNKNOWN.

After freezing code, benchmark and corpus hashes against this development run,
`step3-exposed-regression-v1` processed the nine exposed checks once, offline:
structured one answer/eight abstentions, one numeric automatic pass; raw source
recall 9/9 any and 7/9 all. Its semantic answer quality is unreviewed; RAG is
unavailable for all nine. No selectors, prompts or packing were tuned from those
outputs. All 34 tests and fixed-corpus verification passed. Full findings and
limitations are recorded in `evaluation/source_audit.md`; run/review JSON remains
ignored under `local/answer_runs/`.

### Live Run 02 review and bounded fixes — 2026-10-22 (Singapore)

`live-development-2026-10-21-02` is a saved, unchanged live run using the temporary
`gpt-6-luna` experiment model. It returned 12 answered / 4 abstained / 4 error /
1 unavailable RAG cases. Both agent source reviewers examined all 21 case
dispositions and 13 returned RAG claims: support 12 PASS / 1 UNKNOWN, and all 13
claims passed citation/company/period/basis/vintage checks. The strict case
grades are 2 PASS / 3 FAIL / 16 UNKNOWN because saved exact numeric checks,
unasserted narrative units and unavailable/error cases also affect that verdict.
These are known-development-set judgments, not a blind accuracy estimate.
The original numeric grades are retained rather than re-scored using new aliases.

The formal review corrects two earlier conversational judgments: NBIS-QA-09's
cash-balance increase is accurate without the extra operating-cash-flow caveat;
IREN-QA-08 answers the requested USD/BTC decrease even though the benchmark also
expects a percentage. NBIS-QA-05's interpretation of the quoted ARR definition
retains UNKNOWN support. Review artifacts and a detailed note are local alongside
`run.json`. The original raw source recall remains 21/21 any and 20/21 all.

Two abstentions exposed rows that BM25 ranking missed: IREN's primary revenue
statement and ASTS's Q3 revenue statement. The new table-support policy supplies
those rows to both answerers, and additional guarded table layouts let the
structured extractor read them. The offline baseline now answers 14/21, including
both repaired cases; five unsupported explanation routes and two intended
customer-data gaps remain abstentions. Prompt edits request concise exact quotes,
clear accounting-basis labels, separately period-tagged facts, reproducible
comparisons and explicit interpretation. The local experiment output limit is
now 2,400, retaining its USD 0.50 ceiling. No fresh live response has verified the
effect of these changes; use targeted development questions before a new full
live run. No exposed holdout case was used for these fixes.

### Targeted citation diagnostics and compact requests — 2026-10-25

The submitted v10 IREN FY2025 revenue ask now answers USD 501.023 million and
cites the supplied GAAP transition statement. Its mining ask still returns
`numeric_citation_row_mismatch`, despite metadata matching 439 BTC for October
2024. The provider completed; the rejected quotes were not saved, so this output
does not establish their exact wording or whether rejection was a validator bug.

`filing-answers-v11` keeps the v10 evidence pack and numeric safeguards. The prompt
requires complete metric-label/value evidence, with separate exact boundary
excerpts if a row spans passages. On subsequent financial-validation failures,
`failure_diagnostic.citation_spans` records up to eight source-verified locations:
`source_id`, `chunk_id`, `citation_ordinal`, `start`, `end`, and `ambiguous`.
Offsets are character indices within the original passage, with an exclusive end;
they reconstruct the exact quote locally without retaining rejected prose. The
first occurrence is used; `ambiguous` flags repeated text. These locations never
decide whether the answer passes. Non-verbatim quotes fail earlier and have no
verified span.

`model_evidence()` sends all original passages and source metadata plus the
question, ticker and evidence hash. Raw hit lists, omission IDs and corpus
bookkeeping stay in the local full pack. Compact JSON serialization reduces wire
size without truncating evidence or changing the shared hash. Across the 21
development questions, input text is 748–1,035 bytes smaller, and the complete
generation request is 357–658 bytes smaller even with the expanded prompt. Actual
token/cost savings require fresh provider measurements; byte savings are not a
token or billing claim. Model, network authorization and budget settings are
unchanged.

Twenty-four named focused tests and all twelve accepted-v9-payload replays pass;
structured coverage remains 14/21 and raw top-ten IDs are unchanged. These are
offline checks, not a v11 answer-quality score. No full suite, new API call or
exposed holdout use occurred. Ignored artifacts are under
`local/repair_checks/v11-2026-10-25/`; next repeat the mining ask and inspect any new
diagnostic before checking the cash comparison.

**Subsequent targeted live check:** The submitted v11 mining response now answers
439 BTC for October 2024 and cites the month header plus `Bitcoin` at the left
passage boundary and `mined 439 BTC` at the right boundary. All five citation
occurrences across both methods match the frozen text and metadata; local replay
passes. The evidence hash is unchanged from the preceding failed ask. Input
tokens fell 6,312 to 5,841 (7.46%); output rose 434 to 512; total tokens fell
6,746 to 6,353. The configured-rate estimate fell USD 0.0008482 to USD 0.0008401,
while query time increased 6.732 to 7.559 seconds. These are one pair of observed
responses, not a general cost/latency claim or proof of prompt causality. No code
change was needed for this accepted response. Local review evidence is in
`local/repair_checks/v11-2026-10-25/review-live-mining-v11.json`. Next check the cash
comparison; the full v11 development evaluation remains pending.

### Cash-comparison contract repair — v12 candidate, 2026-10-25

The submitted cash ask completed at the provider, then failed locally on claim
three's second number: an unrecognized metric, 7.054 percent. Its diagnostic spans
reconstruct the original releases' cash sentences: USD 2,288.2 million at September
30 and USD 2,449.6 million at December 31. Their difference is USD 161.4 million;
the starting-balance percentage rounds to 7.054%. The exact rejected metric name
and full claim prose remain unavailable. The prompt previously asked for an
extra percentage without defining its cash-specific name in the validator.

`filing-answers-v12` defines `cash_change_percent` alongside `cash_change`. It
requests the change asked for and avoids an unrequested percentage. The new
`_validate_cash_calculations()` requires two source-supported instant cash fact
claims, preserves each filing identity and exact date, verifies basis and unit
compatibility, and recomputes later-minus-earlier or that difference divided by
the nonzero starting amount times 100. Both period rows must be cited in the
comparison. Wrong results are rejected at reported precision; they are not fixed.
This also closes a previous path where recognized absolute cash-change metadata
was not recomputed.

After all numeric checks pass, the cash comparison sentence is rendered locally
from the checked inputs and results, with original citations retained. The answer
records `calculation_text_source: local_validated_cash_inputs`. This replaces
model comparison prose, preventing a correct positive number from being displayed
as a decrease or with an invented cause. Cash comparisons must have their own
claim; mixed calculation claims reject rather than silently losing other content.
The LLM still selects/cites evidence and drafts the fact claims. This is a narrow
numeric presentation guard, not general semantic validation of other prose.

Twenty-nine focused tests and replay of all twelve accepted v9 payloads pass.
All 21 full evidence packs equal v11, raw top-ten IDs are unchanged, and corpus,
benchmark, context and old-run hashes are preserved. Maximum shaped request is
24,432 bytes under the unchanged 32,000-byte cap. The prompt is slightly longer;
requesting fewer unnecessary output numbers may reduce usage but requires a live
measurement. No new API request or full test suite was run. Ignored snapshots,
diff, focused output, source-span reconstruction and offline synthetic probes are
under `local/repair_checks/v12-2026-10-25/`. Next repeat the cash ask before a full
development run; a live v12 answer-quality result is not yet available.


**Subsequent targeted v12 cash check:** The submitted response now answers with
three claims: the September balance (USD 2,288.2 million), December balance
(USD 2,449.6 million), and locally rendered +USD 161.4 million change. All fifteen
citation occurrences across both answerers match frozen text and full source
metadata. The three RAG claims, reporting dates, US GAAP basis, units and cash
arithmetic pass source inspection and offline replay; comparison text is marked
`calculation_text_source: local_validated_cash_inputs`. The evidence hash is
unchanged from the failed ask. The unrequested percentage is absent, but input
increased 6,120 to 6,171 and output 1,538 to 1,625 tokens. Configured-rate estimate
increased USD 0.001381 to USD 0.0014296; query time fell 16.752 to 15.315 seconds.
This is not a cost-saving or general performance claim. No further implementation
change was needed. The local review is
`local/repair_checks/v12-2026-10-25/review-live-cash-v12.json`. The next check is one
full 21-question development run, followed by answer/source review with retrieval
recall kept separate. A full v12 quality result is still unavailable.

### Locally compute derived cash and revenue results (2026-10-29)

The saved `paired-batch-unknown-fix-2026-10-29-v1` diagnostics showed two
model arithmetic errors: 167.705% instead of 167.652% revenue growth, and
7.053% instead of 7.054% cash-balance growth. The validators were correctly
rejecting those results. The RAG answer flow now computes these two derived
metrics from the separately source-checked facts and renders the comparison
claim itself. It still requires both cited fact rows, matching periods and
accounting basis, compatible units, and a nonzero starting value. It does not
change raw source-fact values or citation checks. The unrelated IREN-QA-04
exact-quote failure remains unresolved; rejected quote prose is not retained,
so the saved run does not reveal the precise mismatch.

Six focused offline Stage 1 tests pass, including wrong-model-result correction
and missing-citation rejection. The user has not yet run the full Stage 1 suite.
No Stage 2 comparison or Stage 3 LLM call was run after this code change; the
saved pair remains historical evidence and is not retroactively rewritten.

**Follow-up:** the user ran full unittest discovery: 170 tests, no failures,
with seven embedding-dependent tests skipped because the selected Python lacked
the isolated embedding packages. The project experiment virtualenv is available;
run those seven skipped tests under it before refreshing Stage 2.


### Full v12 review and v13 context candidate — 2026-10-25

The full v12 development run returns 17 RAG answers, one exact-quote reconstruction
error and three abstentions; structured remains 14 answers and seven abstentions.
All 21 provider generations completed. All twelve accepted v9 cases still answer;
five failures recover. There are 27 accepted RAG claims with 59 exact citation
occurrences; the structured answers have 17 claims and 37 occurrences. Source
inspection found no unsupported factual clause among the accepted RAG answers,
but formal paired grading is pending. RAG median query time is 7.059 seconds and
configured-rate provider estimate USD 0.0194912, not an invoice. Raw top-ten
source recall remains 21/21 any and 20/21 all.

Nine numeric automatic passes do not mean that only nine answers are correct.
ARR's 12 multiplier has a noncanonical name; IREN's correct USD 501.023 million
conversion lacks the duplicate conversion-output name expected by grading; two
correct absolute comparisons omit percentages also expected by the benchmark.
Those four accepted cases remain numeric UNKNOWN. The remaining error and
excessive abstention also affect the 15-target denominator. Do not silently
change benchmark targets or rescore the saved run to raise this count.

IREN capacity has a non-verbatim citation; its discarded wording is unknown.
Customer-specific net profit and Vodafone margin appropriately abstain. ASTS net
loss is an excessive abstention: although selected primary row S1:00022 lacks its
own preceding header, the bundle already contains a complete alternative at
S4:00138/00139. The baseline and an offline citation-valid fixture answer from
that alternative. An initial missing-evidence classification was corrected after
inspection. Per-case review and the correction probe are ignored alongside the
immutable v12 run in `local/answer_runs/live-development-2026-10-25-v12-01/`.

Candidate `filing-answers-v13` / `top10-table-context-v4` groups a selected numeric
row with its preceding period/scale header and all intervening chunks, within a
four-chunk backward limit in the same filing and company. It stops at a new
statement title or missing chunk and never borrows a header below the row. This
makes the primary table context clearer; it is not evidence absent from the
whole original bundle. `table_context_chunk_ids` records the selected context
separately from raw/table hits. Both answerers receive the same capped full pack;
no source body changes or corpus growth occur.

A future non-verbatim quote error can include `quote_match_hint` and trusted
`source_span` character offsets for a whitespace/case/smart-punctuation match.
These are diagnostic only: changed quotes still reject, no raw rejected text is
stored, and arbitrary rewrites report `not_located`. The prompt and generation
schema are unchanged in this pass. No v13 live quality improvement is claimed.

Thirty-seven focused tests pass, including table boundaries/gaps/company/source,
forward-header rejection, unchanged table ranking, earlier guard regressions,
and quote-hint privacy/strictness. All seventeen accepted v12 payloads replay;
structured coverage stays 14/21; raw top-ten and table-hit IDs stay unchanged for
all 21 questions. Max pack/request bytes are 15,547/24,432 under unchanged
16,000/32,000 caps. Ignored exact diffs/checks are under
`local/repair_checks/v13-2026-10-25/`. No full suite, live request, exposed holdout,
context update, commit or push occurred. Next use targeted loss/capacity asks.


Table groups are admitted atomically under the evidence budget: a header and its
selected row cannot be separated by individual admission failures, including
when a member enters through a raw hit or neighbor. A group that cannot fit is
omitted together and its tentative source metadata is rolled back. The backward
scan stops at any STATEMENT(S) OF or BALANCE SHEET(S) boundary, including income
and equity statements. These two limits were independently falsified and then
covered by focused regression checks.


Atomicity applies to a header-to-row group that was actually discovered. If the
bounded scan finds no complete header context, the existing row-only retrieval
fallback remains possible; retrieval alone does not establish answerability, and
validation, abstention and source review still apply. This fallback is deliberate,
not a guarantee that every numeric passage has its header. Diagnostic span offsets
refer to the supplied passage after contact omission, not whole-document or
unfiltered-chunk positions.

### Targeted v13 live checks and v14 fact-selector repair — 2026-10-25

The IREN capacity `ask` succeeds. It returns 21 EH/s installed for October
month-end and, separately, 19.9 EH/s average operating hashrate for October's
monthly average. Both claims cite the November 6, 2024 update (`IREN-S2:00001`,
accession `0001878848-24-000029`) and preserve the operational basis. The response
used 6,504 tokens, took 8.224 seconds end to end and has a configured-rate cost
estimate of USD 0.0009272. This is a single response, not a run-level measure or
invoice.

The ASTS net-loss `ask` completed generation with the correct signed value,
`-171.946 USD million`, for Q3 2024 under US GAAP. Local validation abstained at
`financial_consistency / filing_source_value_mismatch`. The cited value row
(`ASTS-S4:00013`) and its period/scale header (`ASTS-S4:00010`) belong to the
same Q3 2024 Form 10-Q, accession `0000950170-24-127101`. The failure was a gap
in our deterministic fact selectors: they recognized the same value from the
earnings release and a 10-Q note table, but not this 10-Q face-statement table.
The filing identity check correctly stopped a value extracted from one filing
from being attributed to another; it did not yet know that the cited S4 table
independently reports that value.

`fact_specs.json` now includes a guarded S4 selector across chunks
`ASTS-S4:00010–00013`. Its guards require the statement title, USD-thousands
scale, Q3 period header, and complete labeled row. The prompt and strict
source-identity/citation validators are unchanged. One offline replay now
extracts the same fact from both filing identities and accepts the exact S4
header/row citations. This verifies the local selector and validator path; it
does not establish that a new live model response will pass. The run identifier
is now `filing-answers-v14`, so future saved results distinguish this selector
repair from v13. The pre-edit spec
snapshot matches the saved v12 code hash, and the exact change plus safe result
summaries are in ignored `local/repair_checks/v13-2026-10-25/` artifacts.

The ASTS request used 7,774 tokens, took 7.031 seconds end to end and has a
configured-rate estimate of USD 0.001011. These are one-response estimates, not
invoices. Repeat only the ASTS `ask` once to confirm the selector repair against
a live generation. If that succeeds with exact S4 support, run one full
development evaluation; IREN does not need to be rerun. Do not tune on the
exposed holdout.

The `ask` command prints the answer JSON to the terminal and does not save an
answer run. A full `evaluate --run-id ...` writes the paired run to
`local/answer_runs/<run-id>/run.json`.

### v14 ASTS live recheck — 2026-10-25

The repeated ASTS ask now returns `answered` with one claim: `-171.946 USD
million` net loss attributable to common stockholders for 2024-07-01 through
2024-09-30, under US GAAP. It identifies AST SpaceMobile / ASTS and cites the
complete value row in `ASTS-S4:00013`, plus the USD-thousands and Q3 period
headers in `ASTS-S4:00010`. Those passages belong to the same 10-Q accession
`0000950170-24-127101`.

The response has the same evidence hash as the earlier abstention. A local review
rebuilt that frozen evidence pack and validated the schema, every exact quote and
all source metadata for the structured and RAG answers. It also confirmed that
the RAG citation locates the extracted fact from the same filing. These checks
confirm the selector repair for this question; they do not establish answer
quality across the development set.

The live answer used 7,748 tokens, took 6.511 seconds end to end and has a
configured-rate cost estimate of USD 0.000998, not an invoice. Because this was
an `ask`, the answer is printed in the terminal and is not saved as an answer
run. A safe summary is in ignored
`local/repair_checks/v14-2026-10-25/review-live-asts-v14.md`. The next check is
one full 21-case development evaluation under v14; keep the exposed holdout out
of tuning.


### v14 full development run review — 2026-10-25

The saved run `local/answer_runs/live-development-2026-10-25-v14-01/run.json`
uses the 21 development questions and the unchanged `sec-qa-pilot-v1` corpus.
Structured answers: 14 answered, 7 abstained. RAG: 15 answered, 4 errors, and
2 abstentions. The two RAG abstentions (IREN-QA-10 and ASTS-QA-09) match the
benchmark's expected insufficient-evidence responses.

All 21 provider generations completed. Each of the four RAG errors stopped in
local `citation_reconstruction` with `citation_quote_not_verbatim`. Expected
source presence was true for all four; the selected passage was also in the
packed evidence. This points to an unverified quote, not a failed retrieval or
network request. The safe diagnostic could not locate the quote even after
ignoring whitespace, case, and smart punctuation. Rejected quote text is not
stored, so we cannot tell whether it came from the wrong chunk, combined
noncontiguous text, or otherwise differed from the source. Exact-match
validation remains in force; accepting a fuzzy match could turn an unsupported
claim into a citation.

A read-only integrity pass checked 37 citations across the 15 accepted RAG
answers: each quote is an exact substring of its cited packed passage and its
source metadata matches the frozen evidence bundle. This only verifies the
citation record; it does not prove semantic support. Numeric auto-grading fully
passed 9 of 15 cases with numeric targets. Two accepted answers had additional
benchmark-target gaps (IREN's equivalent-revenue alias and the expected
percentage alongside an absolute cost decrease); four errors have no numeric
grade. These values are coverage/completeness checks, not answer-accuracy
scores. Formal paired source review remains pending.

RAG median end-to-end latency was 6.224 seconds; the configured-rate cost
estimate was USD 0.0189000 for the run, not an invoice. Raw retrieval-source
recall remains separate at 21/21 cases with any expected source and 20/21 with
all expected sources. RAG answered five narrative/interpretive questions the
structured prototype abstained on; its four quote errors are on questions the
structured method answered. This single live run does not establish which
method is better overall.

Next, source-review accepted claims and their period/unit evidence before
changing prompts or validators. Keep exposed holdout cases out of tuning and
keep the local pilot fixed. The safe per-case review is in ignored
`local/repair_checks/v14-2026-10-25/review-live-development-v14.md`.


### Original plan: retrieval-only embeddings comparison (completed below)

The original Step 3B plan was to compare `bm25-ticker-v1` with
`top10-table-context-v4` against an embeddings-plus-lexical method on the same
21 development questions and unchanged `sec-qa-pilot-v1`. That retrieval-only
comparison and the later paired answer-generation assessment have now run; see
the completed results below and the [hybrid retrieval study guide](../../.codex/marketlens-study-guide.md#hybrid-retrieval-study-guide).
The v14 exact-quote errors occur after retrieval, so embeddings alone are not a
direct fix for them. The current work focuses on improving the active hybrid
retrieval-to-evidence-to-answer direction while keeping the lexical result as a
frozen comparison reference. Permanent encoder, provider, runtime and vector
database decisions remain open.

### Retrieval-only hybrid comparison — 2026-10-25

Stage 1 is implemented in `hybrid_retrieval.py` and `retrieval_compare.py`.
It keeps the 12-document/3,922-passage pilot, the same 21 development questions,
`bm25-ticker-v1`, `top10-table-context-v4`, and the answer prompt/schema/selectors/
validators fixed. No SEC rebuild, corpus expansion, exposed-holdout tuning,
answer generation or vector database is involved.

The scoped encoder is `sentence-transformers/all-MiniLM-L6-v2`, immutable revision
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41` (Apache-2.0). The isolated dependency
lock is `requirements-embedding-experiment.txt`; tested on Python 3.12/macOS arm64.
CPU float32 inference uses deterministic evaluation, one thread, batch 32 and
L2 normalization. Overlength passages use 254-content-token windows with overlap
64 and maximum similarity per original passage. The 4,376 index views retain
the original 3,922 IDs; they do not change the corpus. Overlength queries fail.
Ticker-filtered cosine top 50 and lexical top 50 combine with equal reciprocal-
rank fusion (`k=60`) into ten hits, then use the unchanged 16,000-byte packer.
This encoder/runtime is an experiment setting, not the product stack.

`retrieval_relevance.json` has 39 source-reviewed groups: 20 financial facts,
11 narrative groups, two qualifications and six source-scope groups. Requirements
combine with AND; verified alternatives combine with OR, with every exact span
required within an alternative. Row, period/year-column, scale, basis and filing
vintage are checked separately. SEC filing/acceptance and benchmark-vintage dates
do not replace unknown issuer publication dates. A source-only verifier did not
see method outputs; the author had historical lexical exposure. Worker inputs
contain only a query handle, ticker and question; labels never enter ranking.

Latest run: ignored `local/retrieval_runs/hybrid-development-2026-10-25-v2/run.json`.
Every lexical evidence pack exactly matches v14.

| Retrieval measure | Lexical + table | Hybrid + same packer |
| --- | ---: | ---: |
| Raw top-ten: any expected source | 21/21 | 21/21 |
| Raw top-ten: all expected sources | 20/21 | 20/21 |
| Raw complete financial value rows | 12/14 | 8/14 |
| Raw rows with applicable period/scale | 11/14 | 6/14 |
| Packed rows with applicable period/scale | 14/14 | 13/14 |
| Packed complete financial requirements | 13/14 | 12/14 |
| Packed complete narrative requirements | 4/5 | 3/5 |
| Packed all required evidence, answerable cases | 17/19 | 15/19 |
| Packed answerable requirement groups | 30/32 | 27/32 |
| Complete abstention source-scope/context | 0/2 | 1/2 |

These are **annotated evidence-presence results**, not answer accuracy or an
abstention-quality grade. Scope checks do not establish universal absence.
All 21 rankings and packs differ. Source inspection found two complete-case
losses and no newly complete answerable case: hybrid omits the Q3 ARR definition
NBIS-S1:00007 under the byte cap, and misses original October installed/average
hashrate evidence IREN-S2:00001. The later-release ARR definition and different
update vintages are not substitutes for the requested source. Hybrid adds useful
hardware-profit limitations for the unanswerable IREN question and another ASTS
scope source; neither establishes the missing customer-level calculation.
The remaining lexical completeness gaps are Bitcoin pool-fee qualification and
explicit wholesale service terms. This slice does not modify the frozen baseline
to repair them. Evidence presence also does not measure distracting/conflicting
additional passages or generated factual support.

**Post-run annotation correction:** the original frozen-label run (`...-v1`)
scored 16/19 versus 14/19. Source inspection then found a valid GAAP statement
at IREN-S1:00676 omitted from the labels. A source-only verifier approved adding
the same alternative for both methods. Revision 2 records that correction; the
original labels/results and code snapshot remain local. Ranked IDs and full
packs are identical between v1/v2 for both arms. Revised counts are development
diagnostics, not preregistered or blind results; the two-case loss is unchanged.

Each arm has 21 unique questions, one untimed warmup per question, then five
timed repetitions (105 correlated observations), alternating arm order. Warm
query time includes query encoding, retrieval/record validation and packing;
verification/model loading/index creation are separate. V2 median/p95 were
5.535/22.336 ms lexical and 12.185/29.064 ms hybrid. Peak isolated-process RSS was
90.5 MiB lexical and 554.7 MiB cached hybrid. Initial passage encoding took
85.872 seconds with a 798.3 MiB peak; v2 reuses that exact cache. Model/index/runtime
directories occupy about 87.3/7.2/784.6 MiB. OS file-cache state is uncontrolled.
Embedding API and answer-generation spend are USD 0; local hardware cost is
unmeasured.

The predeclared practical gate requires two newly complete cases, or closing the
sole baseline gap, with no complete-case/critical-requirement/scope loss and no
unknown labels. It is a pilot heuristic, not a significance test. **The gate
fails: retain lexical-plus-table for this pilot.** A generation experiment could
study ordering robustness, but would not validate an observed retrieval gain;
evidence regressions and the separate v14 post-retrieval quote failures make paid
generation unhelpful for this decision. No paired generation ran. Citation and
abstention checks remain strict; formal v14 answer grading is still pending.
This does not establish that other embeddings/fusion settings are ineffective.

### Weighted hybrid tuning — 2026-10-25

The hybrid path remains the active direction for further retrieval work. This
follow-up tested three predeclared lexical/dense RRF weight pairs on the same
21 development questions, with the same 12-document/3,922-passage pilot,
lexical-plus-table baseline, evidence limits and source labels. Each setting ran
five timed repetitions. The lexical evidence pack matched the saved v14 pack
for all 21 questions in every comparison. The 0.50/0.50 run also exactly
reproduced all 21 rankings and pack hashes from the previous v2 hybrid run.

| Method | Raw expected sources, any/all | Packed expected sources, any/all | Raw financial rows / rows with headers | Packed financial rows / rows with headers | Packed complete answerable cases | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Lexical + table | 21/21; 20/21 | 21/21; 20/21 | 12/14; 11/14 | 14/14; 14/14 | 17/19 | Reference |
| Hybrid 0.75 lexical / 0.25 dense | 19/21; 18/21 | 21/21; 20/21 | 10/14; 8/14 | 13/14; 13/14 | 16/19 | Fail |
| Hybrid 0.50 lexical / 0.50 dense | 21/21; 20/21 | 21/21; 20/21 | 8/14; 6/14 | 13/14; 13/14 | 15/19 | Fail |
| Hybrid 0.25 lexical / 0.75 dense | 18/21; 16/21 | 19/21; 17/21 | 6/14; 6/14 | 11/14; 11/14 | 14/19 | Fail |

No setting added a newly complete answerable case, and all three lost critical
evidence. At 0.75/0.25, the IREN-QA-04 hybrid top ten dropped lexical rank 3
passage `IREN-S2:00001`, which contains the required original October installed
and average hashrate figures; other-vintage IREN passages entered the top ten.
The 0.50/0.50 setting also dropped the Q3 ARR definition passage for NBIS-QA-05.
At 0.25/0.75, the original October cost-vintage distinction in IREN-QA-08 and
the Bitcoin qualification in IREN-QA-03 were also lost. This shows why having a
lexical arm in the RRF formula does not guarantee that every useful lexical hit
survives the final top-ten cutoff.

| Setting | Lexical median / p95 | Hybrid median / p95 | Peak RSS: lexical / hybrid |
| --- | ---: | ---: | ---: |
| 0.75 / 0.25 | 5.67 / 22.61 ms | 12.75 / 29.99 ms | 97 / 716 MiB |
| 0.50 / 0.50 | 5.76 / 22.60 ms | 12.26 / 29.71 ms | 107 / 550 MiB |
| 0.25 / 0.75 | 5.62 / 22.37 ms | 12.26 / 29.16 ms | 107 / 589 MiB |

The new code-hash-specific cache required one cold passage encoding of 84.46
seconds. Its vectors occupy about 7.6 MB; the pinned model files occupy 91.6
MB and the isolated environment 822.7 MB. The later settings reused that cache.
These are local measurements; CPU cost is not converted to dollars and OS cache
state is uncontrolled. No embedding API or LLM call occurred.

Three fixed paraphrase probes—one per company—were recorded as unlabeled
diagnostics. All three changed ranking and packed passage IDs under equal fusion.
They were not graded, did not influence setting selection and do not establish
paraphrase quality. Paired answer generation was not run: the retrieval gate
failed, and the known v14 quote errors occur after retrieval. An answer study
could later test ordering sensitivity, but it would not validate a retrieval
gain on this evidence. The existing lexical-plus-table path remains the
measured default/reference; hybrid remains the active development direction.
No permanent model/provider/database was selected.

The immutable reports are ignored under
`local/retrieval_runs/hybrid-tune-2026-10-25-v1/`; its `tuning.json` references
three child comparisons and the separate diagnostic probe report. The selector
returns no candidate for answer follow-up because none passed the existing
evidence gate.

### Replay this experiment

Keep the immutable corpus and ignored model directory/`model-artifacts.json`
with the saved runs. Package/model acquisition is explicit setup; comparison
workers cannot download and block Python socket connections. The comparison has
no embedding or answer API transport. Reinstall the isolated runtime if needed:

```sh
python3 -m venv evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv
evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python -m pip install -r evaluation/filing_corpus/requirements-embedding-experiment.txt
```

Setup downloaded only `model.safetensors`, `config.json`, `tokenizer.json`,
`tokenizer_config.json`, `vocab.txt`, `sentence_bert_config.json`, `modules.json`,
`1_Pooling/config.json`, `special_tokens_map.json` and `README.md` from the pinned
revision with `huggingface_hub.snapshot_download(token=False)`. The ignored
manifest records model/revision/license and every file's SHA-256/size. Preserve
it for replay, or explicitly reacquire the same public artifacts and regenerate
that manifest. No filing/question upload occurs. The loader checks artifact
hashes and every installed dependency version. Missing runtime/artifacts yield
an unavailable hybrid arm; corrupt inputs/cache yield an error. There is no
fallback encoder/provider or fixture-derived quality score.

```sh
python3 evaluation/filing_corpus/retrieval_compare.py validate-labels
python3 evaluation/filing_corpus/retrieval_compare.py baseline --run-id my-lexical-replay-01
evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python evaluation/filing_corpus/retrieval_compare.py compare --run-id my-hybrid-replay-01
evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python evaluation/filing_corpus/retrieval_compare.py tune --run-id my-hybrid-tune-01
evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python -m unittest discover -s evaluation/filing_corpus -p 'test_*.py' -v
```

Use fresh run IDs; artifacts are never overwritten. Runs retain input/code/label
hashes, original locators/scores, packed evidence, all timing observations,
resources, gains/losses and the generation gate. Raw ranked contact text is not
duplicated; original text stays in the frozen corpus. V14 parity first validates
the saved run/evidence checksums. `source-review-root.json` records all 21
differences and the initial-encoding reference. Cache validation ties normalized
vectors to ordered IDs/text hashes, model artifacts, runtime/window policy and
implementation, rejecting stale, non-finite or malformed vectors.

### Conditional lexical floor: completed development comparison

The opt-in `--floor-policy query-coverage-v1` changes which of the first
`--lexical-floor` lexical hits receive protected positions. A candidate is
protected only when the question has at least two distinct non-stopword terms
from the existing tokenizer and that candidate contains at least half of them.
Each candidate is checked separately. Only the first requested lexical hits
are eligible; a stronger passage farther down the lexical list does not gain
a protected position. The rule and 0.5 threshold were chosen before this
comparison and are a literal-overlap heuristic, not calibrated relevance
confidence. No benchmark labels or expected answers enter the search path.

With a requested floor of three, the effective floor can be zero, one, two,
or three. A rejected candidate remains in the ordinary fusion candidate pool.
The remaining slots use the same weighted lexical/embedding fusion. Thus
removing protection does not switch off lexical scoring. When there are no
lexical candidates, dense candidates fill the result list as before. Neither
this rule nor a high embedding rank establishes sufficient evidence to answer.

`HybridIndex.search_with_diagnostics()` returns the ranked passages plus the
requested/effective floor, reason, and each eligible candidate's term-coverage
fraction. `search()` retains its list-returning interface. Reports save those
decisions even when no passage is returned, summarize effective-floor counts,
and check their determinism across five repetitions. `fixed` remains the
default policy; existing weighted-fusion and fixed-floor experiments remain
available.

Run these sequentially from the project root in the same terminal. Continue
to the comparison commands only if the full suite ends in `OK`:

```sh
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
"$MARKETLENS_PYTHON" -m unittest discover -s evaluation/filing_corpus -p 'test_*.py' -v
"$MARKETLENS_PYTHON" evaluation/filing_corpus/retrieval_compare.py compare --run-id hybrid-conditional-control-2026-10-25-v1 --lexical-weight 0.75 --dense-weight 0.25 --lexical-floor 3 --floor-policy fixed
"$MARKETLENS_PYTHON" evaluation/filing_corpus/retrieval_compare.py compare --run-id hybrid-conditional-2026-10-25-v1 --lexical-weight 0.75 --dense-weight 0.25 --lexical-floor 3 --floor-policy query-coverage-v1
```

The two comparison commands each include the frozen lexical/table arm, use the
same 21 development questions, and write immutable reports under
`local/retrieval_runs/<run-id>/`. Use a new run ID to repeat a command. The first
hybrid run after a code-version change may re-encode the local embedding cache;
this does not rebuild the frozen corpus. Compare warm query timing separately
from cold setup, and check cache-hit status before comparing lifetime peak RSS.
All commands use local computation; they make no LLM or embedding API calls.

The user completed both runs and the full suite: 120 tests passed in 4.357 s.
Both hybrid variants packed 17/19 complete answerable cases, 14/14 financial
rows with headers, and 1/2 complete expected-abstention scope cases. The
lexical/table baseline scored 17/19, 14/14 and 0/2. Conditional protection was
zero for eight questions, one for five, two for one and three for seven.
Raw expected-source any/all presence declined from fixed 20/21 and 19/21 to
conditional 19/21 and 18/21, while packed presence stayed 21/21 and 20/21 for
both. No answerable case became newly complete, so the evidence gate failed.

Warm hybrid median/p95 query-plus-pack time was 11.795/29.310 ms fixed and
11.619/29.220 ms conditional, compared with lexical medians of 5.580–5.669 ms.
The fixed run encoded vectors for 82.571 s and peaked at 759.0 MB RSS; the
conditional run used the cache and peaked at 550.8 MB. Different cache states
prevent a fair lifetime-memory comparison. These small timing differences do
not establish a conditional-floor speed improvement. No paid API call occurred.
Run/code hashes and v14 pack parity passed. See the ignored review and matching
source snapshot at `local/retrieval_runs/hybrid-conditional-review-2026-10-25-v1/`.

### Completed passage embedding-window comparison

`--passage-window-tokens` now accepts 254 (the existing view) or 128. A token is
a piece of text used by the encoder, not necessarily a whole word. The smaller
view embeds shorter overlapping pieces, then still returns the original
passage ID using the same maximum-similarity aggregation. All source tokens are
covered with the existing 64-token overlap. Queries keep their full 254-token
capacity; stored passages, final top-ten limit and 16,000-byte pack budget do
not shrink or change.

This experiment targets precise statements within longer passages. The two
remaining answerable gaps are IREN's mining-pool-fee qualifier and ASTS's
wholesale-service description. Diagnostics show that the required passages
rank below the final ten; two ASTS alternatives are also omitted as neighboring
context near the pack budget. Shorter embeddings may improve their rank, but
do not guarantee recovery or fix packing priority. More windows can increase
encoding time, vector storage and maximum-score length bias. The same pinned
model/runtime remain scoped settings, and no database is introduced.

Keep fixed floor three and 0.75 lexical / 0.25 dense weights for both runs to
change only the embedding view. The conditional policy remains available.
Run the suite first, then these two comparisons if it ends in `OK`:

```sh
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
"$MARKETLENS_PYTHON" -m unittest discover -s evaluation/filing_corpus -p 'test_*.py' -v
"$MARKETLENS_PYTHON" evaluation/filing_corpus/retrieval_compare.py compare --run-id hybrid-window254-control-2026-10-25-v1 --lexical-weight 0.75 --dense-weight 0.25 --lexical-floor 3 --floor-policy fixed --passage-window-tokens 254
"$MARKETLENS_PYTHON" evaluation/filing_corpus/retrieval_compare.py compare --run-id hybrid-window128-2026-10-25-v1 --lexical-weight 0.75 --dense-weight 0.25 --lexical-floor 3 --floor-policy fixed --passage-window-tokens 128
```

Each view has a separate validated local embedding cache; both first runs may
encode vectors. This derives new index views without rebuilding or expanding
the fixed 12-document/3,922-passage corpus. Reports and printed summaries record
the chosen view, window count, setup/encoding time, cache state, query latency
and process memory. The user completed the suite (125 tests passed in 4.280 s)
and both comparisons. Raw expected-source any/all presence improves from
20/21,19/21 at 254 to 21/21,20/21 at 128. Raw financial row/header completeness
improves 9/14 to 10/14 and raw answerable completeness 10/19 to 11/19:
ASTS-QA-04 cash is now directly retrieved. Table packing already recovered that
evidence, so final packs remain 17/19 complete answerable, 14/14 financial
row/header and 1/2 complete abstention scope. The gate remains false; no LLM
comparison ran.

The 128 view creates 8,279 vectors versus 4,376 without changing stored
passages. Warm median/p95 query-plus-pack time was 13.410/30.796 ms at 128 and
12.349/30.150 ms at 254; lexical medians were 5.603–5.659 ms. Both used cold
caches: encoding 80.453 versus 82.907 s, setup 83.960 versus 86.292 s, and peak
RSS 755.6 versus 872.7 MB, respectively. Single-process observations do not
establish a general memory benefit. Matching code snapshots and reviews are at
`local/retrieval_runs/hybrid-window-review-2026-10-25-v1/`.

### Completed context-priority comparison and next anchored-context test

The optional `--packing-policy context-first-v1` changes only hybrid packing
order. It attempts the first raw hit and existing table/header/basis evidence,
then raw hits two and three, then ordinary same-source neighboring chunks in
their existing order, then the remaining raw hits. More context can fit before
lower-ranked raw hits consume the budget. Every candidate remains eligible;
the source collection, raw top ten, original text, provenance, atomic table
groups, 16,000-byte evidence cap and 32,000-byte request cap stay fixed. A new
pack version identifies the option. Default `baseline` packing still reproduces
all 21 v14 lexical evidence packs exactly.

The user completed 130 tests in 5.457 seconds and both real comparisons
(`hybrid-pack-control-2026-10-25-v1` and
`hybrid-context-first-2026-10-25-v1`). Run hashes, matching source hashes and v14
lexical parity checked before edits. Raw ranked records are identical. Packed
answerable evidence rose 17/19 to 18/19, narrative completeness 4/5 to 5/5, and
answerable requirement groups 30/32 to 31/32. Financial row/header coverage
stayed 14/14, financial completeness 13/14, and expected-source presence
21/21 any and 20/21 all. These are evidence measures, not answer accuracy.

ASTS-QA-06 gained its wholesale-through-mobile-network-operators context from
ASTS-S3:00060. However, complete expected-abstention scope fell 1/2 to 0/2.
IREN-QA-10 lost ranked IREN-S2:00008 while both :00007 and :00009 were packed.
ASTS-QA-09 also lost one scope-source requirement, though it was already
incomplete. Scope labels track supplied relevant evidence, not proof that an
answer is absent or that a model abstained correctly. The gate's empty loss
lists compare against lexical/table (already 0/2 scope), not the hybrid control.
One newly complete answerable case is below the existing two-case threshold;
the gate remains false and paid generation is not authorized.

Both hybrid runs used the cached 8,279 vectors. Median/p95 query-plus-pack time
was 11.575/29.121 ms for control and 11.854/29.735 ms for context first; process
peak RSS was 541.1/549.2 MB and setup 4.267/3.546 seconds. Lexical medians were
5.547–5.615 ms. These local measurements do not establish a speed improvement;
API calls and API cost were zero. Matched pre-edit sources and diagnosis are
retained under ignored `local/retrieval_runs/hybrid-packing-review-2026-10-25-v1/`.

The new opt-in `context-anchored-v1` keeps the same first-three/table/basis
priority, then visits raw hits in rank order, attempting each later hit before
its own neighboring passages. Neighbor header priority is retained within each
hit's group, and shared candidates are deduplicated by the existing packer.
This changes ordering only, preserving the candidate pool, raw ranks, original
text, source boundaries, atomic table groups and both byte caps. It cannot
discover IREN-QA-03's distant fee note, which is outside that pool, and does not
guarantee a particular hit will fit. Existing policies remain available for a
controlled comparison. Gate output now names its reference, required gain and
failed conditions; its decision rule has not changed.

The user then completed both anchored comparisons. The submitted full-suite paste
lists 134 `ok` tests, but omits the unittest `Ran ...` / `OK` summary, so full
suite completion is unconfirmed. Both anchored run hashes validate against the
preserved pre-change source snapshot; raw hybrid top-ten records match, and
lexical packs still match v14.
Relative to context-first, anchored packing keeps answerable evidence at 18/19,
narrative completeness at 5/5, financial rows/headers at 14/14, and answerable
requirement groups at 31/32. It restores expected-abstention scope coverage from
0/2 to 1/2. IREN-QA-10's `IREN-S2:00008` is now packed and its qualifier passes;
ASTS-QA-06's wholesale explanation remains supplied and complete. ASTS-QA-09
still lacks the ASTS-S1 and ASTS-S2 scope sources. Scope results describe source
coverage only. The gate against lexical/table still fails: one newly complete
case versus two required, with paid generation unauthorized.

Warm hybrid median/p95 query-plus-pack time is 12.039/29.301 ms for the anchored
control and 11.879/28.733 ms for the anchored run; peak process RSS is 549.0/545.2
MB and setup 3.764/3.563 seconds. Both used cached 8,279 vectors with zero
encoding; API calls and API cost are zero. These timing differences do not show
a speed improvement. Run hashes and per-case evidence are in each ignored run
directory.

### Completed lexical-tail comparison: retrieval evidence gate passed

The user ran the anchored depth-10 control and the depth-15 lexical-tail
candidate on the same 21 development questions and fixed `sec-qa-pilot-v1`
release. Both run hashes validate against the current code, both preserve the
same 21 hybrid top-ten rankings and v14 lexical-pack parity, and both record 12
documents and 3,922 passages. The candidate adds lexical ranks 11–15 to the
packing candidates without changing top-ten retrieval or source-presence
metrics.

| Packed evidence | Lexical/table reference | Anchored depth 10 | Anchored lexical tail depth 15 |
|---|---:|---:|---:|
| Expected source presence: any / all | 21/21 / 20/21 | 21/21 / 20/21 | 21/21 / 20/21 |
| Financial rows with headers | 14/14 | 14/14 | 14/14 |
| Complete financial evidence | 13/14 | 13/14 | 14/14 |
| Complete narrative evidence | 4/5 | 5/5 | 5/5 |
| Complete answerable evidence | 17/19 | 18/19 | 19/19 |
| Expected-abstention scope evidence | 0/2 | 1/2 | 1/2 |
| Answerable requirement groups | 30/32 | 31/32 | 32/32 |

The candidate adds `IREN-S2:00008` to IREN-QA-03's evidence pack. Its
`bitcoin-net-pool-fees` requirement changes from FAIL to PASS. It preserves
ASTS-QA-06's `ASTS-S3:00060` wholesale explanation and IREN-QA-10's
`IREN-S2:00008` cost qualifier. No answerable requirement regressed against the
anchored control. ASTS-QA-09 remains an insufficient-evidence scope case; the
extra lexical passages do not supply its missing ASTS-S1 and ASTS-S2 scope
sources.

The unchanged gate now passes against lexical/table: IREN-QA-03 and ASTS-QA-06
are newly complete, with no lost complete cases or critical requirement losses.
This establishes relevant packed-evidence coverage on this development set; it
does not establish generated-answer correctness or actual abstention quality.

Warm hybrid median/p95 query-plus-pack time rose from 11.675/28.816 ms in the
anchored control to 13.796/32.192 ms with the lexical tail. Peak RSS rose from
537.9 to 556.6 MB. Both runs reused 8,279 vectors, required no passage encoding,
and made zero provider calls. The added lexical pass accounts for extra local
work; these single-machine measurements are not product-scale timings.

### Paired lexical-versus-hybrid answer harness

`qa_retrieval_answer_compare.py` now reads the frozen v14 lexical evidence packs
and the selected hybrid-tail packs, then sends each pack through the same
`rag_experiment.rag_answer()` prompt, answer schema and strict citation and
abstention checks. It adds no changes to the current `qa_compare.py` answer path.
Benchmark answer references are used only after generation for automatic grading
and source review. Retrieval scores and answer-quality scores are stored in
separate report sections. The report tracks answer status, automatic numeric and
citation checks, expected response/abstention, source-reviewed factual support,
company, period, accounting basis, units, answer latency, provider request
latency, tokens and cost. Each answer's model-input hash, usage, cost and provider
timing are cross-checked against its saved provider attempt. Valid quotes and
numeric matches do not by themselves prove semantic support; two source reviews
are required to complete the manual grades.

Before any answer generation, the offline validator checks run and source hashes,
v14 pack parity, the 21 development questions, the fixed 12-document/3,922-passage
release, exact pack text/locators and evidence/request byte limits. It excludes
the nine exposed holdout cases. It makes no network calls.

Run the focused offline checks from the repository root:

```sh
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
"$MARKETLENS_PYTHON" -m unittest discover -s evaluation/filing_corpus -p 'test_qa_retrieval_answer_compare.py' -v
"$MARKETLENS_PYTHON" evaluation/filing_corpus/qa_retrieval_answer_compare.py validate \
  --retrieval-run evaluation/filing_corpus/local/retrieval_runs/hybrid-prompt-coverage-refresh-v1
```

The focused tests exercise hash and pack integrity, fixed pair identity, budget
math, label isolation and offline answer-flow behavior. They do not measure answer
quality. The `validate` action loads the real saved run artifacts and reports
`network_calls: 0` when successful.

The scoped live configuration is for `gpt-6-luna`, at $0.10 per million input
tokens and $0.50 per million output tokens. Across 42 generations (21 questions
per arm), the configured maximum of 8,000 input and 2,400 output tokens per call
reserves up to $0.084; token-count fees are configured as $0. This is a ceiling
estimate, not a bill. The official [OpenAI API pricing page](https://developers.openai.com/api/docs/pricing)
shows these rates. On 2026-10-25, the user approved the 42-answer paid comparison
and chose to execute it in their terminal. The first run was saved as
`paired-development-2026-10-25-v1`, but both arms stopped at their first
token-count request with a transport error; no generation was dispatched. With
the documented certificate setting, `paired-development-2026-10-25-v2` completed
all 42 generations. The current next step is offline source grading below. The
`OPENAI_API_KEY` variable is absent in the agent environment;
availability in the user's terminal is separate. No prompt, citation, or
abstention check was weakened.

The user completed the full suite on 2026-10-25: **146 tests passed in 18.723
seconds**. This includes the lexical-tail and paired-harness tests. The following
validator output reported `status: valid`, 21 questions, 12 documents, 3,922
passages, both retrieval arms and zero network calls. Its retrieval-run hash is
`0294119761677a01dd7c6fbcff78d645009d8c2ad78d24dfe7af0e67bb8ba26c`, matching
the saved selected run. Current input validation also passes. Passing these
software checks does not establish generated-answer correctness; no paired LLM
answers were produced by either command.

Commands used for the two retrieval comparisons:

```sh
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
"$MARKETLENS_PYTHON" evaluation/filing_corpus/retrieval_compare.py compare --run-id hybrid-tail-control-2026-10-25-v1 --lexical-weight 0.75 --dense-weight 0.25 --lexical-floor 3 --floor-policy fixed --passage-window-tokens 128 --packing-policy context-anchored-v1
"$MARKETLENS_PYTHON" evaluation/filing_corpus/retrieval_compare.py compare --run-id hybrid-context-anchored-tail-2026-10-25-v1 --lexical-weight 0.75 --dense-weight 0.25 --lexical-floor 3 --floor-policy fixed --passage-window-tokens 128 --packing-policy context-anchored-tail-v1 --lexical-tail-depth 15
```

The two saved artifacts are under `local/retrieval_runs/` with these run IDs.
The full-suite command remains:

```sh
"$MARKETLENS_PYTHON" -m unittest discover -s evaluation/filing_corpus -p 'test_*.py' -v
```

The assistant previously ran the seven focused paired-harness tests; the user
has now supplied the full-suite `Ran 146 tests` / `OK` summary and validator
output. There is no need to repeat the full suite for this documentation update.

No exposed-holdout tuning, corpus rebuild, or LLM evaluation was part of the retrieval runs.

### First paired attempt: connection failure before generation

The saved run hash is
`1e0d506b07914facb928366fafa8905fa9ab142af3bf77debc711add81709a39`.
Run, source and evidence validation pass. Each arm records one token-count
attempt, zero generation requests, one error and 20 unavailable cases. The error
is `transport_failure` wrapping `URLError` at `token_count_request`; later cases
were skipped because the provider correctly halted after that error. This is not
a retrieval, answer-generation or quote-validation result. No paired answer
quality can be graded from it.

The displayed 61.407/23.901 ms times measure failed request handling, not model
answer speed. The zero token and cost totals sum the available values, but all
21 usage/cost records per arm are unknown. They do not independently establish
an invoice of zero. The USD 0.084 budget is a planned maximum, not observed spend.

The experiment Python currently loads zero trusted CA certificates by default;
its compiled CA file and directory are absent. The same missing-trust issue was
previously confirmed by a user HTTPS probe. Loading the existing macOS bundle
with `SSL_CERT_FILE=/etc/ssl/cert.pem` gives 128 trusted CA certificates, while
certificate verification and hostname checking remain enabled. This is the
leading diagnosis for the current transport failure; the run retained only
`URLError`, so the user's connection check below must confirm recovery.
[Python's SSL documentation](https://docs.python.org/3.12/library/ssl.html#ssl.create_default_context)
describes the default context's certificate verification behavior.

The earlier command omitted this already-known environment setting. This
documentation corrects that omission; the retrieval settings, prompt, model,
answer schema, strict validators and original failed artifact are preserved.

### Certificate recovery used for completed paired v2

Freeze the selected retrieval setting while measuring whether its better evidence
helps the LLM. Run the same 21 questions twice: once with the saved lexical packs
and once with the saved hybrid packs. The LLM drafts answers from those packs;
retrieval itself has already happened. Both arms use the same model, prompt,
answer schema, 16,000-byte evidence cap, 32,000-byte request cap and configured
8,000-input / 2,400-output token limits. Keep the nine exposed holdout questions
out of this comparison.

The public `gpt-6-luna` rates were checked again on 2026-10-25 against
[OpenAI Docs](https://developers.openai.com/api/docs/models/gpt-6-luna): USD
0.10/M input tokens and USD 0.50/M output tokens. The maximum reservation is
`42 * ((8000 * 0.10 + 2400 * 0.50) / 1000000) = USD 0.084`, including the
configured zero fee for each of the 42 token-count requests. The USD 0.50 total
cap is divided into USD 0.25 per arm. These are configured rate estimates;
account eligibility and token-count billing remain the saved local review's
declarations, not checks performed by this offline session.

The user approved this paid comparison on 2026-10-25 with the instruction
"Approve; I’ll run it". The user completed recovery of the same comparison as
`v2`, whose summary is recorded below. The following command is retained as the
execution record; `v2` now exists and must not be rerun with that ID. It sends each question and
its selected public SEC evidence to the OpenAI API, with `store: false` and no
tools. The agent environment has no API key; that does not establish whether the
user's terminal has one. This local check prints only key availability and makes
no network request:

```sh
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
"$MARKETLENS_PYTHON" -c 'import os; print("API key available" if os.environ.get("OPENAI_API_KEY") else "API key missing")'
```

Before the paid retry, run this connection check with the same interpreter and
certificate bundle. It performs only a TLS handshake to the API host: no HTTP
request, API key, evidence, token count or generation is sent. It does not test
API authentication or model access. It expects a direct network connection.

```sh
SSL_CERT_FILE=/etc/ssl/cert.pem "$MARKETLENS_PYTHON" - <<'PY'
import socket
import ssl

context = ssl.create_default_context()
try:
    with socket.create_connection(("api.openai.com", 443), timeout=10) as connection:
        with context.wrap_socket(connection, server_hostname="api.openai.com") as secure:
            print("TLS verification passed:", secure.version())
except OSError as error:
    print("TLS check failed:", type(error).__name__)
    raise SystemExit(1)
PY
```

For future connection diagnosis, share a failed TLS-check output before another
paid call. The user already completed the following `v2` invocation; it is not
the next command to run. Failed `v1` remains intact:

```sh
SSL_CERT_FILE=/etc/ssl/cert.pem "$MARKETLENS_PYTHON" evaluation/filing_corpus/qa_retrieval_answer_compare.py evaluate \
  --retrieval-run evaluation/filing_corpus/local/retrieval_runs/hybrid-context-anchored-tail-2026-10-25-v1 \
  --run-id paired-development-2026-10-25-v2 \
  --live --live-config evaluation/filing_corpus/local/luna-live-config.json
```

Allow several minutes; the runner prints its summary after completing the paired
run. It saves an immutable `run.json` under
`local/answer_pairs/paired-development-2026-10-25-v2/`. Share the output and run
path before starting another paid run. If it fails, share the safe error summary
before retrying: a failed or locally rejected answer may still have incurred a
provider charge.

After generation, inspect all paired answers and citations. Report factual support, citation
validity, company, period, accounting basis, units, filing vintage, expected and
excessive abstention, answer/provider latency and cost. Track exact-quote
validation errors separately from retrieval failures. Generate both review
templates only after the saved answer run exists. For this completed `v2`, both
filled source reviews have now been prepared; the following template commands
are reference instructions and must not be rerun over those existing files:

```sh
MARKETLENS_PAIR_RUN=evaluation/filing_corpus/local/answer_pairs/paired-development-2026-10-25-v2
"$MARKETLENS_PYTHON" evaluation/filing_corpus/qa_retrieval_answer_compare.py review-template --run "$MARKETLENS_PAIR_RUN" --reviewer root
"$MARKETLENS_PYTHON" evaluation/filing_corpus/qa_retrieval_answer_compare.py review-template --run "$MARKETLENS_PAIR_RUN" --reviewer verifier
```

The following is the historical v2 grading command, which the user already
completed. The current run's grading command appears in the newest result
section below. Both commands operate offline:

```sh
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
MARKETLENS_PAIR_RUN=evaluation/filing_corpus/local/answer_pairs/paired-development-2026-10-25-v2
"$MARKETLENS_PYTHON" evaluation/filing_corpus/qa_retrieval_answer_compare.py grade \
  --run "$MARKETLENS_PAIR_RUN" \
  --root-review "$MARKETLENS_PAIR_RUN/root-review.json" \
  --verifier-review "$MARKETLENS_PAIR_RUN/verifier-review.json"
```

The offline grade is complete. It provides the case-level findings used for the
next hybrid improvement. This is one sample per question and retrieval arm on
the development set; it cannot establish general performance on unseen
questions. Keep citation/abstention validators fixed while isolating the next
change.

### Completed v2 results and source-review findings

Saved run hash:
`8077c75080cfc6b96eb76d92cd60515ef424af00d2840caa191ef1dcfe2118af`.
Current source/input hashes, identical prompt/schema settings, provider attempt
records and evidence budgets validate. Each arm completed 21 token counts and
21 generations. No new corpus, question, embedding setting or holdout was used.

| Measure | Lexical evidence | Hybrid evidence |
|---|---:|---:|
| Accepted answers | 16/21 | 17/21 |
| Local answer-validation errors | 3/21 | 2/21 |
| Expected abstentions | 2/2 | 2/2 |
| Automatic numeric-case PASS | 9/15 | 9/15 |
| Automatic numeric-case UNKNOWN | 6/15 | 6/15 |
| Exact accepted citation occurrences checked | 57 | 66 |
| Median answer pipeline time | 6.135 s | 6.980 s |
| p95 answer pipeline time | 17.711 s | 14.950 s |
| Configured-rate cost estimate | USD 0.0189740 | USD 0.0192950 |

The combined estimate is USD 0.0382690, below the USD 0.084 planned reservation
and USD 0.50 configured cap; these rate calculations are not invoices. Hybrid
adds USD 0.0003210 and 0.845 s median answer time in this run. Twenty-one samples
per arm and one generation per question do not establish stable latency or
general accuracy differences. Retrieval timing remains the earlier separate
retrieval-only measurement.

Case inspection explains the apparent gain and the limitations:

- **NBIS-QA-07:** the only error-to-answer change. The correct input sentence is
  in both packs. Lexical assigns the Q3 167.0 value to a nine-month claim and is
  rejected. Hybrid correctly reports `167.0 / 387.6 * 100 = 43.086%`. The gain
  cannot be attributed to newly retrieved input facts in this single sample.
- **IREN-QA-04 and IREN-QA-07:** both arms fail exact-quote reconstruction after
  retrieval, despite supplied relevant evidence. Rejected drafts are not saved,
  so their precise quote mismatch is unknown. Both strict checks remain in force.
- **IREN-QA-03:** both state the correct 439 BTC count but omit the pool-fee
  qualifier. The lexical pack lacks the note; hybrid supplies `IREN-S2:00008`
  but does not use it. Source reviewers flag the omission while differing on
  whether to classify it as incomplete correctness, unsupported scope, or unknown
  given the lexical pack. Keep that disagreement visible.
- **ASTS-QA-06:** both answers support the MNO/existing-device route but omit
  LEO satellites and the wholesale distinction from the expected explanation.
  The LEO sentence is supplied to both; hybrid also supplies the explicit
  wholesale sentence at `ASTS-S3:00060`. Both reviewers mark the answer incomplete.
- **Numeric UNKNOWN does not always mean a wrong value.** IREN-QA-01 correctly
  converts 501.023 USD million but lacks the duplicate named `revenue_millions`
  target. Hybrid NBIS-QA-05 includes the supported multiplier 12 under
  `ARR annualization multiplier`, which the exact-name grader does not match to
  `annualization_multiplier`. Both ASTS-QA-07 and IREN-QA-08 give the correct
  absolute change but omit benchmark percentage targets; the prompt says not to
  add unrequested percentages, so this is also an evaluation-contract mismatch.
  Original scores and targets are preserved.
- **Abstention:** both reviewers regard the ASTS Vodafone-margin and IREN
  customer-profit refusals as safe given their bundles. Claim-metadata dimensions
  without an answer remain UNKNOWN, so strict overall grades can remain UNKNOWN
  even when the abstention check passes.

All 123 accepted citation occurrences match supplied text and full source
metadata. Literal matches do not prove answer completeness; the 46 accepted
claims and all 42 answer records received source review. The offline grade is
saved as `local/answer_pairs/paired-development-2026-10-25-v2/graded-712b710e6357.json`.
It reports **11 PASS / 1 FAIL / 9 UNKNOWN** overall cases in each arm. Hybrid has
one more reviewed/answered case, but the same number of overall PASS cases. Its
NBIS-QA-07 answer is a clear gain; IREN-QA-03 remains UNKNOWN after reviewer
disagreement, with the verifier marking the omitted pool-fee definition as a
completeness failure. Both arms fail ASTS-QA-06 completeness despite supporting
the clauses they state. The two expected abstentions pass the abstention
dimension, but `correct_abstentions: 0` counts only overall-PASS cases; it does
not mean the abstention decisions were judged wrong. The remaining UNKNOWNs
include errors, abstentions with inapplicable claim metadata, and automatic or
review disagreement. This grade is not an overall accuracy percentage.

The next isolated change adds a general instruction to cover each supported
part of the question and retain nearby qualifiers that alter a reported
measure. The LEO mechanism is in the first ASTS-QA-06 passage, the wholesale
passage is packed at the end, and the IREN fee qualifier is packed near the end;
the answerer does not fully use them. This targets answer completeness without
changing retrieval weights, evidence budgets, citation checks or abstention
rules. The saved v2 run remains the historical baseline. Because the prompt is
part of `rag_experiment.py`'s recorded source hash, create a fresh retrieval
comparison artifact before any new paired generation. No additional paid call
is authorized by this change.

### Current-code retrieval refresh (2026-10-26)

The user ran the source-hash refresh with the fixed candidate settings and
21 development questions. The saved artifact is
`local/retrieval_runs/hybrid-prompt-coverage-refresh-v1/run.json`, created at
`2026-10-25T15:38:22Z`, with run hash
`999b97fc6f4d778155ff7eb1f3d13476e701c562389f03b5d53b932c69f6210e`. Its
report hash and all current retrieval source hashes match, and lexical v14 pack
parity is true.

| Measure | Lexical plus table | Hybrid candidate |
|---|---:|---:|
| Expected sources present, any / all | 21/21 / 20/21 | 21/21 / 20/21 |
| Financial rows with headers | 14/14 | 14/14 |
| Complete answerable evidence | 17/19 | 19/19 |
| Complete financial / narrative evidence | 13/14 / 4/5 | 14/14 / 5/5 |
| Expected-abstention evidence scope | 0/2 | 1/2 |
| Query-plus-pack median / p95 | 5.70 / 22.34 ms | 13.34 / 32.48 ms |
| Peak process memory | 106.2 MB | 547.6 MB |

The existing evidence gate passes: IREN-QA-03 and ASTS-QA-06 become complete,
with no lost complete answerable case. The raw expected-source counts are
unchanged. All 21 lexical and all 21 hybrid rankings and evidence-pack hashes
match the previously reviewed selected run. The local encoder cache was used;
passage re-encoding, embedding API cost, and LLM calls were all zero. The saved
run's `source_review` field remains `PENDING`, so keep that metadata distinct
from its hash-valid measured results.

The displayed `FileExistsError` is explained by the run directory already
existing: `compare()` uses `mkdir(exist_ok=False)` to protect saved run artifacts.
The artifact is complete and the worker logs contain only model-library
warnings. Do not repeat the command with this same ID; use the saved results.

The user's earlier full suite reported 142 tests and two setup errors because
the historical paired-run hash no longer matched the edited prompt source. The
test fixture now validates the frozen run against its archived source snapshot
and still checks that current code rejects a stale hash. After that repair, the
user reran the full suite and reported **148 tests passed in 7.818 seconds**.
These tests verify offline code and data contracts; they do not establish answer
quality. The user then ran the offline paired-input validator on the refreshed
artifact: `status: valid`, 21 development questions, 12 documents, 3,922
passages, lexical and hybrid arms, and zero network calls.

The prompt-updated paired-answer run has completed. Its historical preflight
limits allowed 42 generation calls at 8,000 input and 2,400
output tokens each, plus 42 token-count requests. At the current standard
`gpt-6-luna` prices of USD 0.10/M input and USD 0.50/M output, the worst-case
reservation is USD 0.084; configured caps are USD 0.25 per arm and USD 0.50
total. The count-request fee is USD 0 in the reviewed local configuration.
Current rates were checked against the [official model page](https://developers.openai.com/api/docs/models/gpt-6-luna)
and [API pricing page](https://developers.openai.com/api/docs/pricing) on
2026-10-26. The user separately authorized and completed the invocation below;
retain it for reproducibility and do not repeat it with this existing run ID.
It used the same prompt, schema, evidence limits, citation checks and abstention
checks for both arms.

```sh
SSL_CERT_FILE=/etc/ssl/cert.pem "$MARKETLENS_PYTHON" evaluation/filing_corpus/qa_retrieval_answer_compare.py evaluate \
  --retrieval-run evaluation/filing_corpus/local/retrieval_runs/hybrid-prompt-coverage-refresh-v1 \
  --run-id paired-prompt-coverage-2026-10-26-v1 \
  --live --live-config evaluation/filing_corpus/local/luna-live-config.json
```

### Completed prompt-updated paired answers and reviews (2026-10-26)

`paired-prompt-coverage-2026-10-26-v1` has run hash
`054f619c21446ae8bae06d226637b57b060e8e169701385b16f7afeb65402d18`.
Run/source/input validation passes and all 42 token counts and generations
completed. The evidence pack hashes are identical to v2 for all 21 lexical and
all 21 hybrid cases. The only changed answer source file is `rag_experiment.py`,
whose diff adds the shared completeness instruction; schema, validators, model
and generation limits match the previous run. This is still one generation per
question and arm, so stochastic variation prevents a causal prompt-effect claim.

| Answer measure | Lexical evidence | Hybrid evidence |
|---|---:|---:|
| Accepted answers | 18/21 | 15/21 |
| Exact-quote validation errors | 1 | 2 |
| Financial value/period validation errors | 0 | 2 |
| Expected abstentions | 2/2 | 2/2 |
| Automatic numeric-target passes | 12/15 | 10/15 |
| Automatic citation-integrity passes, including abstentions | 20/21 | 17/21 |
| Median / p95 answer pipeline | 6.403 / 16.521 s | 7.286 / 15.053 s |
| Configured-rate cost estimate | USD 0.0186711 | USD 0.0190431 |
| Evidence bytes median / maximum | 15,128 / 15,547 | 15,264 / 15,616 |

The total estimated cost is USD 0.0377142, below the USD 0.084 reservation.
Hybrid adds USD 0.0003720 and 0.883 seconds median in this sample, with a lower
p95. Each latency distribution contains 21 answers. These are local run
measurements and configured-rate estimates, not stable product performance or
invoices. Neither an automatic numeric pass nor an accepted answer establishes
complete source support.

Compared with v2, lexical accepted answers increased 16 to 18 and hybrid accepted
answers decreased 17 to 15. Numeric-target passes increased from 9 to 12 and
9 to 10. The combined accepted count stayed 33 and the combined error count
stayed five. Retrieval evidence scores remain 17/19 lexical versus 19/19 hybrid;
they do not change because answer generation changed.

| Case / affected arm | Safe saved diagnosis | Evidence implication |
|---|---|---|
| ASTS-QA-07, both | Exact quote rejected; lexical hint `whitespace`, hybrid `not_located` | Detailed expense rows and headers are supplied; the rejection occurs after retrieval. |
| IREN-QA-07, hybrid | Exact quote rejected, hint `not_located` | The revenue rows, scale, periods and GAAP context are supplied. Lexical delivers the supported 167.652% growth. |
| IREN-QA-08, hybrid | October electricity cost tagged `2024-10 month-end`, conflicting with monthly `2024-10` fact | The 20,834 value is present; the generated temporal annotation treats a monthly cost metric as month-end. |
| NBIS-QA-07, hybrid | Q3 value 167.0 assigned to the nine-month period | The same source sentence provides Q3 167.0 and nine-month 387.6; lexical assigns them correctly and calculates 43.086%. |

Rejected quote/draft prose is not retained. The hints locate a class of mismatch,
not the exact missing characters, so do not infer a precise repair from them.
All 105 accepted citation occurrences (64 lexical, 41 hybrid) across 47 claims
reproduce packed text and source metadata. Five stored failure source spans have
valid reconstructable offsets. Both expected abstentions identify missing
customer-specific inputs without inventing values.

The targeted completeness issues persist. Both IREN-QA-03 answers say 439 BTC
without the pool-fee qualifier, although hybrid still packs `IREN-S2:00008`.
Both ASTS-QA-06 answer texts omit the explicit LEO and wholesale details; hybrid
now mentions a satellite constellation and cites the LEO sentence, which is a
partial improvement. Its wholesale passage `ASTS-S3:00060` is still unused.
Root and the separately authorized read-only verifier both flag ASTS completeness.
For IREN-QA-03, root marks completeness FAIL while the verifier marks UNKNOWN;
preserve that disagreement. Lexical numeric UNKNOWNs also include the duplicate
IREN-QA-01 `revenue_millions` label and IREN-QA-08's omitted reference percentage;
those do not by themselves prove incorrect delivered values.

Both review files are filled and tied to this exact run. Eleven source files
and a hashed inspection report are preserved under ignored
`local/answer_pair_reviews/paired-prompt-coverage-2026-10-26-v1/`.
The combined offline grade is complete and saved as
`local/answer_pairs/paired-prompt-coverage-2026-10-26-v1/graded-c9aeea3be7a0.json`.

| Overall grade | Lexical evidence | Hybrid evidence |
|---|---:|---:|
| PASS | 14 | 12 |
| FAIL | 1 | 1 |
| UNKNOWN | 6 | 8 |
| Denominator | 21 | 21 |
| Cases with a completed source review | 17 | 14 |

Both arms' only overall FAIL is ASTS-QA-06, where the answer leaves out material
parts of the question. IREN-QA-03 remains UNKNOWN in both because the reviewers
disagreed about the importance of the omitted pool-fee qualifier. All 28 lexical
claims and 19 hybrid claims pass claim-level source and metadata checks; those
claim checks do not make an incomplete answer complete. Each arm's two expected
abstentions pass the abstention dimension. `correct_abstentions: 0` means there
are no abstentions with an overall PASS verdict: other unasserted case-level
dimensions remain UNKNOWN, so this field does not mean the two refusals were
judged unsafe or incorrect.

Compared with paired v2, the overall PASS count changes from 11 to 14 lexical
and 11 to 12 hybrid. Current complete reviews cover 17 lexical cases and 14
hybrid cases. The UNKNOWN totals include both cases that cannot be fully graded
and incomplete or unresolved review dimensions. It is one generation per case
and model outputs can vary between runs, so the differences do not isolate a
causal prompt effect. Since
the evidence packs stayed identical, this result assesses answer construction
over those packs, not a new retrieval ranking. It does not show that hybrid
retrieval is worse at finding evidence; hybrid's separate evidence-coverage
result remains 19/19 versus lexical 17/19. The answer-quality grade, however,
does not favor hybrid in this sample.

The next work targets answer construction while keeping hybrid retrieval fixed.
The offline candidate below has not yet been measured with new LLM generations.
Do not tune retrieval weights from this answer grade. The grade command
previously run by the user was:

```sh
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
MARKETLENS_PAIR_RUN=evaluation/filing_corpus/local/answer_pairs/paired-prompt-coverage-2026-10-26-v1
"$MARKETLENS_PYTHON" evaluation/filing_corpus/qa_retrieval_answer_compare.py grade \
  --run "$MARKETLENS_PAIR_RUN" \
  --root-review "$MARKETLENS_PAIR_RUN/root-review.json" \
  --verifier-review "$MARKETLENS_PAIR_RUN/verifier-review.json"
```

### Offline answer-stage refinement candidate (2026-10-26)

After the grade, two post-retrieval safeguards were added to
`rag_experiment.py`:

- `source_financial_fact_index()` supplies a bounded, structured index of
  financial facts extracted only from rows already in the evidence pack. Each
  entry keeps value, unit, accounting basis, period and source/chunk IDs
  together. `generation_input()` tells the answerer to keep quarterly,
  year-to-date, monthly and month-end facts separate. This is intended to
  reduce period mix-ups such as the Q3/nine-month capex error and the monthly
  electricity-cost label error. Existing financial consistency checks still
  reject mismatched values and periods.
- `reconstruct_whitespace_quote()` returns an exact source substring only when
  there is one unique match and every non-whitespace character is identical.
  `rag_answer()` sends the reconstructed source text through the existing strict
  `citation()` check and stores only the safe span locator, not the model's
  altered quote. Punctuation, case changes, missing words, ambiguous matches and
  cross-passage combinations remain rejected.

This should repair a uniquely whitespace-formatted quote and make the known
financial periods more visible to the generator. The fact index is prompt
context, not a correctness guarantee; no live answer run has tested whether it
improves output. The model input includes a small amount of extra JSON, bounded
to 24 source facts and the existing 32,000-byte request limit. No corpus,
benchmark, retrieval setting, answer schema or validator was changed. No LLM
call was made.

Focused checks run by the agent passed: ten answer tests for fact-index
projection, exact whitespace reconstruction, invalid punctuation, wrong-period
rejection and compact evidence; one paired-harness check confirmed the fact
index contains no benchmark labels and stays inside packed chunk IDs; three
split-row/header checks passed; and one hybrid-pack regression check confirmed
the Q3/nine-month and original/revised IREN facts remain distinct. The first
focused attempt found an outdated byte-size assertion after adding the small
fact index and a mistyped test selector; the assertion now checks that the base
evidence projection remains compact and the full request stays below its bound,
and the corrected focused run passed.

The user has now run the full comparison for
`hybrid-answer-stage-refresh-2026-10-26-v1` and its offline paired-input
validation. Validation reports 21 development questions, 12 documents, 3,922
passages, both arms, and zero network calls. The fixed corpus and development
questions are unchanged.

The user supplied the same comparison output twice; this is one run, not two
independent repetitions. Literal source presence is unchanged at 21/21 any and
20/21 all expected sources for both arms. Before packing, hybrid has 11/14
financial rows and 10/14 row-plus-header groups versus lexical's 12/14 and
11/14. After context-anchored packing, hybrid reaches 14/14 rows, 14/14
row-plus-header groups, and 14/14 complete financial evidence groups. It
packs 19/19 complete answerable cases and 32/32 answerable requirements;
lexical packs 17/19 and 30/32. IREN-QA-03 and ASTS-QA-06 are the two newly
complete hybrid cases, with no previously complete answerable case lost.
These reviewed labels are bounded development annotations, not exhaustive
relevance judgments or answer-quality grades.

For this cached run, hybrid query median/p95 is 13.479/31.995 ms and peak RSS
is about 516 MiB, compared with 5.617/22.385 ms and about 94 MiB for lexical.
Hybrid setup takes 4.422 seconds on a cache hit and encodes no passages in this
run. The hybrid arm considers 8,279 windows. The fixed lexical floor is three
for all 21 questions; this run does not test a conditional floor. No embedding
API or answer-generation calls occurred. First-time local model/index setup is
not represented by these cache-hit timings.

The retrieval evidence gate passes. The user has now run the full suite after
the answer-stage changes: **156 tests passed in 8.137 seconds**. Do not change
retrieval weights based on these results alone. The next useful check is
whether the source-derived financial fact index and strict whitespace-only
quote repair improve answers on these same frozen packs. The suite command was:

```sh
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
"$MARKETLENS_PYTHON" -m unittest discover -s evaluation/filing_corpus -p 'test_*.py' -v
```

The retrieval comparison and offline `validate` have already passed; do not
rerun the existing run ID. The user approved one paired answer run after the
suite passed. It makes 42 answer generations plus 42 token-count requests
through OpenAI and sends the development questions and selected public SEC
passages. The current configured worst-case reservation is USD
0.084 under USD 0.50 aggregate and USD 0.25 per-arm caps. This estimate uses
the configured 8,000 input / 2,400 output token limits and reviewed local rates;
it is not a predicted invoice. This approval covers only the run ID below; the
assistant's current process does not have an API key, so the user runs it:

```sh
SSL_CERT_FILE=/etc/ssl/cert.pem "$MARKETLENS_PYTHON" evaluation/filing_corpus/qa_retrieval_answer_compare.py evaluate \
  --retrieval-run evaluation/filing_corpus/local/retrieval_runs/hybrid-answer-stage-refresh-2026-10-26-v1 \
  --run-id paired-answer-stage-refresh-2026-10-26-v1 \
  --live --live-config evaluation/filing_corpus/local/luna-live-config.json
```

Compare factual support, citation validity, company, period, accounting basis,
units, abstention, latency and cost separately from retrieval coverage. In
particular, inspect whether the new candidate prevents a Q3 value from being
assigned to a nine-month period and whether whitespace-only quote differences
are repaired without accepting changed wording. Keep all strict validation and
abstention checks enabled. Do not use the nine exposed holdout questions for
tuning.

### Paired answer-stage run and offline grade completed

The user completed `paired-answer-stage-refresh-2026-10-26-v1` against the
validated `hybrid-answer-stage-refresh-2026-10-26-v1` packs. The run hash is
`9f4d5850f0df07ca3e041fe75968e86eb340105e9b12b5e825e6d66ddd132bb3`. It passed
the saved-run integrity loader and uses the same 21 questions, model, rates and
evidence-pack hashes as `paired-prompt-coverage-2026-10-26-v1`. Only
`rag_experiment.py` differs in the answer/retrieval source manifests, due to
the source-derived financial fact index and strict whitespace-only quote
repair. This is still one generated answer per question, so observed changes
are promising diagnostics rather than causal proof. Against the previous
prompt-coverage pair, lexical moved from 18 accepted / 1 error / 2 abstentions
to 17 / 2 / 2, while hybrid moved from 15 / 4 / 2 to 17 / 2 / 2. The evidence
packs match the previous run for every question, so this compares the
answer-stage code on fixed evidence; one response per case cannot separate
code effects from generation variability.

Both arms produced 17 accepted answers, 2 validation errors and 2 expected
abstentions. Each had 10/15 automatic numeric passes, 19/21 citation-integrity
passes, and 19/21 expected-response passes; the two errors account for the
unknowns. Source-review correctness judgments are lexical 18 PASS / 1 FAIL / 2
UNKNOWN and hybrid 17 PASS / 2 FAIL / 2 UNKNOWN. The known completeness failures
are ASTS-QA-06 in both arms and IREN-QA-03 in hybrid, where the answer omits the
net-of-pool-fees qualifier. The hybrid answers now correctly distinguish Q3
from nine-month capex in NBIS-QA-07 and monthly reporting from month-end in
IREN-QA-08. Hybrid has exact-quote failures at ASTS-QA-02 and IREN-QA-04;
lexical has them at IREN-QA-03 and IREN-QA-04. Each has a `not_located`
diagnostic; rejected quote prose is not retained. The whitespace repair
recorded zero reconstructions in this live run, so its live effect remains
unmeasured. The user subsequently completed the offline grade; see “Offline grade and next answer-completeness candidate” below.

The configured-rate costs are USD 0.0197094 lexical and USD 0.0192755 hybrid,
USD 0.0389849 total, under the USD 0.084 worst-case reservation. Median/p95
answer latency is 8.141/13.289 seconds lexical and 7.902/12.616 seconds hybrid,
21 cases per arm. These small differences are a single run and do not establish
a reliable speed or quality advantage.


### Offline grade and next answer-completeness candidate

The user ran the offline grade for this pair. The saved report is
`local/answer_pairs/paired-answer-stage-refresh-2026-10-26-v1/graded-29e2adca09b3.json`,
bound to run hash
`9f4d5850f0df07ca3e041fe75968e86eb340105e9b12b5e825e6d66ddd132bb3`.
Overall verdicts are lexical **13 PASS / 1 FAIL / 7 UNKNOWN** and hybrid
**12 PASS / 2 FAIL / 7 UNKNOWN**, out of 21 each. These are case-level grades,
not accuracy percentages.

Both arms' one answer-level failure is ASTS-QA-06: the explanation leaves out
important mechanism/distribution details. Hybrid also FAILs IREN-QA-03: it gives
439 BTC but leaves out that the source reports the figure after mining-pool
fees. The hybrid evidence pack includes the fee note, so this is an answer
completeness failure rather than a retrieval miss. Lexical IREN-QA-03 is
UNKNOWN because its answer failed exact-quote validation and no accepted answer
was available to review. All 28 lexical and 29 hybrid included claims pass
reviewed source support and citation/company/period/basis/unit/vintage checks.

The UNKNOWNs have different causes: two quote-validation errors per arm, two
expected abstentions per arm whose whole-case grade remains UNKNOWN because
citation and claim metadata do not apply to an empty answer, and three automatic
numeric targets per arm that remain UNKNOWN despite the included answer claims
passing manual source review. Examples are a second revenue alias for IREN-QA-01
and unrequested percentage targets for ASTS-QA-07 and IREN-QA-08. Both expected
abstentions pass the abstention review dimension, and `excessive_abstentions` is
zero. `correct_abstentions: 0` counts only abstentions whose entire case grade
is PASS; it does not mean the two expected abstentions were judged wrong.

The next candidate tightens `generation_input()` with a final check that maps
question parts to answer sentences and supporting quotes, names evidence-backed
mechanism/channel roles for “how” questions, and keeps scope qualifiers beside
reported values. This is a shared answer-prompt change so lexical and hybrid
remain comparable. It does not alter retrieval, evidence limits, validators, or
abstention rules. The focused prompt test passed. The user has now created and validated a fresh
retrieval artifact under the updated answer-source hash; its results are recorded
below. The full suite after this prompt change is not included in the pasted
output and remains to be confirmed before generation.

Run these from the project root. If the full suite fails, stop and share its
output before running the retrieval comparison:

```sh
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
"$MARKETLENS_PYTHON" -m unittest discover -s evaluation/filing_corpus -p 'test_*.py' -v
```

The user has now run `hybrid-answer-completeness-refresh-2026-10-26-v1` and
validated its paired inputs. The report hash is
`ecb216e4b999f1538c7d3c60360aaa0103a4d295a19242159a42197e7f0e5f4e`. It uses
the unchanged 12-document/3,922-passage pilot and 21 development questions.
Validation returned `valid` with zero network calls. Its lexical and hybrid
ranked lists and evidence-pack hashes exactly match the prior refresh; the code
hash changed because the shared answer prompt changed. Do not rerun this ID.

Packed evidence remains lexical 17/19 versus hybrid 19/19 complete answerable
cases, with all 32/32 annotated hybrid requirements and no critical losses.
Both arms still have source presence 21/21 any and 20/21 all. Raw hybrid rows
are 11/14 versus lexical 12/14, while context-anchored packing restores hybrid
to 14/14 rows with headers. These are evidence metrics, not answer-quality
results. Hybrid cache-hit query time was 13.97 ms median / 33.02 ms p95, with
about 519 MiB peak RSS, 3.77 seconds index setup, zero passage re-encoding and
8,279 windows. Lexical was 5.59 / 22.74 ms with about 105 MiB peak RSS.

The user subsequently ran the full-suite command above: **156 tests passed in
8.176 seconds**. This confirms the covered offline contracts; it does not
measure live answer quality. At the time of this preflight, the retrieval
inputs were ready for a new paired answer comparison and a fresh approval was
required. The user later authorized and completed that pair; its results and
the offline grading command are recorded in the following section. Do not use
the nine exposed holdout questions for tuning or describe them as a blind test.

### Paired answer-completeness run and offline grade (2026-10-26)

The user completed `paired-answer-completeness-refresh-2026-10-26-v1` using
`gpt-6-luna` on the frozen evidence packs. Run hash:
`b009758f57c01054db2812b098581384f3ceeec9520a5cc3798ac94cf1c10f93`. The
current retrieval run remains `hybrid-answer-completeness-refresh-2026-10-26-v1`
with hash `ecb216e4b999f1538c7d3c60360aaa0103a4d295a19242159a42197e7f0e5f4e`.
All 21 evidence-pack hashes per arm match the previous paired run, so the
retrieved evidence did not change in this comparison.

| Measure | Lexical evidence | Hybrid evidence |
|---|---:|---:|
| Accepted answers | 15/21 | 15/21 |
| Post-generation validation errors | 4/21 | 4/21 |
| Expected abstentions | 2/2 | 2/2 |
| Automatic numeric PASS | 8/15 | 9/15 |
| Citation integrity PASS | 17/21 | 17/21 |
| Median answer time | 6.873 s | 6.771 s |
| p95 answer time | 19.854 s | 15.067 s |
| Configured-rate cost | USD 0.0197259 | USD 0.0196785 |

The combined configured-rate estimate is USD 0.0394044, below the USD 0.084
reservation. The hybrid arm has one additional automatic numeric PASS, but both
arms have the same accepted/error/abstention counts. Relative to the preceding
paired answer-stage run, accepted answers fell from 17 to 15 per arm and
validation errors rose from 2 to 4 per arm. One response per question cannot
show whether the prompt change caused that movement or whether it reflects
generation variation; this pair does not establish an answer-quality win.

Error stages are distinct from retrieval: lexical had four exact-quote
reconstruction failures (ASTS-QA-02, IREN-QA-03/04/07). Hybrid had two quote
failures (IREN-QA-04/07), an adjusted-opex growth mismatch on ASTS-QA-07
(30.794 versus 30.80255), and a numeric citation-row mismatch on IREN-QA-03.
Rejected answer prose is not saved, so do not infer its content. The two
abstentions (ASTS-QA-09 and IREN-QA-10) were judged appropriate. In accepted
answers, source reviewers found citation and company/period/basis/unit/vintage
metadata consistent. Both ASTS-QA-06 answers remain incomplete: neither states
the explicit wholesale arrangement shown in `ASTS-S3:00060`. The exact
completeness requirement for the percentage on IREN-QA-08 remains unresolved;
its USD 1,938/BTC decrease is supported, while the expected approximate
percentage is absent. Keep that review uncertainty visible.

The root and independent-verifier review files are now saved beside `run.json`
and match the run hash. The user ran the offline grade, saved as
`local/answer_pairs/paired-answer-completeness-refresh-2026-10-26-v1/graded-22566bf77ed2.json`.

| Overall verdict | Lexical evidence | Hybrid evidence |
|---|---:|---:|
| PASS | 11 | 12 |
| FAIL | 1 | 1 |
| UNKNOWN | 9 | 8 |
| Denominator | 21 | 21 |
| Cases with complete source review | 14 | 14 |

The only case-level verdict difference is ASTS-QA-02: lexical is UNKNOWN after
quote validation rejected its answer; hybrid is PASS. Both arms have the same
FAIL, ASTS-QA-06, because the answer omits the explicit wholesale business
relationship described in its filing. The hybrid arm gains one overall PASS
and one automatic numeric PASS, but this is one generation per case and does
not establish a general answer-quality advantage. All 23 lexical and 21 hybrid
included claims pass the claim-level support, citation, company, period,
basis, units and vintage checks.

UNKNOWN has several causes. The four error cases in each arm have no retained
answer text to review. The abstention answers for ASTS-QA-09 and IREN-QA-10
were judged appropriate, but their overall grades remain UNKNOWN because
empty answers have no citation/claim metadata to score; therefore
`correct_abstentions` is 0 even though neither abstention was excessive. Other
UNKNOWNs include automatic numeric targets not present in the answer and the
reviewer disagreement over whether IREN-QA-08 needs the benchmark's approximate
percentage in addition to the requested USD/BTC decrease.

The formal grade has completed. It made no new provider calls. The next
answer-stage candidate and user-run offline checks follow below.

### Next prompt candidate: preserve commercial distribution terms (2026-10-27)

The repeated ASTS-QA-06 omission is post-retrieval: the evidence pack contains
the relevant business-model text, but neither accepted answer states the
wholesale relationship. Hybrid quotes `ASTS-S3:00060` for the later MNO
marketing sentence, not the chunk's earlier wholesale sentence. I made one
shared prompt change in `rag_experiment.py`: when evidence explicitly defines
a commercial distribution model, preserve material terms such as wholesale
through a partner versus direct-to-customer. The instruction applies equally
to lexical and hybrid and does not change ranking, packing or validation.
This is a candidate behavior change, not a measured improvement.

The existing focused test was extended to assert the instruction. From
`evaluation/filing_corpus`, the command
`../../evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python -m unittest test_qa_answers.SourceTests.test_generation_instructions_require_supported_question_coverage -v`
passed **1 test in 0.862 seconds**.

### Current-source retrieval refresh after the commercial-term prompt

The user ran `hybrid-commercial-coverage-refresh-2026-10-27-v1` after the
answer prompt changed and validated its paired inputs. The run hash is
`711c8bdcbadcbf445da04b4324cac798ee6051d49ebd3b08df9f3798da56e384`. Validation
reported `valid` for 21 development questions, 12 documents, 3,922 passages and
both arms, with zero network calls. Do not rerun these commands with the same
run ID; saved run directories are immutable.

Expected-source presence is equal: 21/21 cases have at least one expected
source and 20/21 have all expected sources in both arms. The packed evidence
results are:

| Measure | Lexical + table | Hybrid |
|---|---:|---:|
| Financial rows with headers | 14/14 | 14/14 |
| Complete financial evidence groups | 13/14 | 14/14 |
| Complete narrative groups | 4/5 | 5/5 |
| Complete answerable cases | 17/19 | 19/19 |
| Annotated answerable requirements | 30/32 | 32/32 |

The hybrid gate passes: it newly completes IREN-QA-03's pool-fee qualifier and
ASTS-QA-06's wholesale-to-MNO evidence, without losing a previously complete
answerable case. Raw hybrid retrieval is weaker on some labels before packing
(11/14 financial rows and 10/14 row/header groups versus lexical 12/14 and
11/14); the context-anchored packer and lexical tail recover them. This is a
retrieval coverage result, not a generated-answer correctness result.

The run used a fixed lexical floor of three on all 21 queries, so it does not
test the conditional-floor policy. Cached local hybrid median/p95 retrieval
time was 14.00/31.83 ms and peak RSS about 537 MiB; lexical was 5.57/22.47 ms
and about 83 MiB. Hybrid setup took 3.81 seconds on a cache hit, with zero
passage re-encoding and 8,279 windows. Embedding API cost and LLM generations
were both zero. These are single-run local measurements, not service-level
latency estimates. The artifact's source review field remains pending for a complete pack-by-pack
method audit. A read-only comparison of all 21 case summaries found that all
ranked lists and packs differ, but only three annotated requirements changed:
IREN-QA-03 gains the pool-fee qualifier from `IREN-S2:00008`, IREN-QA-10 gains
the hardware-profit exclusion relevant to its abstention scope from the same
source passage, and ASTS-QA-06 gains the wholesale-to-MNO detail from
`ASTS-S3:00060`. The passages state those details directly; no annotated
requirement regressed.

The user subsequently ran the full offline suite: **156 tests passed in 8.229
seconds**. This verifies the covered code contracts, including conditional-floor
unit behavior, but not retrieval quality under a conditional floor or generated
answer correctness. The comparison itself used a fixed floor of three.

A paired answer-stage evaluation is now useful to check whether the evidence
gain carries through under the same prompt, model, schema, limits and strict
validators. A new run would make 42 `gpt-6-luna` generations and 42 token-count
requests over the same 21 development cases, sending the questions and selected
public SEC passages to OpenAI. At the configured standard rates ($0.10 per
million input tokens and $0.50 per million output tokens), the worst-case
reservation is **USD 0.084**; token counting is configured at zero cost. These
rates match the current [GPT-6 Luna pricing](https://developers.openai.com/api/docs/models/gpt-6-luna).
This is under the configured USD 0.50 aggregate cap. The user subsequently
approved and ran the paired evaluation below; no additional generation has
been run.

```sh
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
SSL_CERT_FILE=/etc/ssl/cert.pem "$MARKETLENS_PYTHON" evaluation/filing_corpus/qa_retrieval_answer_compare.py evaluate \
  --retrieval-run evaluation/filing_corpus/local/retrieval_runs/hybrid-commercial-coverage-refresh-2026-10-27-v1 \
  --run-id paired-commercial-coverage-2026-10-27-v1 \
  --live --live-config evaluation/filing_corpus/local/luna-live-config.json
```

### Paired commercial-coverage answer run (2026-10-27)

The user ran `paired-commercial-coverage-2026-10-27-v1` against the validated
retrieval run above. Saved run hash:
`1c044568c1634f2465612a80d0cf45d8d6fb3910c47ad5507a9b866fc562663a`; it is tied
to retrieval hash `711c8bdcbadcbf445da04b4324cac798ee6051d49ebd3b08df9f3798da56e384`.
The local `load_pair_run()` check validated the current run and its exact
21-question, 12-document, 3,922-passage inputs.

| Measure | Lexical | Hybrid |
|---|---:|---:|
| Answered after local validation | 17/21 | 16/21 |
| Post-generation validation errors | 2/21 | 3/21 |
| Expected abstentions | 2/21 | 2/21 |
| Automatic numeric PASS | 10/15 | 10/15 |
| Citation-integrity PASS | 19/21 | 18/21 |
| Expected-response PASS | 19/21 | 18/21 |
| Median / p95 answer time | 6.548 / 11.710 s | 6.643 / 14.204 s |
| Configured-rate cost estimate | USD 0.0199695 | USD 0.0198126 |

Combined configured-rate estimate is USD 0.0397821, below the USD 0.084
maximum reservation. These are single-run measurements, not a bill or stable
latency/quality comparison. Both arms used the same model and answer settings.

The targeted case review found that hybrid's ASTS-QA-06 answer now states the
wholesale-through-MNO arrangement; lexical describes the MNO route without
explicitly saying “wholesale.” Both IREN-QA-03 answers report 439 BTC but omit
the source's post-pool-fee qualifier. Hybrid packed that definition, so this
omission occurred during answer generation. The lexical ASTS-QA-07 answer was
rejected for missing calculation citations; hybrid answered it but omitted the
expected growth percentage. Exact-quote reconstruction errors affected lexical
IREN-QA-04 and hybrid IREN-QA-01/04/07. Rejected drafts are not retained, so
those cases remain unreviewable. These failures are after retrieval.

Root and independent-verifier source-review files are saved beside `run.json`.
They disagree on whether hybrid ASTS-QA-06 is complete: hybrid includes the
wholesale term, while the root review also requires the explicit low-Earth-orbit
detail. The user ran the offline `grade` action; it preserves that uncertainty.
The saved result is `graded-f81b4868ca8f.json`:

| Measure | Lexical | Hybrid |
|---|---:|---:|
| Overall PASS / FAIL / UNKNOWN | 12 / 2 / 7 | 12 / 2 / 7 |
| Reviewed cases | 17 | 16 |
| Answered cases | 17 | 16 |
| Claim-level checks passed | 26 | 24 |

The matching totals conceal different case outcomes. Lexical fails ASTS-QA-06
and IREN-QA-03; hybrid leaves ASTS-QA-06 UNKNOWN because the reviewers disagree
and fails IREN-QA-03 and ASTS-QA-07. Hybrid passes NBIS-QA-05, where lexical is
UNKNOWN; lexical passes IREN-QA-07, where hybrid has an unreviewable generation
error. Both ASTS-QA-09 and IREN-QA-10 abstentions were judged appropriate, but
their full-case verdicts remain UNKNOWN because empty answers have no citation
and metadata claims to grade. Thus `correct_abstentions: 0` does not mean those
abstentions were judged incorrect. The overall tie does not establish answer
quality parity or superiority from one generation per question.

This offline grade made no API calls. Keep the fixed pilot and 21 development
questions; the nine exposed holdout questions remain regression-only. For
reproducibility, the command used was:

```sh
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
MARKETLENS_PAIR_RUN=evaluation/filing_corpus/local/answer_pairs/paired-commercial-coverage-2026-10-27-v1
"$MARKETLENS_PYTHON" evaluation/filing_corpus/qa_retrieval_answer_compare.py grade \
  --run "$MARKETLENS_PAIR_RUN" \
  --root-review "$MARKETLENS_PAIR_RUN/root-review.json" \
  --verifier-review "$MARKETLENS_PAIR_RUN/verifier-review.json"
```

The source review also confirms a post-retrieval gap: hybrid evidence includes
IREN-QA-03's pool-fee qualifier, but the generated answer omits it. Retrieval
coverage improved on the reviewed requirements, while this paired answer grade
does not show that the gain consistently carried into complete answers.

### Follow-up answer prompt candidate: period-change comparisons

The hybrid ASTS-QA-07 answer gave the absolute increase of USD 10.666 million
but omitted the expected approximately 30.80% growth. The shared
`generation_input()` instruction said not to add an unrequested percentage,
which may be too restrictive for a question phrased “how did this change?”
The candidate now says to give both absolute and percentage change when a
cross-period change question supports both from same-basis values, while
keeping absolute-only requests free of an extra percentage. This changes only
the shared prompt; retrieval, packing, answer schema, validation and abstention
rules are unchanged. It is not yet measured on new model generations.

The focused prompt-contract test passed: 1 test in 0.861 seconds. From the
project root, the full-suite command after the prompt edit is:

```sh
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
"$MARKETLENS_PYTHON" -m unittest discover -s evaluation/filing_corpus -p 'test_*.py' -v
```

The user then reported **156 tests passed in 8.023 seconds** for this full
suite. This validates offline contracts but does not measure answer quality.
The user also ran the same-settings retrieval refresh,
`hybrid-period-change-prompt-refresh-2026-10-27-v1` (run hash
`d2c326b594bb7b695bc3e0e75dd2715b04d32e11375851172ff3bd790520c953`), and
offline pair-input validation:

- Validation is `valid` for 21 development questions, 12 documents, and 3,922
  passages, with both arms and zero network calls.
- Expected-source presence is 21/21 any and 20/21 all in both arms. After
  packing, hybrid has 14/14 financial row/header groups, 14/14 complete
  financial groups, 5/5 narrative groups, 19/19 complete answerable cases and
  32/32 requirements. Lexical has 14/14, 13/14, 4/5, 17/19 and 30/32. The
  evidence gate passes with IREN-QA-03 and ASTS-QA-06 newly complete and no
  complete answerable losses.
- Hybrid median/p95 local retrieval time is 13.785/33.265 ms, peak RSS is
  552,124,416 bytes, the vector cache was hit, passage encoding was zero, and
  8,279 windows were used. Lexical is 5.676/22.759 ms with peak RSS
  86,310,912 bytes. These are one-run local measurements.

This confirms the new pair inputs are fresh and the retrieval evidence gate
passes; it does not measure the prompt change's effect on generated answers.
A paired evaluation would make 42 `gpt-6-luna` generations and 42 token-count
requests, using the same 21 questions and selected public SEC passages. The
configured worst-case reservation is USD 0.084: 8,000 input and 2,400 output
tokens per generation at USD 0.10/M input and USD 0.50/M output; counting is
configured at zero cost. This remains under the configured USD 0.25 per-arm
and USD 0.50 aggregate caps. The current [GPT-6 Luna pricing](https://developers.openai.com/api/docs/models/gpt-6-luna)
confirms these Standard rates. The retrieval artifact reports
`paid_generation_authorized: false`, which is separate from the user's approval.
The user has since approved one paired run after the full-suite pass. This
approval is limited to the command below; it does not authorize later pairs.
The user runs the command and shares its summary:

```sh
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
SSL_CERT_FILE=/etc/ssl/cert.pem "$MARKETLENS_PYTHON" evaluation/filing_corpus/qa_retrieval_answer_compare.py evaluate \
  --retrieval-run evaluation/filing_corpus/local/retrieval_runs/hybrid-period-change-prompt-refresh-2026-10-27-v1 \
  --run-id paired-period-change-prompt-2026-10-27-v1 \
  --live --live-config evaluation/filing_corpus/local/luna-live-config.json
```

This sends the 21 development questions and selected public SEC passages to
OpenAI. The maximum reservation is USD 0.084; actual cost may be lower. The user
ran it; the approval is spent. The saved run hash is
`ff1187b1dc6e702f71c65b785703283d60a877ea804496f922afb5d4ccdcace2`:

| Measure | Lexical | Hybrid |
|---|---:|---:|
| Answered / errors / abstentions | 16 / 3 / 2 | 16 / 3 / 2 |
| Automatic numeric PASS | 9/15 | 10/15 |
| Numeric FAIL | 0 | 1 |
| Citation / expected-response PASS | 18/21 | 18/21 |
| Median / p95 answer time | 8.962 / 21.891 s | 7.565 / 21.299 s |
| Configured-rate cost estimate | USD 0.0203504 | USD 0.0200545 |

The evidence hashes match the preceding pair for all 21 cases. ASTS-QA-07 now
includes both the USD 10.666 million increase and 30.803% growth in both arms;
both automatic numeric checks pass. Hybrid IREN-QA-08 adds an unrequested
9.303% decrease, which fails the signed -9.302% target. The hybrid IREN-QA-03
answer still omits the post-pool-fee qualifier in its evidence. A single answer
per question cannot isolate prompt effects from generation variability. No
formal source-review grade is available for this pair.

The latest output led to a prompt refinement: distinguish “how did it change?”
questions, where both measures can be useful, from “how much did it
increase/decrease?” or absolute-difference questions, which should stay
absolute-only unless a percentage is explicitly requested. This candidate is
not measured by the pair above. The focused prompt test passes in 0.864 seconds;
its first run failed only because the test still expected the prior wording, and
the assertion was corrected. Retrieval settings, evidence packing, answer
schema, citation checks and abstention rules remain unchanged.

The refined prompt changes the answer-source hash, so run the full suite and a
fresh same-settings retrieval comparison plus offline validation before another
answer pair. The full-suite command remains:

```sh
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
"$MARKETLENS_PYTHON" -m unittest discover -s evaluation/filing_corpus -p 'test_*.py' -v
```

A new paired generation is not authorized by the prior one-run approval.

### Retrieval refresh after the prompt-scope refinement (2026-10-27)

The user ran `retrieval_compare.py compare` and offline pair-input validation
with the same fixed settings: lexical/dense weights 0.75/0.25, fixed lexical
floor 3, 128-token embedding windows, `context-anchored-tail-v1` packing and
lexical tail depth 15. The saved run is
`hybrid-period-change-scope-refresh-2026-10-27-v1`, hash
`df6cc618f96665d45f67bf576a413d86454122f935fac5735c024a2b42f2a6a3`. Validation
is `valid` for 21 development questions, 12 documents and 3,922 passages, with
zero network calls.

Literal expected-source presence remains 21/21 for at least one source and
20/21 for all expected sources in both arms. Before packing, hybrid is behind
lexical on labeled rows and complete answerable evidence (11/14 versus 12/14
financial rows; 11/19 versus 12/19 complete answerable cases). Packing changes
the result: lexical reaches 14/14 financial rows with headers, 13/14 complete
financial groups, 4/5 narrative groups, 17/19 answerable cases and 30/32
requirements. Hybrid reaches 14/14, 14/14, 5/5, 19/19 and 32/32 respectively.
It newly completes IREN-QA-03 and ASTS-QA-06 with no lost complete answerable
case. IREN-QA-10 gains one abstention-scope qualifier, but overall scope
coverage remains partial at 1/2 for hybrid and 0/2 for lexical. The evidence
gate passes; this is evidence coverage, not generated-answer accuracy.

The ranked lists and packed evidence exactly match the preceding prompt-refresh
run for all 21 cases in each arm. This shows the prompt wording change did not
change retrieval. The current run records its method-level source review as
pending. Hybrid latency was 13.891 ms median / 31.066 ms p95 with peak RSS
618,233,856 bytes; lexical was 5.494 / 22.303 ms with peak RSS 116,441,088
bytes. Hybrid used a local vector-cache hit, zero passage re-encoding and 8,279
embedding windows; setup took 3.773 seconds. There were no LLM generations or
embedding API calls. These are measurements from one local run.

The user did not include the full-suite result after the latest prompt
refinement. Run it before considering paired answer generation:

```sh
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
"$MARKETLENS_PYTHON" -m unittest discover -s evaluation/filing_corpus -p 'test_*.py' -v
```

The prior paid-run approval is spent. If the suite passes, the next meaningful
experiment is a matched answer-generation comparison to see whether the packed
evidence gain reaches answer quality. It will require a fresh spend estimate
and separate approval; the retrieval artifact itself does not authorize it.

### Full-suite pass and paired-answer preflight (2026-10-27)

The user ran the full unittest suite after the prompt-scope refinement: **156
tests passed in 7.984 seconds**. Together with the valid current-source
retrieval artifact and its passing evidence gate, this clears the offline
preflight for a matched answer-generation comparison. It does not authorize a
paid run.

The current local reviewed configuration selects `gpt-6-luna` Standard at
USD 0.10 per million input tokens and USD 0.50 per million output tokens. The
conservative budget is 42 generations (21 per arm, up to 8,000 input and 2,400
output tokens each) plus 42 token-count requests with zero configured fee.
Maximum reservation is **USD 0.084 total**, or USD 0.042 per arm, under the
existing USD 0.50 aggregate cap and USD 0.25 per-arm cap. These rates match the
[official GPT-6 Luna model pricing](https://developers.openai.com/api/docs/models/gpt-6-luna)
and [OpenAI API pricing](https://developers.openai.com/api/docs/pricing),
checked on 2026-10-27. Actual cost may be lower. This sends the 21 development
questions and selected public SEC passages to OpenAI.

The next pair uses the same model, answer prompt, schema, generation settings,
and strict citation/abstention validators for lexical and hybrid evidence. It
does not use an LLM to retrieve passages. The user approved one user-run pair
on 2026-10-27; that approval covers only the command below and awaits execution:

```sh
SSL_CERT_FILE=/etc/ssl/cert.pem "$MARKETLENS_PYTHON" evaluation/filing_corpus/qa_retrieval_answer_compare.py evaluate \
  --retrieval-run evaluation/filing_corpus/local/retrieval_runs/hybrid-period-change-scope-refresh-2026-10-27-v1 \
  --run-id paired-period-change-scope-2026-10-27-v1 \
  --live --live-config evaluation/filing_corpus/local/luna-live-config.json
```

At the time this preflight was written, no provider request had been made. The
user later completed and graded this pair; see the current evaluation sequence
below. Keep answer-quality results separate from retrieval metrics, and do not
relax citation or abstention checks.

### Evaluation sequence and status snapshot — 2026-10-29

The user’s three stages and current progress:

1. **Stage 1 — software tests: complete.** After the paired-run usage-validator
   repair, the full suite passed 169 tests in 9.764 seconds. After adding the
   `revenue_millions` metric-alias check, the user ran its focused regression
   (1 test passed in 0.973 seconds) and the full suite (170 tests passed in
   9.538 seconds). These tests check covered software contracts; they do not
   call the live LLM or measure retrieval quality.
2. **Stage 2 — retrieval: complete for this candidate.** The comparison run
   `hybrid-batch-unknown-fix-2026-10-29-v1` is valid for the fixed 21 development
   questions, 12 documents and 3,922 passages. Validation reports zero network
   calls and checks run integrity; the comparison report measures retrieval
   evidence.
3. **Stage 3 — answer generation and source review: complete.** The user
   completed `paired-batch-unknown-fix-2026-10-29-v1`; both arms completed 23
   generations and 23 token counts including quote retries. After the narrow
   usage-schema repair, the saved run validates offline. Two independent source
   reviews and the offline grade report 16 PASS / 5 UNKNOWN per arm, 17 reviewed
   cases, and 2 correct expected abstentions. The remaining UNKNOWNs are the two
   correct abstentions with non-applicable answer metadata, the extra automatic
   `revenue_millions` alias target for manually verified IREN-QA-01, and two
   strict validation errors per arm. The failed-answer cases differ by arm.
   This is one paired development sample, not evidence of hybrid superiority.

The evidence results are:

| Measure | Lexical | Hybrid |
|---|---:|---:|
| Expected source present, at least one | 21/21 | 21/21 |
| All expected sources present | 20/21 | 20/21 |
| Raw financial rows with headers | 11/14 | 10/14 |
| Packed financial rows with headers | 14/14 | 14/14 |
| Packed complete financial groups | 13/14 | 14/14 |
| Packed complete answerable cases | 17/19 | 19/19 |
| Packed answerable requirements | 30/32 | 32/32 |
| Packed narrative groups | 4/5 | 5/5 |
| Packed abstention scope | 0/2 | 1/2 |

Hybrid’s advantage appears after packing: raw hybrid rows and complete cases
start behind lexical, but context-anchored packing plus the lexical tail brings
the required evidence into the final answer budget. It newly completes
IREN-QA-03’s post-pool-fee detail and ASTS-QA-06’s wholesale-through-MNO detail,
with no critical requirement losses. IREN-QA-10 gains the hardware-profit
exclusion relevant to abstention scope; overall scope evidence remains partial.
The saved report’s method-level source review is still marked pending.

The new run reproduces the previous run’s ranked passages and packed evidence
exactly in all 21 cases in both arms. This confirms that the revenue-growth
answer-validator change did not change retrieval. In this five-repetition local
measurement, hybrid median/p95 query time was 13.49/33.48 ms and peak RSS was
about 541 MB; lexical was 5.63/22.30 ms and about 86 MB. The local vector cache
hit, passage encoding was zero, embedding API cost was zero, and no provider
generation occurred. These are single-machine measurements.

The preceding paired answer grade was lexical 15 PASS / 6 UNKNOWN and hybrid
17 PASS / 1 FAIL / 3 UNKNOWN. Hybrid’s IREN-QA-07 answer said 167.654% growth;
the cited facts support 167.652% at the displayed precision. Retrieval success
does not establish that the LLM will calculate or explain the answer correctly.

The previous notes described 13/14 as lexical row/header coverage. Correction:
both arms have 14/14 packed financial rows with headers; 13/14 is lexical’s
complete financial-group count. The hybrid count is 14/14.

The latest retry-aware paired run has been source-reviewed and graded below.
Do not repeat its generation with unchanged settings: the one-run approval has
been used. Keep the 12-document pilot and 21 development questions; the nine
exposed holdouts remain regression checks only.

## 2026-10-29 — Bounded pass for avoidable answer `UNKNOWN` results

This bounded pass was motivated by the historical grade for
`paired-scope-context-budget-fix-2026-10-28-v1`: lexical 13 PASS / 2 FAIL / 6
UNKNOWN and hybrid 14 PASS / 0 FAIL / 7 UNKNOWN. That previous grade is not
rewritten. Review identified four specific issues that could be addressed
without relaxing citation or abstention rules:

- Exact quote formatting failures now get at most one retry with a stronger
  instruction to copy a quote verbatim. The same source and strict citation
  validator are used again; a second invalid quote remains an error.
- The answer metric matcher recognizes the explicit aliases “annualization
  factor” and “ARR annualization multiplier” for the benchmark metric
  `annualization_multiplier`. This does not add fuzzy metric matching.
- IREN-QA-08 asks for the absolute decrease in USD/BTC, so its expected answer
  now checks that requested amount without requiring an unrequested percent.
- The grade summary counts a correct expected abstention from its correctness
  and abstention review checks separately from the full-answer verdict. The
  overall case can remain UNKNOWN when answer-only metadata does not apply.

The retry ceiling changes any future paired live-run budget. It permits up to
42 answer generations and 42 token-count requests **per arm** (84 of each over
the two arms). At the rates and token-count fee in the saved local
configuration, the maximum reservation is USD 0.168 in total. This ceiling is
not a fresh price review or authorization. Before any Stage 3 call, recheck
rates and obtain new approval; the local configuration must reserve 42
generation requests per arm.

The 21 development question texts, source spans and relevance labels are
unchanged. The benchmark reference and relevance-file fingerprint changed to
record the IREN-QA-08 correction. The SEC pilot remains 12 documents and 3,922
passages. At the time this bounded-pass note was written, the full Stage 1
suite and refreshed Stage 2 retrieval run had not yet been reported. Later
results are recorded below. Some UNKNOWN results can remain correct when
evidence is insufficient or an automatic check cannot resolve a metric alias.

### Stage 2 refresh result (2026-10-29)

The user ran and validated `hybrid-batch-unknown-fix-2026-10-29-v1`. Its saved
hash is `bfd6e954ff916504071a752dca4b4f0b451b1e7d72f58db90e0742437671a0f0`.
Validation reports 21 development questions, 12 documents, 3,922 passages,
both arms and zero network calls. The evidence gate passes, but the report says
`paid_generation_authorized: false`.

| Packed evidence measure | Lexical | Hybrid |
|---|---:|---:|
| Expected source present, at least one | 21/21 | 21/21 |
| All expected sources present | 20/21 | 20/21 |
| Financial rows with headers | 14/14 | 14/14 |
| Complete financial groups | 13/14 | 14/14 |
| Complete narrative groups | 4/5 | 5/5 |
| Complete answerable cases | 17/19 | 19/19 |
| Answerable requirements | 30/32 | 32/32 |

Hybrid newly completes IREN-QA-03 and ASTS-QA-06, with no lost complete case
or critical requirement. These counts match the prior Stage 2 result: this
refresh updates the run fingerprint after the benchmark/code changes; it does
not establish another retrieval improvement or answer quality.

In this local cached run, median/p95 query time was 14.29/32.77 ms for hybrid
and 5.75/23.04 ms for lexical. Peak memory was about 540 MB versus 103 MB.
The hybrid vector cache hit, passage encoding was zero, and embedding API cost
was zero. This is one local measurement over 21 questions, not a repeated
performance estimate.

At the time this Stage 2 output was supplied, the complete Stage 1 suite and
Stage 3 rate review were still pending. The user subsequently completed both;
their results appear below.

### Stage 1 completion and Stage 3 preflight (2026-10-29)

The user first ran the full Stage 1 suite after the bounded remediation:
**166 tests passed in 9.727 seconds**. After the paired-run usage-schema repair,
the user reran it and **all 169 tests passed in 9.764 seconds**. This confirms
the tested code contracts; it does not measure retrieval quality or
generated-answer quality. The saved Stage 2 run remains valid and unchanged.

Current Standard `gpt-6-luna` pricing was checked against the [official API
pricing page](https://developers.openai.com/api/docs/pricing) and [model
page](https://developers.openai.com/api/docs/models/gpt-6-luna): USD 0.10 per
million input tokens and USD 0.50 per million output tokens. At the saved
8,000-input / 2,400-output limits, the retry-aware ceiling is **USD 0.168** for
84 generations across both arms; the config lists zero cost for token-count
requests. This is a maximum reservation, not an expected bill.

The saved ignored live configuration originally had `max_generation_requests`
set to 21, which the retry-aware harness rejects. After the user approved one
run at the updated ceiling, that local value was changed to 42. A no-network
preflight accepted it. The user then ran the approved Stage 3 command; all 46
generation attempts and 46 token counts completed across the two arms. The
recorded configured-rate cost was USD 0.0462299 combined, below the USD 0.168
maximum reservation. The generic CLI failure occurred after saving the answer
artifact, during integrity validation. After fixing the usage-schema mismatch,
an offline load of the saved artifact passes. No generation needs to be
repeated. Two independent source reviews and the offline grade are complete:
lexical and hybrid each have 16 PASS / 5 UNKNOWN, 17 reviewed cases and 2
correct expected abstentions. The UNKNOWN causes are recorded in the current
evaluation sequence above.

### Calculation-rendering refresh: Stage 2 result (2026-10-29)

The user reports Stage 1 is all okay. The new retrieval artifact
`hybrid-local-calculation-render-2026-10-29-v1` validates for the fixed 21
development questions, 12 documents and 3,922 passages, with zero network calls.
Its evidence gate passes: hybrid packing completes IREN-QA-03 and ASTS-QA-06,
loses no complete case, and has no critical requirement losses. Packed
answerable completeness is 19/19 hybrid versus 17/19 lexical; financial
completeness is 14/14 versus 13/14; narrative completeness is 5/5 versus 4/5.
Literal source presence is unchanged at 21/21 any and 20/21 all in both arms.
Hybrid raw rows/headers/completeness are lower than lexical, but context and
tail packing recover and complete the groups in this run.

The cache was warm and no passages were re-encoded. Hybrid median/p95 query
latency was 14.38/32.63 ms and peak RSS 588 MB; lexical was 5.46/22.28 ms and
115 MB. This is one local run, and hybrid costs more local time and memory.
It measures evidence coverage, not generated-answer correctness. The user
completed the approved paired Stage 3 run; its result is recorded below. The
USD 0.168 reservation covered at most 84 generations and 84 zero-fee token
counts under the USD 0.50 cap. This was a ceiling, not the observed call count.


### Calculation-rendering pair and transition decision (2026-10-29)

The user ran `paired-local-calculation-render-2026-10-29-v1` (hash
`99f4f80b1948a506b2c5b738868dc9094c871002b8e755907f4d2b470c0e910d`).
An assistant-run offline `load_pair_run()` check passes current code, question,
source, evidence and provider-usage integrity. It makes no new API calls.

| Measure on 21 development questions | Lexical | Hybrid |
|---|---:|---:|
| Accepted answer records | 17 | 17 |
| Expected-question abstentions | 2 | 2 |
| Strict answer-validation errors | 2 | 2 |
| Automatic numeric PASS / UNKNOWN / NOT_APPLICABLE | 13 / 2 / 6 | 13 / 2 / 6 |
| Citation-integrity PASS / UNKNOWN | 19 / 2 | 19 / 2 |
| Actual generations / token-count requests | 22 / 22 | 24 / 24 |
| Median / p95 answer time, seconds | 8.072 / 17.796 | 6.520 / 19.482 |
| Configured-rate provider cost, USD | 0.0218819 | 0.0227998 |

The combined recorded cost is USD 0.0446817, below the approved USD 0.168
ceiling. Bounded quote retries explain calls beyond the 21 questions per arm.
Both provider sessions finished without halting. This is one stochastic
sample: hybrid has a lower median but a higher p95 here, and no accepted-answer
count advantage. These status and automatic-check figures are not an accuracy
score. The new run has not received two independent source reviews or a formal
combined grade; do not run `grade` against missing or placeholder reviews and
interpret UNKNOWN as new model behavior.

The local revenue/cash calculation change worked in this sample: IREN-QA-07
returns 167.652% and NBIS-QA-09 returns USD 161.4 million / 7.054% in both arms.
Both methods reject IREN-QA-03 with `numeric_citation_row_mismatch` and
IREN-QA-04 with `citation_quote_not_verbatim`. Stored offsets reconstruct the
IREN-QA-03 quote as `mined 439 BTC`; the required `Bitcoin` label ends the
preceding passage and was not cited. Hybrid's fee-definition quote does not
supply that missing adjacent row fragment. IREN-QA-04 remains an exact-quote
failure after the bounded retry. Both error records contain zero claims.
These failures occur after evidence packing, not because the needed source is
missing. Keep the validators strict.

**Next project step: build the private Telegram Q&A MVP.** Freeze this hybrid
configuration for integration, retain lexical as a regression control, and
pause broad retrieval/prompt tuning. The bot must return cited accepted claims,
show that its pilot sources are historical, and safely explain rejected or
unavailable answers. Independent grading can finish on the saved answers
without paying for another pair. Record the revenue-growth formula's input-unit
wording as a product-display issue: the percent value is checked, but the
rendered explanation should state that the revenue units cancel. This is
readiness to begin product development, not production acceptance. Keep the
12-document / 3,922-passage corpus, 21 questions and holdout constraints fixed.

### Exact value-row quote hints: bounded repair candidate (2026-10-29)

The user requested a focused repair for IREN-QA-03 and IREN-QA-04 before Telegram
integration. Retrieval settings and evidence packing remain frozen. The existing
financial fact index provided values and passage IDs, but no short row quotes
for the model to copy. It now includes source-derived `value_row_citations`.

- New `source_value_row_citations()` finds a short, unique, intact row in one
  supplied passage and verifies it with the existing `fact_row_citations()` rule.
  It can select the intact `439 Bitcoin mined` highlight rather than the broken
  `mined 439 BTC` fragment. Installed capacity uses `21 EH/s installed`; average
  operating hashrate keeps its own distinct row. It does not join fragments,
  repair punctuation, fetch hidden evidence or read benchmark answers.
- Modified `source_financial_fact_index()` exposes at most one row quote per
  fact, at most 300 characters per quote and 600 quote characters across the
  index. Missing, ambiguous, unsupported and over-budget hints stay empty.
- Modified `generation_input()` directs both the first answer and the existing
  quote retry to copy these quotes with their listed passage IDs. The answerer
  still needs separate period/unit headers and material qualifiers. Repeated
  quote/index guidance was shortened to make room. The response schema, exact
  citation checks, financial validators, abstention rules and retry count are
  unchanged. The runtime still enforces its 8,000-input-token limit.

Two Stage 1 tests were added for the affected questions and unsafe hints. Existing
tests now check the new input field, exact source membership and request bounds.
Ten focused checks passed; after the final prompt compression, four affected
checks passed again in 2.007 seconds. Constructing all 84 initial/retry request
bodies from the prior frozen packs produced 25,506–28,825 bytes, within the
32,000-byte limit. The prior highest-token case's request is 200 bytes smaller.
This is a byte/presentation check, not a new API token count or evidence of a
live quality gain. A model can still ignore a hint; strict rejection remains.

The final affected Stage 1 checks were run from the project root with the
experiment interpreter. They check usable exact hints for both failed questions,
unchanged source evidence and request limits, retained prompt rules and absence
of benchmark labels in generation input:

```sh
PYTHONPATH=evaluation/filing_corpus evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python -m unittest \
  test_qa_answers.SourceTests.test_generation_row_hints_supply_exact_quotes_for_bitcoin_and_capacity \
  test_qa_answers.SourceTests.test_compact_model_evidence_preserves_all_passages_sources_and_hash \
  test_qa_answers.SourceTests.test_generation_instructions_require_supported_question_coverage \
  test_qa_retrieval_answer_compare.FrozenInputTests.test_generation_input_contains_no_benchmark_labels -v
```

Before editing, offline validation of the preceding pair passed and 15 matching
source/test/reference files were preserved in its ignored
`source-before-row-hints` directory with checksums. The saved run JSON and grades
were not rewritten. Current code validation of an old run may now reject its
source fingerprint; that does not mean its original data is corrupt. Use a new
run ID for the new code. No new LLM or embedding API call has been made.

From the project root, run **Stage 1 — software tests**. This checks code rules
and uses offline fixtures; it does not call the filing-answer LLM:

```sh
MARKETLENS_PYTHON="$PWD/evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python"
PYTHONPATH=evaluation/filing_corpus "$MARKETLENS_PYTHON" -m unittest discover -s evaluation/filing_corpus -p 'test_*.py' -v
```

If it passes, run **Stage 2 — retrieval**, followed by **Stage 2 — artifact
integrity validation**. This refreshes the source identity with the same
retrieval settings; it is not another weight sweep. The existing vector cache
can be reused. Both commands are offline and incur no provider charge:

```sh
"$MARKETLENS_PYTHON" evaluation/filing_corpus/retrieval_compare.py compare \
  --run-id hybrid-exact-row-hints-2026-10-29-v1 \
  --lexical-weight 0.75 --dense-weight 0.25 \
  --lexical-floor 3 --floor-policy fixed \
  --passage-window-tokens 128 \
  --packing-policy context-anchored-tail-v1 \
  --lexical-tail-depth 15 &&
"$MARKETLENS_PYTHON" evaluation/filing_corpus/qa_retrieval_answer_compare.py validate \
  --retrieval-run evaluation/filing_corpus/local/retrieval_runs/hybrid-exact-row-hints-2026-10-29-v1
```

Stop and share any failure. Do not rerun an existing run ID or start another paid
pair automatically. After these checks, estimate and authorize one fresh matched
Stage 3 run to measure whether the two citation failures improve. Keep the same
21 development questions and the immutable pilot; exposed holdouts remain
regression-only.

### Stage 1 follow-up: stale prompt-wording assertion (2026-10-29)

The user ran all 172 tests in 10.073 seconds: 171 passed, and
`test_evidence_instructions_cannot_change_request_settings()` failed because it
still expected the old literal quote-instruction sentence. The shortened prompt
retains the same verbatim/contiguous requirement. The test now checks the current
wording and additionally compares the prompt instructions, answer schema and
request fields before/after hostile evidence is inserted. Existing no-tools and
no-storage checks remain. This strengthens the offline request-isolation check;
it does not establish live prompt-injection resistance.

Only that test method changed in the follow-up; no runtime function or prompt
changed. The assistant ran the focused Stage 1 command below from the project
root using the experiment interpreter: **1 test passed in 0.863 seconds**.

```sh
PYTHONPATH=evaluation/filing_corpus evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python -m unittest test_qa_answers.SourceTests.test_evidence_instructions_cannot_change_request_settings -v
```

The other 171 passing results apply to the same application code. The full suite
has not been rerun after this test-only repair. Continue with the Stage 2
comparison/validation commands above; another full-suite run is unnecessary for
this wording-check correction. The proposed run ID remains
`hybrid-exact-row-hints-2026-10-29-v1`. No provider call was made.

### Row-hint Stage 2 refresh passes; Stage 3 preflight (2026-10-29)

The user completed `hybrid-exact-row-hints-2026-10-29-v1`, hash
`fa181f528b15a7d79d58ae00f4159093d85626a41f28f9bcd27ada6d6a0b7517`.
It validates for the fixed 21 questions, 12 documents and 3,922 passages with
zero network calls. Hybrid retains 19/19 complete packed answerable cases versus
lexical 17/19, financial completeness 14/14 versus 13/14 and narrative
completeness 5/5 versus 4/5. Both retain 14/14 actual financial rows with headers
and expected-source presence 21/21 any, 20/21 all. The evidence gate passes with
IREN-QA-03 and ASTS-QA-06 newly complete relative to lexical, with no losses.

An assistant-run read-only `load_pair_inputs()` check passes current code and
frozen evidence. Comparing with the preceding calculation-rendering pair shows
identical raw retrieved IDs and evidence hashes for all 42 case/arm pairs. This
is a new source-identity artifact, not new retrieval quality. The new row hints
are added during generation-input construction; Stage 2 cannot establish whether
the answerer will use them correctly.

This cache-hit run records hybrid median/p95 retrieval-plus-packing time of
13.99/31.52 ms and 547 MB peak RSS, versus lexical 5.46/22.46 ms and 119 MB.
Hybrid setup took 4.22 seconds, reused 8,279 windows and encoded no passages.
These are local measurements; timing/memory differences from the prior run do
not establish a performance improvement.

The no-network `paired_provider_configs()` preflight on the unchanged saved
configuration reserves at most USD 0.168: 42 initial answers across both arms,
plus up to 42 bounded quote retries, and up to 84 token-count requests at the
configured zero count fee. At 8,000 input / 2,400 output tokens per generation,
the configured per-call ceiling is USD 0.002. The maximum is USD 0.084 per arm,
under the saved USD 0.25 per-arm / USD 0.50 total run caps. The current official
[GPT-6 Luna model page](https://developers.openai.com/api/docs/models/gpt-6-luna)
lists USD 0.10/1M input and USD 0.50/1M output tokens, matching the saved rates.
This estimate is based on configured rates and limits, not an invoice or account
balance check.

### Row-hint Stage 3 result and offline grade (2026-10-30)

The user completed `paired-exact-row-hints-2026-10-29-v1`, hash
`a0876278d39a824e36bea023bf49991166cca104ead896ada2e7565b48421abd`, on the
validated fixed-pilot retrieval run above. Lexical accepted 19 answers, had 2
expected abstentions and no validation errors. Hybrid accepted 18 answers, had
2 expected abstentions and one validation error on IREN-QA-07. Automatic numeric
checks were lexical 14 PASS / 1 UNKNOWN / 6 NOT_APPLICABLE and hybrid 13 PASS /
2 UNKNOWN / 6 NOT_APPLICABLE. Citation integrity and expected-response checks
were 21/21 lexical and 20/21 hybrid; the latter UNKNOWN is the rejected answer.
Both arms now answer IREN-QA-03 with the exact `439 Bitcoin mined` row and
IREN-QA-04 with the installed-capacity and operating-average figures separated.

The run used 21 lexical and 24 hybrid generations and the same number of
token-count requests. Configured-rate costs were USD 0.0207398 lexical and
USD 0.0237164 hybrid (USD 0.0444562 total), below the approved USD 0.168 ceiling.
Median answers took 7.885 seconds lexical and 7.914 seconds hybrid; p95 was
14.282 and 23.023 seconds. This is one run, not stable latency evidence or proof
that either arm is generally more accurate. Hybrid's one rejected response was
not saved, so the exact validator cause cannot be inspected. On lexical
IREN-QA-07, reviewers agree the 167.652% value is supported but disagree about
the formula wording that appends `USD million` to a percentage calculation.
NBIS-QA-05 is supported by source review, while its separate automatic
annualization-multiplier target remains UNKNOWN.

The root and independent verifier reviews are complete and hash-matched beside
`run.json`. The user ran **Stage 3 — offline answer grading** and saved
`graded-d37e87cc345e.json`. Both arms scored 17 PASS / 4 UNKNOWN / 0 FAIL.
Each has 2 correct expected abstentions and 0 excessive abstentions. The four
UNKNOWNs per arm are the two expected abstentions with inapplicable claim
metadata, NBIS-QA-05's automatic multiplier check, and IREN-QA-07. The latter is
a reviewer units disagreement for lexical and an unreviewable validation error
for hybrid. This grade uses saved answers and review records only.

The command used was:

```sh
MARKETLENS_PYTHON="$PWD/evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python"
MARKETLENS_PAIR_RUN=evaluation/filing_corpus/local/answer_pairs/paired-exact-row-hints-2026-10-29-v1
"$MARKETLENS_PYTHON" evaluation/filing_corpus/qa_retrieval_answer_compare.py grade \
  --run "$MARKETLENS_PAIR_RUN" \
  --root-review "$MARKETLENS_PAIR_RUN/root-review.json" \
  --verifier-review "$MARKETLENS_PAIR_RUN/verifier-review.json"
```

The offline grade preserves the reviewers' units disagreement as UNKNOWN. No
further paid run is needed to close this evaluation. Proceed to the private
Telegram Q&A MVP with the fixed pilot and safe handling of validation errors
and abstentions. This result is development evidence, not production acceptance.
