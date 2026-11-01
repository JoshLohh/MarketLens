---
name: marketlens-docs
description: Keep MarketLens's local docs/ learning guide in sync after project files, features, architecture, or evaluation evidence change. Use after implementation work or when the user asks to refresh the project guide; skip unrelated code tasks and simple factual questions.
---

# MarketLens Documentation Guide

Update the local `docs/` learning guide after meaningful MarketLens work so
the user can understand what the project contains, how its pieces connect, and
what has actually been verified. This skill updates documentation; it does not
authorize application-code changes, tests, external calls, commits, or pushes.

## Read the current evidence first

1. Read `AGENTS.md` and `.codex/context.md` before planning edits.
2. Read the relevant parts of `PROJECT_PLAN.md`, the root `README.md`, and the
   existing pages in `docs/`.
3. Inspect changed source, tests, and saved run artifacts. Source is evidence
   for what currently runs; plans describe intended behavior; saved outputs
   support claims about checks. Call out stale or conflicting notes instead of
   copying them forward as current facts.
4. Do not update `.codex/context.md` unless the user invokes `$handoff` or
   explicitly requests a context update. Use `marketlens-learning-log` after
   meaningful documentation work when available.

## Keep the guide coherent

The current entry point is `docs/README.md`. Update the existing pages when
possible instead of creating near-duplicates:

- `docs/architecture.md` explains the user-visible flow and data stores.
- `docs/stack-and-tools.md` explains each major service, tool, dependency,
  where it runs, why it is used, and whether it is active or still optional.
- `docs/repository-map.md` explains files, folders, and how they connect.
- `docs/function-guide.md` explains runtime functions in plain language.
- `docs/checks-and-status.md` records what checks mean, their evidence, and
  current limitations.

The current architecture diagram is Mermaid in `docs/architecture.md`; older
PNG files are historical snapshots. Keep the stack page and Mermaid flow aligned
when dependencies, services, or runtime connections change.

When files or modules are added, removed, or repurposed, update the repository
map. When a runtime function or important method changes, add, revise, or remove
its entry in the function guide and explain where it sits in the flow. The
current guide inventories `marketlens_bot/` functions and maps the main
evaluation functions the bot reuses; keep that scope clear if the codebase
grows. Explain domain terms simply and avoid copying implementation details
that do not help the user follow the product.

When a service, library, tool, model, data source, or runtime is added, removed,
or changes status, update `docs/stack-and-tools.md`. Say what job it performs,
where it runs, what information it receives or stores when relevant, and
whether it is used by the current bot, a manual check, or only a future path.
Distinguish a user's reported install or check from a state independently
verified in the current session. Do not label a service active just because an
adapter or setup command exists.

Update the architecture prose and diagram when a change alters the question
path, data stores, external services, privacy boundary, or other major
relationships. A code change that does not alter those relationships usually
needs no diagram change. Update the Mermaid diagram in place for changes to the
flow; if a separate raster is useful and being refreshed, use the available
image-generation skill when appropriate, save it inside `docs/`, and verify the
embedded filename exists. If the diagram cannot be refreshed, say so clearly
rather than presenting an outdated diagram as current.

## Report status carefully

- Separate implemented behavior, intended future work, and measured evidence.
- For every result, identify its input or run when available, what it proves,
  and its limits. A passing software test does not prove retrieval quality or
  answer quality.
- Preserve the fixed pilot, retrieval settings, company/source provenance,
  consent and spending boundaries, and the distinction between local and live
  services. Do not infer that a configured adapter or consent command makes a
  provider active.
- Follow the user's current test-ownership instructions. Do not run a suite or
  broader retrieval/answer evaluation unless authorized. Record only checks
  that actually ran; leave other checks explicitly pending.
- The project intentionally ignores `docs/` in Git. Keep the learning guide
  local and do not change that ignore rule or add its files to version control
  unless the user asks.

## Validate and explain

Check local Markdown links and image references, confirm important new or
changed functions are represented in the function guide, and check that
`docs/` remains ignored. Run `git diff --check` when useful. These checks
validate documentation structure, not application behavior; do not describe
them as product tests.

After updating, explain in chat which documentation files changed and how they
help the user follow the feature. State that no functions changed when the work
was documentation-only, list the checks actually run, and identify any
unverified behavior or stale source that limited the update. Do not commit or
push unless explicitly asked.
