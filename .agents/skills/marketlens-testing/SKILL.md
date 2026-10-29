---
name: marketlens-testing
description: "Interpret MarketLens test/evaluation results in chat, explain file and function changes, run focused checks, and guide the user through broader evaluations. Use for testing and experiment iteration; use marketlens-learning-log for persistent session notes."
---

# MarketLens Testing Coach

Help the user understand and participate in the testing cycle. Deliver the
explanations in chat; the separate learning-log skill records completed work.
Read project instructions and `.codex/context.md` first, then the relevant plan,
implementation, tests and saved artifacts. Follow the latest user authorization
and test-ownership preferences; this skill adds no approval process.

## Interpret before changing code

- Read submitted output and matching saved runs. Check run/input/code identity
  before editing; retain the matching source when uncommitted implementation
  changes would otherwise make old runs hard to reproduce. Treat code and saved
  artifacts as evidence, and call out stale or conflicting progress notes.
- Explain the observed result, its practical meaning and its limits in plain
  language. Distinguish unit/contract tests from measured retrieval and answer
  quality. For RAG, keep expected-source presence, relevant row/header retrieval,
  packed evidence and generated-answer support separate. Interpret resource and
  cost figures with cache state, sample size and repeated-run limits.
- Inspect actual differing/missing cases before selecting the next experiment.
  Identify whether evidence was never retrieved, omitted during packing, rejected
  during answer validation, or incorrectly generated. Change one appropriately
  sized factor with a fixed reference and explicit tradeoff. Runtime code must
  not read expected answers or relevance labels; do not weaken an evidence gate,
  citation check or abstention rule to raise scores.

## Explain changes as they happen

- For each meaningful code change, identify the file, added/modified/removed
  functions, what each does, why it changed, and its effect on the surrounding
  flow. Define unfamiliar terms when needed. Give a concrete before/after rule
  when that makes the change easier to understand.
- Say when no functions were removed, and when documentation/configuration has
  no function changes. Keep implementation, proposed behavior and measured
  improvement distinct. Scale the detail to the change; avoid line-by-line
  narration and unexplained test counts.

## Split testing responsibilities visibly

- Label every user-run test or evaluation command with the user's stage:
  **Stage 1 — software tests**, **Stage 2 — retrieval**, or
  **Stage 3 — answer generation**. If a command only validates an artifact,
  still identify its stage and explain that validation checks integrity, not
  quality. Label each command separately when a sequence spans stages.
- Current default: the assistant runs focused individual checks for changed
  behavior; the user runs the complete suite and broader evaluation experiments.
  A later explicit instruction can change that split. Do not run the user's
  broader evaluations while waiting for their output.
- Report each assistant command with its working directory/interpreter, purpose,
  actual result, and what was learned or protected against. Choose meaningful
  counterexamples and invariants. A passing test proves its stated check, not
  general model quality. Explain failures and repairs honestly, including a bad
  test fixture; never hide a genuine defect by weakening its assertion.
- Give the user copyable sequential commands, fresh run IDs, the purpose of each
  run and the fields/results to inspect. Ask them to stop and share a failure
  before proceeding. Explain likely setup time and any network or paid-call
  implications. Preserve existing privacy, budget and experiment constraints.
- Leave unrun checks pending. Once user output arrives, interpret it against the
  saved artifacts and fixed reference, then continue the authorized iteration.
  Keep the final response self-contained: result/meaning, changes and reasons,
  focused commands/results/learning, limitations, and the user's next commands.

Record completed work through `marketlens-learning-log`. Update the concise
context only under the project's explicit context-update rules. Neither skill
authorizes commits, pushes, paid generation or external messages by itself.
