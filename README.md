# MarketLens

MarketLens is a learning project for a private Telegram research assistant covering Nebius Group (NBIS), IREN (IREN), and AST SpaceMobile (ASTS).

The planned assistant will first answer company questions using cited filings and releases. Later stages may monitor selected sources for updates and provide experimental estimates of expected share-price movement. Any future forecast will need to show its evidence, horizon, assumptions, and information cutoff.

## Project status

The evaluation and source-audit milestone is complete. The local Telegram Q&A prototype in `marketlens_bot/` has returned its first checked calculation through Telegram. This verifies one answer path, not general answer quality or production readiness. There is no production ingestion service, price dataset, or forecast backtest yet.

The development results support the private Telegram prototype, with remaining answer-validation failures handled explicitly. This is not a production-readiness or general accuracy claim. Scheduled news/event monitoring, alerts, new-company ingestion, and forecasting are deferred. The selected local vector database is Weaviate; the prototype retains the evaluated application-side lexical/dense fusion. The existing local encoder and cache remain fixed pilot inputs.

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

The evaluated approach is being connected to the private prototype through a
local Weaviate collection. Its import uses the existing cached windows; the bot
must pass the development parity check before it will poll Telegram. Evidence
coverage and answer quality are measured separately. Native Weaviate hybrid
search and adding companies are future work.

## Local prototype status

The user reported that the offline software checks passed: 23 state, Telegram,
Weaviate-index and readiness checks, plus five routing, citation, abstention and
fake-provider checks. The pinned Weaviate 1.40.3 image is running locally, and
the cache-only bootstrap confirmed all 8,279 existing cached windows. The user
then reported that Weaviate matched all 21 development retrieval cases after
the dense check was narrowed to the actual top-50 candidate list. This verifies
retrieval and evidence-pack parity for those cases, not answer correctness or
performance. One focused software regression test for that cutoff was added
after the earlier test runs; the user then ran it and reported that it passed.
Live OpenAI generation is enabled in the code. The user ran the activation
checks successfully; earlier live Telegram explanation attempts reached the output
limit, and a later user-triggered question returned a cited AI explanation.
This is one example, not a general answer-quality evaluation. It requires an API
key, a usable Chrony installation, and fresh reply-bound `/ai confirm v4`
consent. Otherwise questions use checked local calculations or cited filing
excerpts. The prototype uses a private sender and chat allowlist and a local
SQLite ledger. It keeps no question or answer text in that ledger.

The GPT-6 Luna adapter now makes one Responses request per attempt and reserves
for up to 40,000 input and 25,000 output tokens before sending. This uses the
32 KB request limit plus an 8,000-token formatting allowance; at the configured
conservative rates it reserves $0.0265 per attempt. A citation retry is allowed
only if the first settled cost leaves room for another full reservation within
the approved $0.03 per-answer cap. This is a local byte-based estimate,
not an OpenAI tokenizer guarantee. The $5 monthly cap is unchanged. The real
HTTP transport is enabled in code. The runtime provider factory returns no
provider if the API key, Chrony, or verified TLS setup is unavailable, so local
answers remain usable. AI notice version 4 revokes older AI consent when state
is opened while retaining privacy consent and budget records.
The user previously ran 42 Stage 1 software checks and 10 Stage 3 fake-provider
answer checks after the budget change. The newer Stage 1 run below also covers
the clock adapter and v3 consent migration.

An authenticated clock adapter supports both a separate readiness check and
the guarded provider construction path. It runs Chrony once in query-only mode
against Cloudflare's NTS service,
accepts only a parsed offset within one second, and never adjusts the Mac clock.
The check sends no question or filing excerpt, but Cloudflare can see the Mac's
internet address. One authenticated source does not independently prove that
the source's time is accurate. The runtime provider factory connects
`ChronyNTSClock` to `ClockGuard` when configured. A time sample is requested
only when an opted-in provider begins dispatch, not when the bot starts.

Install Chrony yourself, then run this standalone clock-service check from the
repository root. It is a time-only smoke check, not Stage 1, 2, or 3:

```bash
brew install chrony
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
MARKETLENS_CHRONYD_PATH="$(brew --prefix chrony)/sbin/chronyd" \
  PYTHONPATH=. "$MARKETLENS_PYTHON" -m marketlens_bot clock-check
```

A pass means Chrony completed its authenticated NTS check and estimated the
Mac's clock offset at no more than one second. A missing executable, network
failure, malformed response, or larger offset fails closed. The command neither
sets the system clock nor contacts Telegram, Weaviate, or OpenAI.
Chrony gets up to 15 seconds to obtain a sample; the wrapper allows 17 seconds
for it to exit.
Chrony's [`-Q` mode](https://chrony-project.org/doc/4.9/chronyd.html) reports
the offset without adjusting the clock; Cloudflare's
[NTS documentation](https://developers.cloudflare.com/time-services/nts/)
describes the TLS key
exchange and authenticated time packets used by this configuration.

After that, run **Stage 1 — software rules** for the new clock adapter and the
consent, state, and provider contracts. This uses a fake Chrony process and
makes no time-service, Telegram, or OpenAI requests:

```bash
PYTHONPATH=tests python3 -m unittest \
  test_bot_trusted_clock test_bot_state test_bot_telegram test_bot_provider -v
```

The user ran this clock check successfully with an estimated offset within one
second and then ran the Stage 1 command above; all 50 tests passed before the
runtime factory was added. They cover the clock adapter, consent migration,
state ledger, Telegram rules, and existing fake-provider safeguards. They do
not call Telegram or OpenAI. The earlier Stage 3 fake-provider answer run
passed 10 checks before the runtime-factory integration test. Later the user
ran four focused Stage 1 factory checks and one Stage 3 integration check; all
passed with fake clocks/transports and no live OpenAI request.

The next safety change asks OpenAI for the standard service tier explicitly and
checks the returned tier before settling a reservation. After a response, a
separate clock check verifies that the request began and finished in the same
Singapore month. If the month or charge is uncertain, its reservation remains
and counts against every month's available local budget. `/budget` shows that
global unresolved total. The user ran 53 Stage 1 and four Stage 3 checks for
these safeguards; all passed. The later live-activation changes need the
user-run checks below.
The reservation uses a byte-based token estimate and dated prices; it cannot
guarantee the final OpenAI bill if actual usage, rates, or service tier differ
from the assumptions.

**Stage 1 — software rules** (budget, clock and Telegram behavior; local fakes):

```bash
PYTHONPATH=tests python3 -m unittest test_bot_provider test_bot_state test_bot_telegram -v
```

The activation also adds a cited local fallback when an OpenAI explanation
fails. Because `answer.py` is part of the parity identity, rerun **Stage 2 —
retrieval evidence** once before restarting the bot. Stop any running bot with
Ctrl-C first; keep Docker Desktop open. This uses the same 21 development
questions and existing vectors, and makes no OpenAI request:

```bash
set -a
. "$HOME/.config/marketlens/weaviate.env"
set +a
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
docker compose --env-file "$HOME/.config/marketlens/weaviate.env" up -d weaviate
PYTHONPATH=. "$MARKETLENS_PYTHON" -m marketlens_bot parity --split development
```

The user ran these activation checks: 59 Stage 1 checks passed, all 21 Stage 2
development parity cases matched, and 15 Stage 3 checks passed. Those checks made no
live OpenAI request. A later user-triggered question returned a cited explanation.

**Stage 3 — answer generation** (fake-provider answers and citations):

```bash
PYTHONPATH=tests python3 -m unittest test_bot_answer test_bot_generation -v
```

The user has now shared a live Telegram response for IREN FY2025 revenue:
USD 501.023 million, period FY ended 2025-06-30, US GAAP, cited to the IREN-S1
10-K. The response included the corrected selected-filing coverage date
(2025-08-28). This confirms one local calculation, source citation and coverage
display path. It does not verify broad natural-language explanations, which
remain unavailable without an answer provider.

The bot process expects `WEAVIATE_API_KEY`, `MARKETLENS_TELEGRAM_TOKEN`,
`MARKETLENS_CHAT_ID`, and `MARKETLENS_SENDER_ID` in its environment. The local
Weaviate key is stored outside the repository in the owner-only file
`~/.config/marketlens/weaviate.env`; load it into a shell with the commands
below. Never paste keys or tokens into issue reports or test output. State
defaults to `~/.local/share/marketlens`; set `MARKETLENS_STATE_DIR` to select
another owner-only directory.

The current code expects the existing pilot release and 128-token cache under
`evaluation/filing_corpus/local/`, with the pinned experiment virtual
environment described in the filing-corpus README. It refuses to encode the
passages again if the cache is missing. Weaviate's image is pinned in
`compose.yaml`, binds to `127.0.0.1`, requires an API key, and keeps its data in
a named volume. Cache import and the 21-case development parity have been
verified with Docker and the local model environment. The user-run Mac benchmark
passed startup, retrieval, queue and sampled-memory gates; its limitations are
recorded in the learning log. Holdout regression remains a separate optional
check and is not a substitute for the fixed development parity.

From the repository root, load the private key file, start Weaviate, then use
the existing experiment interpreter:

```bash
set -a
. "$HOME/.config/marketlens/weaviate.env"
set +a
docker compose --env-file "$HOME/.config/marketlens/weaviate.env" up -d weaviate
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
PYTHONPATH=. "$MARKETLENS_PYTHON" -m marketlens_bot bootstrap
PYTHONPATH=. "$MARKETLENS_PYTHON" -m marketlens_bot parity --split development
```

The bootstrap imports cached vectors only and refuses a mismatched collection.
The parity command compares the same 21 development questions with the local
control and records a content-free result. It checks vector scores, passage
ranking and bounded evidence packing; it does not measure answer correctness.
The nine exposed holdouts can be checked separately with
`PYTHONPATH=. "$MARKETLENS_PYTHON" -m marketlens_bot parity --split holdout`;
they are regression checks, not a blind evaluation. Do not interpret parity as
answer quality.

The authenticated clock module does not change retrieval code or the inputs to
the saved parity identity, so the already reported 21/21 result remains the
relevant Stage 2 evidence; do not rerun it for this clock-only change.

### Measure local prototype readiness

The performance benchmark uses only the saved query-only projection from the
fixed 21-question development run. It does not load answer labels, rebuild the
corpus, re-encode passages, write vectors, call Telegram, or call OpenAI. It
restarts Weaviate five times and starts a fresh app worker each time; host OS
caches remain warm. The worker uses a temporary SQLite state directory and
removes it when done. The named Weaviate volume is preserved. Stop the polling
bot with Ctrl-C before running it. Keep Docker Desktop open and the Weaviate
service started.

From the repository root:

```bash
set -a
. "$HOME/.config/marketlens/weaviate.env"
set +a
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
PYTHONPATH=. "$MARKETLENS_PYTHON" -m marketlens_bot.perf_benchmark \
  --cold-starts 5 --repetitions 30
```

Before the Docker measurement, run **Stage 1 — software rules** for the
query-only input guard, p95 calculation, memory cap, and balanced queue schedule:

```bash
PYTHONPATH=tests python3 -m unittest test_bot_perf_benchmark -v
```

The user ran this command twice; all six Stage 1 checks passed on both runs.
The user also completed the documented Stage 2 Mac benchmark and all four
performance targets passed. The measured startup, retrieval, queue and memory
results and their limits are recorded in `docs/learning-log.md`.

The command checks the fixed readiness targets: app startup at most 60 seconds,
warm retrieval-through-pack p95 at most 500 ms, two sequential local checked
answers p95 at most 1.5 seconds, and sampled app plus Weaviate memory at most the
smaller of 2 GiB or 25% of physical Mac RAM. The queue simulation prefixes each
question with its ticker to satisfy the bot's current routing rule, then sends
synthetic updates through the local Poller, Bot, and temporary SQLite ledger.
The Telegram client uses a stub that discards reply text and makes no network
request. The report
states its sample counts and memory method; Docker Desktop VM overhead is not
included. Startup ends when the local answerer, verified collection, and Poller
objects are ready; it excludes a parity recheck and the first Telegram poll.
This performance run does not repeat retrieval parity or establish
answer-generation quality. It never removes the Weaviate volume; do not use
`docker compose down -v` for this measurement.

### Connect a private Telegram account

Create a bot through Telegram's `@BotFather`, keep its token private, open the
new bot's private chat, and send `/start`. With the Weaviate service running
and the development parity command already passed, read the bot token without
echoing it and ask the setup helper for the numeric allowlist IDs:

On macOS, install the pinned Telegram dependency into the existing experiment
environment once. It lets the Telegram connection use macOS certificate trust
while retaining certificate and hostname verification:

```bash
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
"$MARKETLENS_PYTHON" -m pip install -r marketlens_bot/requirements.txt
```

Then enter the Telegram token and look up the IDs:

```bash
read -rs "MARKETLENS_TELEGRAM_TOKEN?Telegram bot token (input hidden): "; echo
export MARKETLENS_TELEGRAM_TOKEN
MARKETLENS_PYTHON=evaluation/filing_corpus/local/retrieval_experiments/hybrid-dev-v1/venv/bin/python
PYTHONPATH=. "$MARKETLENS_PYTHON" -m marketlens_bot telegram-ids
```

The helper prints IDs only when the pending updates identify exactly one
private `/start` sender. It does not print message text or acknowledge the
update; Telegram confirms updates only after a later `getUpdates` call uses a
higher offset ([Bot API](https://core.telegram.org/bots/api#getupdates)). If
the helper reports that there is no unique sender, check that only your
private `/start` is pending and retry. Enter the printed numbers and start the
bot in the same shell:

```bash
read "MARKETLENS_CHAT_ID?Private chat ID: "
export MARKETLENS_CHAT_ID
read "MARKETLENS_SENDER_ID?Telegram sender ID: "
export MARKETLENS_SENDER_ID
read -rs "OPENAI_API_KEY?OpenAI API key (leave empty for local-only answers): "; echo
export OPENAI_API_KEY
MARKETLENS_CHRONYD_PATH="$(brew --prefix chrony)/sbin/chronyd"
export MARKETLENS_CHRONYD_PATH
set -a
. "$HOME/.config/marketlens/weaviate.env"
set +a
PYTHONPATH=. "$MARKETLENS_PYTHON" -m marketlens_bot run
```

On first contact, reply to the bot's privacy notice with `/privacy accept v1`.
OpenAI consent is off by default; send `/ai on` and reply to the resulting
notice with `/ai confirm v4` to opt in. Once configured and opted in, a company
question and selected filing excerpts can be sent to OpenAI and incur charges.
Use `/ai off` to disable future AI requests. Without a configured provider or
consent, the bot uses checked local calculations or cited excerpts. Stop the
local process with Ctrl-C. Do not paste keys into chat or commit them.
OpenAI describes API training and retention defaults in its
[data controls documentation](https://developers.openai.com/api/docs/guides/your-data).

Before the Telegram setup, the user can run the focused **Stage 1 — software
rules** check for the new ID helper:

```bash
PYTHONPATH=tests python3 -m unittest test_bot_telegram.TelegramIdentityTests -v
```

The user-run Stage 3 fake-provider checks verify answer validation rules, not
answers from the real provider. The activation checks passed, but the bot has
not yet been exercised with a live OpenAI request.

The following checks require no OpenAI key or provider network calls. From
the repository root, run **Stage 1 — software rules** for the provider, spending
ledger and Telegram polling, then **Stage 3 — answer generation** for fake
provider responses and citation/abstention handling:

```bash
PYTHONPATH=tests python3 -m unittest \
  test_bot_trusted_clock test_bot_state test_bot_telegram test_bot_provider -v
PYTHONPATH=tests python3 -m unittest test_bot_answer test_bot_generation -v
```

The prior `answer.py` change invalidated the saved parity identity even though
the retrieval settings and cached corpus did not change. The user reran the
**Stage 2 — retrieval evidence** check and all 21 development cases matched.
The local fallback changes `answer.py`, which is included in the parity
identity. Rerun the 21-question Stage 2 parity command before restarting the
bot; no corpus rebuild or new vectors are needed:

```bash
PYTHONPATH=. "$MARKETLENS_PYTHON" -m marketlens_bot parity --split development
```

These checks do not establish live answer quality or measure Mac performance.
The provider no longer makes a separate token-count request. It reserves using
the maximum accepted request size and a large formatting allowance, then settles
to reported usage. The reservation is deliberately conservative and may exceed
the provider invoice; it cannot cap other API activity or control invoice
timing. Requests above the size or remaining budget bound fail closed; if an
OpenAI explanation fails, the bot returns a local calculation or cited
excerpts when possible. It says when no request was sent, or warns that a
request may have been charged once transmission was entered.

The command `python -m marketlens_bot data status` describes retained state.
`data purge` resets consent and enters a durable drain mode; `drain` discards
queued Telegram updates before normal handling resumes. These commands retain
spending and replay safeguards, the Weaviate volume, corpus and cache. They do
not delete Telegram chat history or OpenAI data. Stop the bot before these
operations; the single process lock prevents simultaneous ownership. Stopping
or rolling back the application must preserve the ledger and vector volume.

The first user-triggered live explanation fell back to cited excerpts; its exact failure was not retained. New fixed-message diagnostics describe the failure stage without displaying provider output or exception text. They require user-run checks and a refreshed development parity record before restart.
