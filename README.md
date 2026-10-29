# MarketLens

MarketLens is a learning project for a private Telegram research assistant covering Nebius Group (NBIS), IREN (IREN), and AST SpaceMobile (ASTS).

The planned assistant will first answer company questions using cited filings and releases. Later stages may monitor selected sources for updates and provide experimental estimates of expected share-price movement. Any future forecast will need to show its evidence, horizon, assumptions, and information cutoff.

## Project status

The evaluation and source-audit milestone is complete. The repository now includes offline filing-corpus, retrieval, and answer-evaluation code. Product application development has not started: there is no running bot, production ingestion service, price dataset, or forecast backtest yet.

The development results support beginning a private Telegram Q&A prototype, with remaining answer-validation failures handled explicitly. This is not a production-readiness or general accuracy claim. The next planned product step is the Telegram Q&A experience; there is not yet a running bot. Scheduled news/event monitoring and alerts are deferred until that interaction is built and useful. The application language, framework, storage, and data providers remain open. Embedding search does not by itself mean a production vector database has been selected or is required; the current vector index and cache are local evaluation tools.

## Evaluation materials

- [Q&A benchmark](evaluation/qa_benchmark.jsonl): 30 questions with reference answers, citations, periods, units, and calculation rules.
- [Historical event catalog](evaluation/forecast_events.csv): 36 event clusters for later forecast evaluation.
- [Evaluation protocol](evaluation/forecast_protocol.md): scoring, point-in-time rules, and 5-session and 20-session forecast horizons.
- [Source audit](evaluation/source_audit.md): observed source coverage, constraints, open decisions, and verification notes.
- [Filing Q&A evaluation](evaluation/filing_corpus/README.md): the fixed SEC pilot, retrieval comparisons, and answer checks.
- [Project plan](PROJECT_PLAN.md): product goals and planned build sequence.

The event catalog has no price labels yet, so forecast performance has not been evaluated. The Q&A benchmark is a frozen test set; it is not the live document corpus used by a future retrieval system.

## Project principles

- Cite evidence and identify the company, reporting period, units, and source version.
- Keep reported facts distinct from interpretation and calculations.
- Preserve publication and observation times so historical evaluations do not use later information.
- Report missing evidence and forecast uncertainty clearly.

MarketLens is being built to learn about RAG, evaluation harnesses, agentic workflows, and related AI engineering tools. The project plan keeps the implementation choices open until source constraints are better understood.

## Hybrid retrieval

For filing questions, MarketLens is evaluating a search approach that combines
exact wording with meaning-based matching. The goal is to find relevant filing
passages while preserving financial labels, reporting periods, units and source
citations in the evidence given to an answerer.

This work is currently an offline evaluation on a fixed SEC pilot and is the
intended retrieval direction for the planned Telegram Q&A product. It is not yet
integrated into a running bot. Evidence coverage and answer quality are measured
separately. The experiment's local vector index is not a production vector
database; a persistent database choice remains open until the product's storage
and update needs are known.
