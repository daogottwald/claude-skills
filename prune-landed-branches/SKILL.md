---
name: prune-landed-branches
description: Delete local branches whose patches already landed upstream, verifying by content (virtual rebase) rather than commit subject and screening for backouts, so a branch holding still-needed work is never dropped. Use when asked to clean up, prune, or delete stale/obsolete/merged/landed branches, when `git branch` has accumulated dozens of entries, or before starting fresh work in a cluttered checkout.
---

# Pruning branches whose patches have landed

Delete local branches that hold nothing the upstream integration branch doesn't
already have. The whole difficulty is establishing "already has" for a patch
that landed **rewritten** — rebased, amended by review, retitled on the way in —
so its hash never matches and `git branch --merged` reports nothing.

The two obvious shortcuts both fail, in opposite directions:

- **Subject matching alone over-deletes.** A patch can land and then get *backed
  out*. Its subject stays in the upstream log forever (and the backout commit
  quotes it, so the subject matches twice), while the tree no longer carries the
  change. The branch is the only copy of work that still has to go back up.
- **Subject matching alone also under-deletes.** A patch retitled during review
  ("developer docs" landing as "source docs") looks unlanded, so a genuinely
  dead branch survives the sweep.

And one case defeats *both*, because it defeats commit-level comparison
entirely: a patch reworked during review lands under the same bug with a
different approach, changing its subject and its implementation at once. Only
the bug identifier still ties the branch to what landed.

So: test **content**, screen for **backouts**, use subjects as a fallback where
the content test is inconclusive, and finish with a **bug-level sweep** over
whatever survived.

## Procedure

Throughout, `$UP` is the upstream integration ref the work lands on
(`origin/main`, `origin/autoland`, …) — a remote ref, never a local mirror of
one, which is usually stale.

### 1. Confirm the upstream ref is current

```bash
git log -1 --format='%h %cd' $UP
```

A ref that is days old will report recently-landed patches as unlanded. Fetch
if needed.

### 2. Enumerate candidates

```bash
git for-each-ref --format='%(refname:short)' refs/heads/
git worktree list          # branches checked out elsewhere cannot be deleted
```

Set aside up front, before any analysis:

- **Long-lived tracking branches** (`main`, `beta`, release branches). They are
  thousands of commits behind or ahead by design; never candidates.
- **Branches checked out in another worktree.** Git refuses to delete them, and
  they are usually someone's live tree. Report them; don't try to free them.
- **Branches with zero commits ahead of `$UP`.** They hold no patches at all —
  stale pointers at old upstream commits. Legitimate to delete, but they are a
  *different* category from "patches that landed": call them out separately and
  confirm, since one may be a deliberate base marker.

### 3. Triage by name and age — to prioritize, never to justify

Two cheap signals say *where to look first*. Neither is ever grounds for
deleting on its own; they only order the work and tell you which branches
deserve the extra duplicate check in step 5.

**Scratch names.** Branches named for a *mechanism* rather than a change go
stale fastest — they were snapshots of a moment, and the moment passed:

```bash
rg -i 'backup|tmp|temp|wip|rebase|reorder|probe|try|fixup|prototype|presubmit|_[0-9]+$|-v[0-9]+$'
```

A `-rebase`, `-backup`, or `_1` branch almost always has a sibling holding the
same work in better shape, so run step 5 against these first. Beware the
counter-case: a name like `try-…` can mark a CI push someone is still reading,
and `-rebase` can be the *only* copy of a patch that was backed out upstream.

**Age.** Compare two dates — they answer different questions:

```bash
git for-each-ref --sort=committerdate --format='%(refname:short)|%(committerdate:short)' refs/heads/
git reflog show --date=short "$b" | head -1 | sed -E 's/.*@\{([^}]*)\}.*/\1/'
```

Tip date is when the work was written; the reflog date is when the *branch*
last moved (a reset, a rebase, a fresh checkout). A branch with an old tip but
a recent reflog entry is being actively reworked — leave it alone. The reverse,
old on both, is a stale snapshot. In a checkout where branches are created and
never touched again the two dates coincide and the reflog adds nothing; say so
rather than implying it corroborated anything.

Old **and** scratch-named is the strongest prompt to look closely. It is still
only a prompt: the content test decides.

### 4. Content test: virtually rebase each commit onto upstream

For each commit a branch has beyond `$UP`, apply just that commit's delta onto
the current upstream tree and see whether anything changes. `git merge-tree`
does this with no checkout and no working-tree mutation:

```bash
base_tree=$(git rev-parse $UP^{tree})
res=$(git merge-tree --write-tree --merge-base="$h^" $UP "$h" 2>&1)
if   [ $? -ne 0 ];             then echo "conflict — indeterminate"
elif [ "$res" = "$base_tree" ]; then echo "empty on rebase — content is upstream"
else                                echo "real change — NOT upstream"
fi
```

Read the three outcomes precisely:

- **empty on rebase** — the delta is already present upstream. Conclusive.
- **real change** — the delta is not present. Conclusive, and the branch stays.
- **conflict** — inconclusive, *not* evidence either way. Most often the commit
  depends on branch predecessors that aren't upstream, so it can't apply
  standalone. Fall through to step 6 for these.

A branch is deletable only if **every** commit it has beyond `$UP` clears.

### 5. Collapse duplicate copies

A branch can be dead without anything having landed: a scratch copy whose work
also lives, in equal or better shape, on a sibling branch. Fingerprint each
branch by the set of subjects it carries beyond `$UP`, and look for collisions:

```bash
git log --format='%s' $UP..$b | sed -E 's/ +r[?=][^ ]*$//' | sort | md5sum
```

A collision is a *lead*, not a verdict — two branches can carry the same patch
at different review rounds or on wildly different bases. Confirm with
`git patch-id`, which is base-independent, and never with `git diff <a> <b>`
(that compares whole trees, so branches cut from different bases show thousands
of unrelated files and tell you nothing):

```bash
git show "$h" | git patch-id --stable
```

Three outcomes:

- **Identical patch-ids** — genuine redundant copies. Keep one; prefer the one
  with the descriptive name over the scratch-named one.
- **Same subjects, different patch-ids** — different rounds of the same work.
  Keep the *newer* one, which usually means the post-review version (`r=`
  rather than `r?`) or the one carrying extra fixups. Diff the two commits
  directly to see which is ahead.
- **One branch's set is a subset of another's** — the smaller is typically a
  pre-submit snapshot of the larger. Check the shared commits' patch-ids; if
  they match and the larger branch only adds to them, the snapshot is dead.

### 6. Subject fallback for the inconclusive ones

Build a subject index once (cheap, and far faster than a `--grep` per commit
over a large history):

```bash
git log -n 120000 --format='%s' $UP > /tmp/landed_subjects.txt
```

Match the branch commit's subject with any review-tag tail stripped, since the
tail changes on the way in (`r?user` becoming `r=user`, reviewers added):

```bash
norm=$(printf '%s' "$s" | sed -E 's/ +r[?=][^ ]*$//')
grep -qxF "$norm" /tmp/landed_subjects.txt || grep -qF -- "$norm r=" /tmp/landed_subjects.txt
```

Use `-x` (whole line) or anchor with the `r=` tail. A bare substring `grep`
matches a one-word subject like `temp` against half the tree's history.

Beware the near-miss: a subject that landed with only punctuation or a word
changed will miss here. That's why step 4 runs first — content is the primary
signal, this is the fallback.

### 7. Backout scan — do not skip this

The step that saves you from deleting live work. Collect every bug/issue
identifier across the branches you're about to delete, then scan upstream for
reverts naming them:

```bash
git log --oneline -40000 $UP --grep='^Backed out\|^Revert "' -i \
  | rg -i '<id1>|<id2>|<id3>'
```

For each hit, list that patch's full upstream history oldest-first and read the
**last** event:

```bash
git log --format='%h %ad %s' --date=short -20000 $UP --grep="<distinctive subject fragment>" | tac
```

Land → revert → land means it stuck; delete. Land → revert with nothing after
means the patch is **out of the tree** and the branch is the surviving copy —
keep it. A patch backed out and relanded several times is normal; only the tail
matters.

This scan is also why the content test can disagree with the subject test: a
backed-out patch's subject is in the log, but its content isn't in the tree.
When they disagree, content wins and the backout scan explains why.

### 8. Bug-level sweep over the survivors — the catch-all

Everything above compares a *commit* to upstream. That misses the case where
the work landed but the commit didn't: a patch reworked during review lands
under the same bug with a **different approach**, so its subject changed *and*
its implementation changed. The content test conflicts (different code), the
subject test misses (different wording), and the branch looks alive while its
bug is closed.

Real shape of it: a branch's `Send engine icons over the actor as bytes` had
landed a day earlier as `Send engine icons over the actor as data URLs` — same
bug, approach swapped in review. Nothing but the bug number connects them.

So for every branch that survived steps 4–7, ask what upstream did with its
bug:

```bash
for id in $(git log --format='%s' $UP..$b | grep -oE '[Bb]ug [0-9]+' | sort -u); do
  git log --format='%h %ad %s' --date=short -30000 $UP --grep="$id" | tac
done
```

Read the result as a prompt for judgment, never as an auto-delete rule — a bug
legitimately spans many commits, and yours may be the one piece still to go:

- **Nothing landed for the bug** — the branch is live. Done.
- **Something landed under a recognizably different subject** — likely the
  reworked version of your commit. Diff branch against the landed commit and
  check whether the branch adds anything the landed version lacks. If the
  remainder is only the abandoned approach, it's obsolete.
- **Other commits landed for the bug, yours unrelated to them** — a multi-part
  bug with work outstanding. Keep.

Surface these to the user with the evidence rather than deleting: this is the
one check that turns on intent, not on what git can prove.

### 9. Delete, and keep the receipts

```bash
git branch -D "$b"    # -d fails: a rewritten-on-landing commit is not an ancestor
```

Re-check what is checked out immediately before deleting, not just during step
2 — over a long session the user may have switched a worktree onto a branch you
had already judged obsolete. Git refuses the deletion, which is the safe
outcome; the mistake to avoid is "fixing" it by switching their worktree away.
Ask where they want it moved instead.

`-D` prints `Deleted branch <name> (was <sha>)`. Keep that output in your reply
— with the SHAs, every deletion is recoverable (the objects survive until gc,
and `git reflog` / `git branch <name> <sha>` restores one outright). Say so
when reporting, so an over-eager deletion is a one-line fix rather than lost
work.

## Reporting

Give the user, separately:

- what was deleted, with SHAs;
- what was kept and the specific unlanded commit that keeps it alive — this is
  what lets them spot a superseded commit you couldn't judge;
- anything indeterminate, named, with the reason (typically a reworded or
  partially-superseded commit) — don't silently fold these into either pile;
- branches you couldn't touch (checked out in a worktree).

Near-miss branches — everything landed except one commit on top — are worth
flagging explicitly. They're the most common source of a follow-up "that one's
superseded too, drop it".
