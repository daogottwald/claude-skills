---
name: stack-plan-series
description: Decide how a body of work should be carved into a reviewable patch series before first review — the ordering, the boundaries, what to front-load, whether to split across bugs — so every prefix leaves the tree valid. Use when planning or reshaping a series for review — "how should I split this work into commits/PRs", "organize/reorganize my patches for review", "prepare this branch for review", or a series that mixes concerns or only makes sense taken as a whole. To restructure commits that already exist, see stack-reorganize.
---

# Planning a patch series for review

Mozilla reviews commits **individually** — there's no "review the squashed
series" workflow. So the unit of quality is the single commit, and this skill is
about choosing the shape — ordering and boundaries — before first review.

`docs/contributing/reviewable-patch-series.md` is the shape to aim at: the goals
(every prefix builds, lints and tests; every commit a natural standalone
increment; no forward references), the sources of review friction, the
strategies that reduce them (land-early nuggets, front-loading risk, refactor
before behavior change), what to split across bugs, the widely-used-API
trade-off and a worked example. Read it first; this skill covers how to arrive
at that shape and who executes it.

## The envision method

The hard part is inventing a fresh "origin story" for the final tree, independent
of how the work actually happened.

1. **List the original commits and, per commit, the *logical units*** — individual
   cleanups, orthogonal behavior changes, plumbing, refactors.
2. **Reorder the units freely, ignoring which commit they came from.** Brainstorm
   orderings; the goal is a sequence where each step is a natural increment and no
   step forward-references a later one.
3. **Hold the intermediate states in your head.** The main constraint is that
   comments and code in earlier patches can't refer to concepts introduced later.
4. **Settle on the ideal grouping and draft the commit messages** before touching
   history.

Example: commits `A:[M,N,O] B:[P] C:[Q,R,S]` might re-envision as
`R, M, Q, P, [N,O], S` — units regrouped by what tells the cleanest incremental
story, not by their original home.

## Predict the reviewer

Review each planned patch yourself and anticipate the objection; shuffle
boundaries until the only thing left to disagree with is the *effect* of the
change, never the mechanics of how it's presented. For a concrete self-review
pass over the working diff, run **`/code-review`** before pushing — **medium**
effort by default (fewer, high-confidence findings), bumped to **high** for a
risky or large behavior change; add `--fix` to apply the cleanups it finds.

## Executing the plan

Once the target shape is settled, this is a mechanics problem — use the skills
below. All of them hold the net-diff contract: a reorganization ends at the same
overall diff as the original work, so a better implementation you spot along the
way needs the user's agreement first.

- Split one commit by concern → **`stack-split-commit`**.
- Restructure a stack (reorder, drop, resplit, weigh against review history) →
  **`stack-reorganize`**.
- Drop a superseded / move-then-unmove commit at source → **`stack-reorganize`**'s
  `references/drop-superseded.md`. This is the one intervention that deliberately
  changes the net tree.
- Push the result for review (preview, scope, re-wire dependency edges) →
  **`submit-stack`** / **`submit-single`**. Never re-push without approval.

In a Jujutsu checkout the mechanics skills are `stack-split-jj` and
`stack-reorganize-jj`.
