# MarketLens

MarketLens is a learning project for a private Telegram research assistant covering Nebius Group (NBIS), IREN (IREN), and AST SpaceMobile (ASTS).

The planned assistant will answer company questions using cited filings and releases, monitor selected sources for updates, and provide experimental estimates of expected share-price movement. Any future forecast will need to show its evidence, horizon, assumptions, and information cutoff.

## Project status

The evaluation and source-audit milestone is complete. Application development has not started: there is no running bot, retrieval pipeline, price dataset, or forecast backtest yet.

The application language, framework, storage, and data providers are still open. Provider access, historical coverage, precise publication times, and data-retention/display rights need to be resolved before those choices are made.

## Evaluation materials

- [Q&A benchmark](evaluation/qa_benchmark.jsonl): 30 questions with reference answers, citations, periods, units, and calculation rules.
- [Historical event catalog](evaluation/forecast_events.csv): 36 event clusters for later forecast evaluation.
- [Evaluation protocol](evaluation/forecast_protocol.md): scoring, point-in-time rules, and 5-session and 20-session forecast horizons.
- [Source audit](evaluation/source_audit.md): observed source coverage, constraints, open decisions, and verification notes.
- [Project plan](PROJECT_PLAN.md): product goals and planned build sequence.

The event catalog has no price labels yet, so forecast performance has not been evaluated. The Q&A benchmark is a frozen test set; it is not the live document corpus used by a future retrieval system.

## Project principles

- Cite evidence and identify the company, reporting period, units, and source version.
- Keep reported facts distinct from interpretation and calculations.
- Preserve publication and observation times so historical evaluations do not use later information.
- Report missing evidence and forecast uncertainty clearly.

MarketLens is being built to learn about RAG, evaluation harnesses, agentic workflows, and related AI engineering tools. The project plan keeps the implementation choices open until source constraints are better understood.
