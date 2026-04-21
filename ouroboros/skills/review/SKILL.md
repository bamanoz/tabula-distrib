---
name: review
description: "Review current changes, risks, and regressions in Ouroboros style."
user-invocable: true
---

Perform a review of the current worktree.

1. Use `git_status`.
2. Use `git_diff` and `git_diff` with `cached=true` if useful.
3. If code inspection is needed, use file tools.

Primary focus:

- bugs
- behavior regressions
- missing tests
- architectural drift away from the Ouroboros concept

Findings first, summary second.
