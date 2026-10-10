---
name: yeet
description: Review the current project changes and create a conventional local commit; push only when the user explicitly asks to push.
---

# Yeet

When the user invokes `$yeet`, review and commit the intended current-task changes in this repository.

Inspect the current branch and upstream, `git status`, staged and unstaged diffs, and relevant untracked files before staging anything. Check that the changes form one coherent commit, look for accidental secrets or unrelated files, and draft a concise Conventional Commit message. Stage only the reviewed files for this task; do not use a blanket `git add .` when that could include unrelated or sensitive content. Review the staged diff before committing. If the intended scope is unclear, files contain secrets, or unrelated pre-staged changes would be included, stop and ask the user how to proceed.

Create the local commit with the reviewed message, then report its hash and summary. Do not amend existing commits, force-push, or change branches.

Push only when the user explicitly requests it, for example with `$yeet push` or “yeet and push.” In that case, push the new commit to the current branch's configured upstream only. If there is no upstream, or the push is rejected, stop and report the result; do not change remotes or force-push.
