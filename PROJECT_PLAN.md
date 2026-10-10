# MarketLens Project Plan

> Current project name: MarketLens. Former working name: QuarterlyLens.

## Product goal

MarketLens is a private Telegram company-research bot for Nebius Group (NBIS), IREN (IREN), and AST SpaceMobile (ASTS). It answers questions about the companies, watches for new SEC filings and selected news, and sends source-linked summaries with an estimate of expected share-price movement.

Forecasts are uncertain estimates. Each forecast should show its horizon, evidence, assumptions, and data cutoff.

## Version 1 capabilities

1. **Company Q&A:** Answer questions from cited filings and company releases. Use structured financial data and ordinary code for figures and calculations; use document retrieval for explanations and comparisons. State when evidence is missing.
2. **Automatic updates:** Monitor covered SEC filings and selected company news. Summarize new information, compare it with earlier evidence, and send a Telegram alert with source links. Deduplicate repeated items.
3. **Expected price movement:** Include an experimental forecast with relevant alerts. Show the expected percentage move, likelihood of an upward move, an uncertainty range, key evidence, forecast horizon, and information cutoff. Never imply certainty when the historical evidence is weak.

## Initial companies and source coverage

- **Nebius Group (NBIS):** SEC 20-F annual reports and 6-K interim reports.
- **IREN (IREN):** Historical 20-F/6-K filings and amendments, followed by 10-K/10-Q/8-K reporting as issuer status changed. Preserve IFRS versus US GAAP and original versus revised figures; resolve coverage from actual submissions.
- **AST SpaceMobile (ASTS):** SEC 10-K annual reports, 10-Q quarterly reports, and relevant 8-Ks.
- Start news monitoring with official investor-relations releases. Add a broader news service after checking historical archive access, timestamps, and usage rights.
- Start with roughly two years of filing history, then assess how much additional point-in-time history is available for evaluation.
- Obtain historical market prices from a separate source; SEC filings do not include the price history required for share-price forecasting.

## Forecast definition and evaluation

- **Primary horizon:** expected share-price percentage movement over the next **5 trading sessions**. This is the initial choice for event alerts because it focuses on the near-term response to new information.
- Target split-adjusted USD price return excluding cash dividends. The origin convention (future regular-session open or close) remains open pending source verification. Freeze inputs at a declared cutoff; preserve publication, SEC acceptance and collector observation separately. Show market-data timestamps.
- Return a point estimate, an upward-move probability, and an uncertainty range only to the extent the backtest supports them. A **20-session secondary horizon** is included in the evaluation protocol; neither horizon has labels or backtest results yet.
- Build chronological, point-in-time backtests. Compare results with simple baselines such as zero return and a trailing-volatility range. Track forecast error, directional accuracy, and range coverage and width.
- Do not use later filings, articles, prices, or revised data in a historical forecast. Report sample size and limitations. With only three companies and a short event history, early forecast results are exploratory and cannot establish reliable predictive skill.

## Product architecture

Use a Telegram interface with a product orchestrator that routes requests to bounded components for filing and news retrieval, evidence-grounded answers, financial calculations, and a separate forecasting component. Background jobs collect and timestamp new source material, remove duplicates, and prepare alerts. Store source links, publication times, filing identifiers, financial-data periods and units, forecast cutoffs, and alert state so the same item is not processed twice.

The product's runtime design is separate from Codex's `learning-first-coding` skill. That skill guides the planning, implementation, review, and QA workflow used to build this project; it does not need to be embedded in the Telegram bot.

## Build sequence

1. **Evaluation set — starter artifacts completed.** 30 Q&A cases (10/ticker, 4 abstentions), 21 development/9 holdout; 36 historical event clusters (12/ticker) with a frozen candidate/exclusion inventory. Window 2024-10-01–2026-09-30; earliest qualifying observed clusters selected. Availability gaps are explicit.
2. **Source audit completed; corpus collection pending.** SEC/issuer examples retrieved; two price and two news candidates assessed. Resolve retention, availability, authenticated history coverage and usage rights before providers or stack. Telegram setup remains pending.
3. **Build filing-based Q&A.** Begin with a simple retrieval baseline and source citations. Add structured financial facts and tested calculations for numeric questions.
4. **Add monitoring and Telegram alerts.** Detect covered filings and selected news, compare each item against prior evidence, summarize supported changes, and send deduplicated alerts.
5. **Backtest the forecast.** Resolve origin/label gates; implement 5-session primary and 20-session secondary evaluations with grouped chronological splits, overlap purging and a 20-session embargo. Compare baselines before surfacing supported forecasts.
6. **Harden and measure.** Add retry handling, alert preferences, secure token storage, logs, and measurements for answer quality, detection, duplicate alerts, forecast performance, latency, and cost.

## Initial acceptance checks

- Answers cite evidence that supports each factual claim and use the right company, period, and units.
- Financial calculations can be reproduced from their inputs.
- The monitor detects new covered items, preserves their source timestamps, and avoids duplicate alerts.
- Alert summaries distinguish reported facts from interpretations and identify unresolved questions.
- Forecast backtests use only data available at each cutoff, report comparison baselines, and disclose weak or insufficient evidence.

## Milestone 1 artifacts and learning outcome

- [Q&A benchmark](evaluation/qa_benchmark.jsonl): prompts, grading references, source locations, periods, units, calculations and abstention criteria.
- [Event catalog](evaluation/forecast_events.csv): 36 clusters, individual member times and empty price/forecast fields.
- [Protocol](evaluation/forecast_protocol.md): scoring, 5/20-session label contract, leakage rules and gates.
- [Source audit](evaluation/source_audit.md): observed access, provider constraints, hashes, frozen inventory, effort and verification.

This is a starter evaluation contract, not a live corpus, executable model harness, backtest or bot. Four NBIS anchors precede trading resumption; no prices are invented. IREN revisions/accounting changes and ASTS compound-event timing remain explicit.

Next proposal: a small permitted/versioned filing corpus and Q&A baseline using development cases. Choose language, storage and orchestration after source constraints. Record holdout exposure and avoid repeated tuning on published gold answers.
