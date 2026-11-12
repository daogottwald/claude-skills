---
name: submit-stack
description: Push a rewritten or multi-commit stack to code review (Phabricator/moz-phab) safely — preview before pushing, keep WIP commits WIP, avoid no-op reviewer spam, and re-wire the stack dependency edges the tool leaves stale. Use when re-pushing a reorganized/split/rebased stack, or submitting a multi-commit stack for review.
---

# Submitting a stack for review

Builds on the **`submit-single`** skill for the base moz-phab mechanics — the
review gate (show the diff in `lrv` before pushing; a "submit"/"resubmit"
instruction is not a review waiver), the approval gate, preview-by-declining
(`printf 'no\n' | moz-phab submit …`), `--single`, the `-m` update note, and
verify-after. This skill adds what's
specific to pushing a **stack** (after a split, reorder, drop, or rebase), where
the tool notifies reviewers and carries stack state it won't reconcile for you.
Scope, preview, push, re-thread, verify — don't fire and assume.

**Post inline replies before the push.** Before re-pushing any revision in the stack that has
reviewer inlines, post the replies you intend (the `phab-comments` skill). A reply shares its
thread's diff and Phabricator ports both together; posted after the push, it has to name the
line the UI moved the thread to, which only the user can read off the web UI (D335930 waited
on that). This holds for a per-commit `--single` push as much as for a range.

## 1. Read the preview for stack side-effects

A range preview (`printf 'no\n' | moz-phab submit <base> <tip>`, declined) surfaces
side-effects beyond "update the diffs":

- **WIP flip** — a `!! "Changes Planned" status will change to "Request Review"`
  line means a commit you left WIP will be un-WIP'd (review requested
  prematurely). Usually a reason to narrow the range (§2).
- **Bug re-attribution** — `!! Bug ID ... will change from X to Y`. Expected if
  you reworded to a new bug; a surprise otherwise.
- **`(New)` vs `(D…)`** — a commit with no `Differential Revision` trailer
  becomes a new revision; the rest update in place. A clean split leaves exactly
  one `(New)`.
- **Scope** — every commit in the range is listed; unchanged ones still get
  re-diffed and re-parented, notifying their reviewers.

## 2. Scope the range

Submit only what should move. To keep a WIP tip out of the push, submit a
sub-range ending below it: `moz-phab submit <base> <end>`. `<base>` must be a
commit reachable from the branch, named as the commit *below* the first one you
want (`<first>~1`); a remote ref such as `origin/main` as `<base>` fails with
"Failed to find any commits" even when the stack sits directly on it. moz-phab amends the
range and **rebases the trailing commits on top automatically** — so the WIP tip
survives with a new hash but is neither submitted nor un-WIP'd (verify it's still
there afterward, §4).

Re-upload every revision that now sits on different code than when it was
uploaded, because it or a commit below it moved, changed or was dropped. Lando
applies each stored diff on top of the one below it, and a hunk's context still
holds the neighbours of the old order, so such a revision can fail to land
(D340488 after a sink, 2026-11-11). A range-diff `=` shows that the commit's own
change is unchanged, not that its diff still applies at the new position:
range-diff may check less context than the diff Lando applies. Revisions above
the moved range sit on the same code as before when the range nets to its
original diff, and then need no push; submit narrowly and fix deps by hand (§3).

## 2b. Re-check every message in the range

A split, reorder or fold-in leaves messages describing the wrong scope, so run
the checker over the whole range before pushing — not just the commit you edited:

```
python3 ~/.claude/skills/submit-single/scripts/check-commit-message.py <base>..<tip>
```

Fix every `ERROR`; judge each `check`. See the Message gate in `submit-single`.

**A range that is WIP throughout gets no update note (`--wip` without `-m`); a note there is
noise a reviewer arriving fresh has to skip.** Otherwise the update note needs the user's nod
too, and on a range it is one note for every revision in it. `submit-single`'s rule is to print the exact `-m` text and
wait; a range makes that text carry more, since the same line lands on each
revision's timeline whether or not it describes what changed in that one. Show it
before pushing, and if one line cannot honestly cover the range, narrow the range
rather than writing a note that fits none of them.

## 3. Re-wire the stack graph — `submit` won't

`moz-phab submit` over a range updates each revision's diff and sets deps for *new* revisions, but only between revisions inside the range: a new floor whose base commit is an existing revision comes out parentless (D340746 on D340490, 2026-11-08), so link it with `phab.py link`; `--single` sets no edges at all, so a new revision submitted that way (a WIP tip, say) comes out parentless: link it with `phab.py link --child D<new> --parent D<below> --yes`, which re-reads the revision so you see the edge took. A range submit also
does **not** re-thread an existing stack after a reorder: stale "Depends On"
edges survive (e.g. a sunk commit still lists its old parent). `moz-phab reorg [start_rev] [end_rev]` recomputes the whole chain from the local order, touches only the mismatched edges, and prints "Reorganisation is not needed" when there are none. It covers a reorder or swap (it emits `children.set`, which *replaces* a child list, so no transient cycle arises and nothing needs unlinking first), a mid-stack insertion (it fixes the parent of the commit *above* the inserted range, which `submit` leaves dangling), and edge churn. It refuses to run when any local commit has no revision id, and refuses a non-linear remote graph without `--force`; a transaction failure part-way through leaves the stack half-reorganised, and reports that.

**It gets the floor of a partially-landed stack wrong, so read the preview it prints before confirming.** `stackGraph` keeps landed revisions and their edges while the local range excludes landed commits, so the landed base reads as remote-but-not-local: `reorg` emits `children.remove` on it *and* `abandon`, exempting only revisions already in the abandoned state. A WIP tip above the range goes the same way, so scoping the range (§2) matters as much for `reorg` as for `submit`. `--no-abandon` drops the abandon and still severs the edge. Stop if the preview names a revision you did not mean to abandon, and edit those edges by hand instead, computing the desired chain from the local order and editing **only the mismatches** — every edit is churn, so leave correct edges alone. Read the current edges from `stackGraph` first and again afterwards, with the bundled helper rather than curl: `~/.claude/skills/submit-single/references/conduit-and-edges.md`.

- Desired parent of each revision = the revision directly below it in
  `git log --reverse <base>..<tip>` (read each commit's `Differential Revision`
  trailer for its id).
- **The floor of the range is not necessarily at the base.** `<base>..<tip>`
  excludes already-landed commits, so the bottom open commit looks parentless —
  but on the review server it still depends on the **last landed revision**
  beneath the stack. Do **not** blindly `parents.remove` the floor's parent
  (that severs the open stack from its landed base); set it to the landed
  predecessor instead. Only remove a parent when the floor genuinely sits on the
  repository base with nothing landed below it. When in doubt, check what the
  floor depended on *before* the rewrite and preserve that edge.
- **Use the helper rather than hand-rolled curl.** `python3
  ~/.claude/skills/submit-single/scripts/phab.py` has `stack` (edges resolved to
  D-numbers), `link`/`unlink`, `abandon` and `comment`, each write dry-run by default:
  it prints the exact payload, needs `--yes`, then re-reads the object. Whichever way you
  call it, **verify the edge afterwards** rather than trusting that the edit returned OK —
  a malformed transaction applies nothing and still answers success.

Also **abandon** any genuinely superseded revision with a comment pointing to its
replacement — but abandoning does **not** remove it from the stack view. When you
replace a contiguous range with new revisions, the lowest abandoned revision keeps
its `revision.parent` edge into the live base, so the whole abandoned chain still
hangs off the live stack in the graph. To actually simplify the view, **detach the
island**: `parents.remove` that one connecting edge (the lowest abandoned
revision's parent pointing at the live base) — the rest of the chain comes away
with it. Re-wiring the live stack's own edges above does not do this, because the
live revisions were repointed away from the abandoned ones, not vice-versa.

## 4. Verify after

- **Branch tip** is what you expect — a sub-range submit rebased the excluded tip,
  so confirm it's still on the branch (new hash is fine).
- **Tree unchanged by the push**: `git diff <backup> <branch>` empty (the submit
  only amends messages/trailers, never content).
- **Dependency chain** — don't hand-check a subset; that's how the *ceiling*
  edge (the commit above an inserted range) gets missed. Run the bundled checker,
  which walks the local stack bottom→top and reports, for each revision, whether
  its server parent is the revision directly below it in commit order:

  ```
  python3 ~/.claude/skills/submit-stack/verify-stack-edges.py [<git-range>]
  ```

  It's a **safety net, not a linearity mandate**: it surfaces every edge that
  *differs from the plain commit order* so an *accidental* dangler can't hide.
  Most divergences it flags are bugs to fix (`moz-phab reorg`, or
  `differential.revision.edit` `parents.set` by hand, §3) — but a *deliberately*
  non-linear graph (a diamond, multiple parents, an intentional fork) will also
  show as "differs," and that's fine; confirm each flagged edge is what you meant
  rather than reflexively linearizing. The floor's parent it can't decide —
  confirm it's the intended landed predecessor.
- **Submitting acquires reviewers you did not ask for.** Herald adds group reviewers by
  touched path, so a revision whose message names one individual comes back with
  `#some-group [added]` — sometimes blocking — attached. Read the reviewer list back rather
  than assuming it matches the `r?` line (`phab.py show` prints it), and never reconcile it
  with `reviewers.set`, which drops exactly those group entries; add and remove instead
  (`submit-single/references/reviewers-and-status.md`).
- **Start the bot watcher once the edges are right**: `submit-single`'s "Watch for the review
  bots", passing every revision in the range. The bots apply each diff on top of its parent
  revisions as the graph stands when the diff is uploaded, so a revision pushed before its
  parent edge existed (an existing revision re-parented onto new ones inserted below it) fails
  its build with no bot comment. Where the push inserts new revisions below existing ones,
  submit the new lower commits first, wire the edges (§3), then submit the rest.
## 5. Review-only stacks

A personal-review push detaches from the bug and omits reviewers so it lands WIP with no
notifications; a stack adds a trailer strip and a disconnected base. For a pure local look
at a reorganized series, `lrv --series` (the `lrv` skill) is lighter and is the default —
reserve the WIP push for when you want the Phabricator-rendered view or a shareable
checkpoint. The recipe for both is
`~/.claude/skills/submit-single/references/wip.md`.
## 6. Splitting a revision that's already in review

Keeping review continuity while splitting a pushed revision is `stack-reorganize`'s; come
back here for the edge the tool won't set afterwards (§3).
