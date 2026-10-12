# Project Context

## Product

MarketLens (formerly QuarterlyLens) is planned as a private Telegram company-research bot covering Nebius Group (NBIS), IREN (IREN), and AST SpaceMobile (ASTS). The detailed product definition and build sequence are in `PROJECT_PLAN.md`.

## Tech Stack

- User interface: Telegram bot; implementation framework is undecided.
- Application language/runtime: undecided.
- Database or persistent storage: undecided.
- Filing, news, and market-price providers: to be evaluated.

## Core Rules & Constraints

- Ground company answers in cited filings and company releases; identify the company, period, and units, and say when evidence is missing.
- Preserve source links, identifiers, publication times, periods, units, forecast cutoffs, and alert state where relevant. Deduplicate repeated alerts.
- Distinguish reported facts from interpretation.
- The primary horizon is five trading sessions; evaluation also includes twenty sessions. Show evidence, assumptions, supported uncertainty and cutoff. Price-origin mode/provider remain open.
- Use chronological, point-in-time backtests; do not use later information in historical forecasts. Compare with simple baselines and report sample size and limitations.
- Do not assume a language, framework, database, or provider until project evidence or an explicit decision establishes it.

## Active Progress

- ✅ **Step 1 — historical testing anchors (2026-10-09):** Approved Mind milestone implemented with Hyper Relay and learning-first workflow: 30 Q&A cases (10/ticker; 4 abstentions; 21 development/9 holdout), 36 historical event clusters (12/ticker), 5/20-session protocol, bounded audit and frozen source/candidate metadata. These are historical test cases and source references, not a downloaded two-year filing corpus or historical price series. See `evaluation/` and its source_audit.md verification record.
- ✅ **Step 2 — SEC filing pilot collected and verified (2026-10-12):** All twelve source entries passed live SEC checks for accession, form, dates and expected text markers. Current response hashes are pinned; earlier IREN/ASTS hashes remain for provenance. The immutable local `sec-qa-pilot-v1` release contains 12 documents, 17,272,038 raw bytes and 3,922 passages. Raw filing bodies and reports are ignored under `evaluation/filing_corpus/local/`; they were not part of the tracked source manifest. Rebuilding this version is not supported; use `verify`. The collector supports `audit-all`. Exact causes of historical byte-hash differences are unknown because the earlier response bodies were not retained.
- ✅ **Initial retrieval evaluation and ASTS-QA-04 investigation (2026-10-12):** At top five, development source recall was 20/21 questions for at least one expected source and 19/21 for all expected sources. The exposed holdout was 9/9 and 7/9, respectively. For the development-only top-ten diagnostic, results were 21/21 and 20/21. The requested ASTS cash-and-cash-equivalents row exists in the release at `ASTS-S2:00017`, in USD thousands, but ranked ninth; top ten surfaced it. The same balance-sheet row is also present in ASTS-S3 at a lower rank. Detailed findings are summarized in `evaluation/source_audit.md`; private logs and generated reports remain local.
- ⚠️ **Technical Debt / Known Issues:** No product application, answer generator, labels, backtest or Telegram integration exists. Retrieval metrics test whether expected source documents appear; they do not test answer correctness, citations, numeric calculations or abstention. Top-ten retrieval returns more near-matches. The remaining top-ten development all-source miss is ASTS-QA-09, an abstention question needing evidence from four filings. The nine holdout cases are exposed and should be treated as regression checks, not a blind test. SEC retention for this private pilot does not settle price/news-provider rights or retention. Full history, precise date-only availability, price-origin convention, calendar and Telegram setup remain unresolved. Language/framework/storage/product providers remain open.
- ➡️ **Next Steps:** Begin Step 3 with structured financial facts and calculation checks for numeric questions, preserving source, reporting period, units and filing vintage. Use the development split to iterate; do not repeatedly tune against exposed holdout answers. Decide the model/runtime only when the product implementation needs it. Keep forecast and broader price/news source gates open until evidence resolves them.
