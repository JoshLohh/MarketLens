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

- ✅ **Completed (2026-10-09):** Approved Mind milestone implemented with Hyper Relay and learning-first workflow: 30 Q&A cases (10/ticker; 4 abstentions; 21 development/9 holdout), 36 event clusters (12/ticker), 5/20-session protocol, bounded audit and frozen source/candidate metadata. See evaluation/ and its source_audit.md verification record.
- **Findings:** IREN needs historical 20-F/6-K and amendments plus later domestic forms, with IFRS/GAAP and original/revised vintages preserved. NBIS has pre-resumption price gaps. ASTS clusters retain each member's availability.
- ⚠️ **Unknowns:** No application code, durable corpus, model runner, labels, backtest or Telegram integration exists. Provider entitlement/retention, full history, precise date-only availability, origin convention, calendar and Telegram setup remain unresolved. Language/framework/storage/providers remain open.
- ➡️ **Next:** Propose a small versioned filing-corpus/Q&A baseline slice after resolving access/retention. Tune on development cases and record holdout exposure. Resolve price/news and forecast gates before labels or predictive claims.
