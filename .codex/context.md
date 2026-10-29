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

- ✅ **Fixed-pilot evaluation completed:** Keep the SEC pilot at 12 documents / 3,922 passages and use the same 21 development questions. The nine exposed holdout questions are regression checks only, not a blind test. Do not rebuild or expand the corpus.
- ✅ **Stage 2 — retrieval evidence:** `hybrid-exact-row-hints-2026-10-29-v1` (hash `fa181f528b15a7d79d58ae00f4159093d85626a41f28f9bcd27ada6d6a0b7517`) passes the evidence gate. Hybrid packs complete evidence for 19/19 answerable cases versus lexical 17/19, and 14/14 complete financial groups versus 13/14; both pack 14/14 financial rows with headers. Each arm finds at least one expected source for 21/21 questions and all expected sources for 20/21. These are retrieval/evidence metrics, not answer correctness.
- ✅ **Frozen retrieval candidate:** Use ticker-aware lexical plus local embedding retrieval, weighted rank fusion (0.75 lexical / 0.25 dense), fixed lexical floor 3, 128-token passage windows, `context-anchored-tail-v1` packing, and lexical tail depth 15. Hybrid is the intended Telegram integration path; lexical stays as a regression control. The encoder/runtime are scoped experiment settings. No permanent embedding provider, application runtime, or vector database has been chosen.
- ✅ **Stage 3 — paired answer review:** The user completed the approved run `paired-exact-row-hints-2026-10-29-v1` (hash `a0876278d39a824e36bea023bf49991166cca104ead896ada2e7565b48421abd`) and the offline grade `graded-d37e87cc345e.json`. Both arms scored 17 PASS / 4 UNKNOWN / 0 FAIL out of 21; each has two correct expected abstentions and no excessive abstentions. Lexical accepted 19 answers; hybrid accepted 18 and had one strict validation error on IREN-QA-07. Recorded configured-rate cost was USD 0.0207398 lexical and USD 0.0237164 hybrid. One paired sample does not prove a general quality winner or production readiness.
- ⚠️ **Known answer-review gaps:** The four UNKNOWNs per arm are two expected abstentions with inapplicable claim metadata, NBIS-QA-05's automatic annualization-multiplier check despite source-review support, and IREN-QA-07. Reviewers disagree on the lexical formula's units; hybrid's rejected text is intentionally not retained, so its validation cause is unknown. Preserve strict citation, period, units, and abstention checks. Do not repeat a paid pair merely to reduce UNKNOWN counts.
- ⚠️ **Stage 1 test record:** The latest recorded full suite had 171 passes and one stale prompt-wording assertion out of 172 tests. The corrected focused test passed in 0.863 seconds, and the user reported Stage 1 was okay; no full-suite output after that correction is recorded here.
- ➡️ **Next — Step 4 Telegram Q&A MVP:** Build an on-demand private bot using the fixed pilot and frozen hybrid retrieval path: question → company/question routing → retrieval → bounded evidence → checked calculation or LLM answer → validated citations or safe abstention. Make validation errors and unsupported questions understandable without exposing rejected model text. Do not add scheduled news monitoring or proactive alerts in this step; those are deferred. Choose only the minimal framework, runtime, and storage needed for the MVP, and keep the persistent vector-database decision open until requirements justify it.
