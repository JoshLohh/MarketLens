---
name: handoff
description: Update this project's living context with verified session progress when the user invokes $handoff.
---

# Handoff

When the user invokes `$handoff`, leave `.codex/context.md` ready for the next project session.

Read `.codex/context.md` and inspect the current repository state with `git status` and the relevant staged and unstaged diffs. Review relevant untracked files when they contain work from this session. Use the current conversation for decisions and intent, but use repository evidence to determine what actually changed or is complete.

Replace only the contents under `## Active Progress` with a concise update containing:

- ✅ **Completed:** Changes that are present and complete, with useful file or feature references.
- ⚠️ **Technical Debt / Known Issues:** Issues observed in the code or work, plus important unresolved decisions. Do not invent bugs or present unverified suspicions as facts.
- ➡️ **Next Steps:** The smallest useful next actions, based on the current project plan and repository state.

Preserve the rest of `.codex/context.md`, including the tech stack and core rules. If a stack or decision is still unknown, keep it marked as undecided rather than filling it by inference. If no project work changed, say so in Active Progress instead of claiming completion. Save the file, then give the user a short summary of the handoff update.
