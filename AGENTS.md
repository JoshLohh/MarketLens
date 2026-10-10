# Project Agent Instructions

## Load project context first

For every task in this repository, read `.codex/context.md` before planning, delegating, reviewing, or editing. Treat it as the concise, current project brief. For product behavior, architecture, data-source, monitoring, and forecasting work, also read the relevant parts of `PROJECT_PLAN.md` and inspect the implementation before making claims about what exists.

The source code is evidence of current implementation; `PROJECT_PLAN.md` describes intended product behavior; `.codex/context.md` records the active project constraints and progress. Call out conflicts or unknowns instead of silently guessing. When delegating, include the relevant context and constraints in the agent's task.

## Choose the right workflow

- Handle small, clear, reversible edits and straightforward questions directly. Do not run a Council for a tiny change.
- For substantial features, architecture choices, ambiguous work, migrations, or changes with meaningful privacy, security, data-loss, or forecasting risk, use `$codex-council:codex-mind` for the proposal and review stages. Use the smallest suitable Council mode and budget.
- Mind should return its build/revise/stop decision, blockers, dissent, confidence, and required checks. Treat the Council as decision support; its agreement is not proof that a change is correct.
- If the proposal has blockers or is not approved to build, resolve the blockers or stop. Do not send blocked work to implementation.

## Approval before implementation

Before making a substantial change, show the user the reviewed scope, the implementation approach, important tradeoffs or risks, and the checks that will establish completion. Wait for the user's approval before editing. Keep implementation within that approved scope; ask again if the scope or risk changes materially.

After approval, use `$codex-council:codex-hyper` for a substantial Mind-approved implementation when available. For small changes, implement directly. In either case, inspect the final diff and report what was changed, the checks actually performed, and any unresolved issues. Do not describe a check as passing unless it ran and passed.

## Project-specific requirements

- Keep company facts tied to source evidence, company, reporting period, and units. Separate reported facts from interpretation, cite sources in user-facing answers, and say when evidence is missing.
- Preserve source identity and publication time for filings and news; deduplicate alerts without discarding provenance.
- Treat the initial forecast horizon as five trading sessions. Show the horizon, evidence, assumptions, uncertainty, and information cutoff. Do not use information that became available after the forecast cutoff.
- Keep forecasting claims calibrated to chronological, point-in-time evaluation. Report sample size and limitations, and compare with the baselines in `PROJECT_PLAN.md`.
- Do not invent an implementation stack, data provider, or database choice where the repository and project context leave it undecided.

## Session closeout

When the user invokes `$handoff`, update `.codex/context.md` using the handoff skill. Do not rewrite the stable project constraints during a progress update. When the user invokes `$yeet`, follow the yeet skill; pushing requires explicit push intent such as `$yeet push`.
