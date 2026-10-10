# MarketLens evaluation protocol — milestone 1

Version 1.0.1 · starter set frozen 2026-10-07 · review closed 2026-10-09

## Completion boundary

This milestone defines evaluation data and source contracts. Retrieval, monitoring, Telegram delivery, models and backtests are not implemented. Language, storage, frameworks, MCP integration and providers remain open.

- **30 Q&A cases:** 10 per ticker; 12 numeric, 6 explanation, 8 comparison, 4 abstention.
- **36 event clusters:** 12 per ticker, selected without inspecting returns.
- Search window: **2024-10-01 to 2026-09-30**, inclusive. The first 12 qualifying clusters fall earlier in that window; this is not a balanced two-year sample.
- **5 trading sessions primary; 20 secondary.** Both currently have zero price labels.
- Research bound: SEC, three issuer IR families, at most two price and two broader-news candidates; 200 retrieval attempts maximum, at most two retries per item.

## 1. Q&A data and scoring

[qa_benchmark.jsonl](qa_benchmark.jsonl) is UTF-8, one JSON object per line.

| Fields | Contract |
|---|---|
| schema_version, case_id, ticker, category, split | Stable identity, task type and partition |
| question, expected_answer | Prompt and human-written grading reference |
| reporting_period, units, accounting_basis | Required interpretation context |
| source_vintages | Dates of the specific document versions |
| evidence | Source ID, exact URL and section/table locator |
| expected_numbers | Named values, units and absolute tolerances |
| calculations | Ordered inputs, operation, output, unit and tolerance |
| abstention | Null for answerable cases; otherwise missing inputs and document scope |

Source IDs resolve to the registry in [source_audit.md](source_audit.md). Vintage dates identify versions; they are not precise forecast cutoffs. A release copied into a later SEC exhibit has separate release and acceptance times.

Use decimal arithmetic:

- difference(a,b) = a − b
- share_percent(a,b) = 100a/b
- growth_percent(a,b) = 100(a/b − 1)
- divide(a,b) = a/b

Input units follow the cited rows. USD thousands convert to millions by dividing by 1,000. Tolerances allow rounding, not economic uncertainty. A negative change and a positive amount explicitly described as a decrease can be semantically equivalent.

### Frozen partitions

There are **21 development and 9 holdout cases**, 7/3 per ticker. Freeze IDs and references before tuning. Only questions and permitted source content may enter the answerer's context. Gold answers, calculation outputs and grading notes belong to the evaluator.

This holdout is a readable, logical partition, not a secret or independently authored test. Sources overlap and questions are correlated. Once a holdout answer influences tuning, record that exposure and create a fresh test set before claiming generalization. These are not 30 independent trials.

### Future baseline acceptance

Score factual support, company/period/unit accuracy, citation support and calculation correctness separately. Report results by ticker/category/split and retrieval recall of required evidence locations. Categorize failures as retrieval, extraction, version/period selection, arithmetic, reasoning or abstention.

Initial engineering targets:

- At least 17/21 development and 7/9 holdout answers fully correct.
- All four missing-evidence cases abstain and identify missing inputs.
- No fabricated citation, later-vintage substitution or unsupported precise number.
- Every claimed calculation reproduces within its recorded tolerance.

These are targets, not measured model results. Human review must check semantic answers and supporting citations; a model judge alone is insufficient.

## 2. Event selection

The frozen candidate inventory in source_audit.md is the input; the CSV is its selected-cluster projection.

1. Enumerate observed issuer-newsroom records forward from the window start.
2. Admit results, monthly operations, financing/transactions, actual or specifically planned capacity/product deployments, customer/market-access agreements, regulatory/operational authorizations, and formal operational coordination agreements. A voluntary coordination agreement is not a regulatory authorization.
3. Exclude upcoming-results notices, conferences, rankings, index membership, administrative notices, strategic-adviser reviews without announced commitments, educational collaborations, and unquantified supplier/design work without new customer-market access or service deployment.
4. Group proposal, pricing, closing and related follow-ups under the first disclosure. Exclude a cluster first disclosed before the window. Deduplicate identical publisher permalinks.
5. Group new eligible events sharing an issuer and publisher calendar date into a compound cluster. Attach follow-ups to existing transactions first: an old transaction's follow-up does not merge a separate new event simply because they share a date.
6. Sort anchors by date, then offset-aware time when available, then stable candidate ID. Select the earliest 12 per ticker. Do not refill a price-gap case.

The rule is deterministic **given the frozen human-reviewed eligibility and cluster assignments**. It is not an automatic classifier or a claim to enumerate all SEC/media events. Corrections require a version change and explained diff.

Examples: NBIS October 17/21 resumption announcements; IREN December proposal/pricing/closing; ASTS January spectrum agreement/June settlement. ASTS November 14 launch agreements/results and March 3 joint venture/results are compound clusters with different member times. ASTS October 2 warrant completion belongs to an August disclosure and is excluded.

**Cluster membership permits deduplication, not future evidence.** At a forecast cutoff, admit only members individually proven available by then. A follow-up forecast needs a distinct forecast-instance ID, and the entire cluster stays in one evaluation partition.

## 3. CSV contract

[forecast_events.csv](forecast_events.csv) has one row per selected cluster.

| Field group | Meaning |
|---|---|
| event_id, ticker, event_type, anchor_candidate_id | Stable event identity |
| published_date, published_at, timestamp_precision, source_url | Publisher provenance |
| observed_at, observation_basis | This audit's archive observation |
| cluster_members | JSON list; each member retains its own date/time and URL |
| forecast_at, origin_mode, origin_session, origin_price | Empty until timing and origin are resolved |
| end_session, end_price, return fields for 5/20 | Empty until labels are verified |
| label_status, price_status, split, coverage_note | Explicit gates and missingness |

CSV empty cells and JSON null mean **unknown/unpopulated**, never zero or midnight. A date-only release does not become an invented ISO timestamp. ASTS feed timestamps include offsets and zero seconds in this sample; conservatively treat them at minute precision. NBIS/IREN anchors are date-only. NBIS's archive observation uses the capture file's modification time and is labelled accordingly.

## 4. Point-in-time evidence

A future collector must retain separately:

- Publisher time and precision.
- Raw SEC accession acceptance timestamp.
- First time our collector observed each version.
- Content hash, source identity, accession and version.
- Actual forecast freeze/issuance time.

An October 2026 audit retrieval cannot establish a 2024 bot observation. Declare either publication-time historical simulation, with limitations, or prospective observed-time evaluation.

Only information available by the cutoff may enter a prediction. Later revisions, filings, archive edits and same-cluster follow-ups cannot be backdated. Latest-value APIs alone are insufficient. Accession-specific documents and dated versions are needed.

Model pretraining may already contain later outcomes. Restrict tools/context, log model/version and prompts, and disclose that retrospective LLM evaluation cannot rule out this leakage. A prospective shadow run is required before strong predictive claims.

## 5. Price target and unresolved origin

The target is **split-adjusted USD price return, excluding cash dividends**. A dividend-adjusted total-return series defines a different target and requires a protocol revision. Retain raw prices and corporate actions to reconcile both endpoints on the same split basis.

For a verified regular-session calendar s, an origin session s[j], and its reference price P[j]:

**return_h_pct = 100 × (P[j+h] / P[j] − 1), for h = 5 or 20.**

The origin is session zero; the endpoint is the fifth or twentieth subsequent scheduled trading session. Holidays and early closes require a calendar. Missing/halted required prices make a label unavailable; do not interpolate or skip forward to the fifth observed row.

**Origin mode remains open until source capabilities are verified.**

| Candidate convention | Required evidence and tradeoff |
|---|---|
| First regular-session open strictly after forecast freeze | Reliable opens, timestamps, halt handling and release-time mapping |
| First regular-session close strictly after freeze | Daily-data compatible; measures movement after that later close and excludes the earlier alert-to-close move |

Use the same price field at origin and endpoint, in s[j] and s[j+h]. Neither convention is selected. Name the future origin in the forecast. Information arriving between freeze and future origin is prohibited as a prediction input.

A previous close before an after-hours announcement is not an actionable post-alert price; it could support a separately named event-reaction study. Date-only sources need a verified conservative availability rule before session mapping. Unresolved rows remain blocked.

NBIS anchors before October 21, 2024 retain a trading-gap flag. Old YNDX quotes cannot substitute for a contemporaneous NBIS baseline.

### Label eligibility gates

Record all of the following before populating any label:

1. Evidence version and adequate availability bound.
2. Selected origin convention, timezone and verified exchange calendar.
3. Security identity, tradability and corporate-action treatment.
4. Data access/usage rights for intended retention and display.
5. Positive valid origin/endpoint prices with vendor timestamps and provenance.
6. Completed endpoint and vendor finalization status.

Keep per-horizon failure reasons; report attempted, eligible, labelled and excluded counts. Passing the 5-session gate does not pass the 20-session gate automatically.

## 6. Future backtest

Freeze a versioned labelled manifest before fitting. Split chronologically across all three companies, keeping issuer transaction clusters intact. Use expanding training windows and later validation/test blocks. Purge training label intervals overlapping a later block; apply a **20-trading-session embargo** at boundaries, across issuers too.

No random shuffling of related events. Report sample remaining after purging. If too small, report insufficient evidence. Thirty-six clusters cannot establish reliable calibration, subgroup significance or a complex model's predictive skill.

Compare on identical eligible rows against:

- Zero return.
- A zero-centred trailing-volatility interval using 60 valid daily returns available before the forecast freeze, scaled by square root of horizon as a rough assumption. If history is incomplete, mark this baseline unavailable.
- Upward probability 0.5 where probability outputs are evaluated.

Report MAE in percentage points, median absolute error, directional accuracy with a zero-return class, baseline-relative error and sample size. For intervals: nominal level, empirical coverage and mean width. For upward probabilities: Brier score against return > 0. Set interval level and tie rules before seeing holdout outcomes. Avoid tiny-bin calibration claims.

Document overlapping windows/shared events; they are correlated. These are predictive associations, not causal effects of announcements.

## 7. Next slice and acceptance boundary

Milestone-1 checks validate counts, schemas, calculations, provenance, selection and explicit limitations. Results are in source_audit.md. They do not certify licensing, complete historical coverage, forecasts or product behavior.

Next: resolve corpus access/retention and document-version handling, then propose a small filing-Q&A baseline using the development cases. Price-provider and origin decisions can proceed separately. Stack selection should follow these source constraints.

## Revision history

- **1.0 (2026-10-07):** Initial 30-case / 36-cluster starter set.
- **1.0.1 (2026-10-09, independent-review corrections):** Made operational coordination agreements explicit in eligibility, matching the NSF event's actual nature. Attached the observed June 30 ASTS joint-venture headquarters follow-up to ASTS-E-09 with its own timestamp. No new anchor, price data or return-driven selection; counts remain 30 questions and 36 clusters.
