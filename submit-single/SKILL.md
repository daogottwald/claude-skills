---
name: submit-single
description: Submit or update a single patch to Phabricator via moz-phab safely — classify it as a real review request or a WIP first, then preview, submit just your commit with `--single`, note the update, verify. "For my review" or "for my eyes" means the author's own review, where `lrv` is the default. Use when pushing or re-pushing one revision (not a stack).
---

# Submitting a single patch with moz-phab

Pushing a patch to review is outward-facing: it notifies reviewers and mutates
shared state. Preview, push narrowly, note the update, verify.

## Decide first: review request or WIP?

Classify the push from the **user's words** before anything else, because it changes the
whole recipe and the default path pings reviewers:

- **Real review request** (the default). Proceed through the gates below.
- **WIP**: the author wants to look first, or someone wants to peek before the patch is
  ready. Triggers: "for my review", "for my eyes", "so I can look first", "as a WIP", "don't
  ping reviewers yet", "so X can have a look". A local review via `lrv` (the `lrv` skill) is
  the default for the author's own look; a WIP *push* is for when someone needs the
  Phabricator-rendered view or a shareable server checkpoint. Follow `references/wip.md`
  rather than the recipe below.

"For my review" means the *author's own* review; it is **not** a request-review signal.
Treat an explicit "for my review"/"WIP" phrase as dispositive, and never override it from
whether the commit already carries `Bug NNNNNN` / `r?`: real stacked patches carry both and
are still pushed as WIP for a first look. When the phrasing is ambiguous ("submit this" with
no audience cue), ask which one before pushing; the ping can't be recalled.

## Review gate: show it in `lrv` first — a submit request is not a review waiver

Before the real push, the exact diff you're about to submit must have already
been shown to the user in `lrv` since its last change. If it hasn't (you just
wrote or amended it, folded a fix in, or are re-pushing after edits), open `lrv`
first (per the "open `lrv` automatically when a patch is ready" rule) and fold in
any comments, **even when the user's instruction was "submit" / "resubmit" /
"fold it and resubmit."** An explicit submit instruction says *where the change
ends up*, not *skip my review*; it is not a waiver. The only waiver is the user
explicitly declining review ("no need to review", "just push it") or a WIP for
their own eyes. This gate exists because the "open `lrv` automatically" rule
reliably loses to a competing imperative like "resubmit", so it's bolted to the
push action here, where the temptation to skip it happens.

## Message gate: re-check the message, especially after a fold-in

Run the checker over what you're about to submit; it is mechanical, so it
catches what re-reading by hand doesn't:

```
python3 ~/.claude/skills/submit-single/scripts/check-commit-message.py <rev|range>
```

`ERROR` findings (paragraph and word ceilings, bullets, a banned phrase) must be
fixed before pushing; `check` findings are prompts to re-read a sentence and
judge. The failure mode it exists for is **accretion**: each time you fold a fix
into a commit, the temptation is to append a paragraph describing it, and nobody
re-reads the whole body afterwards. Amend the message as a whole instead, and
narrow it to what this commit now is.

**Amending an already-submitted commit means editing its message, never replacing it.** Its
message carries a `Differential Revision` trailer, and `git commit --amend -F -` (or `-m`)
drops it; the next submit then reads the commit as new and creates a *duplicate* revision,
orphaning the original mid-stack. Amend in an editor, or re-append the trailer as a literal
line. The checker will not catch this. A **message-only** change needs no diff re-push:
`references/reviewers-and-status.md` has the title/summary sync.

## Approval gate

Never push without explicit user approval, and make sure the approval matches
what the tool will actually *do*, since a submit can un-WIP a revision,
re-attribute its bug, or touch more than you intended (see Preview).

## Reply gate

**Post inline replies before the submit, not after.** Before any push to a revision with
reviewer inlines, settle which of them get a reply and post those first, through the
`phab-comments` skill. A reply shares its thread's diff, and Phabricator ports both together
when the new diff lands. Posted after the push, the reply has to name the line the UI moved
the thread to. When the code under the thread changed, no model predicts that line, so the
post needs the user to read it off the web UI (D329859 and D335930 waited on that). The
update note goes with the submit as before.

## Status gate

Read the status before you submit:

```
python3 ~/.claude/skills/submit-single/scripts/phab.py show D<id>
```

`Needs Review` or `Needs Revision`: carry on. `Accepted`: a re-submit does not clear it, so
a substantive change needs the user's decision on re-requesting review; `Draft` moments
after a first push is transient. Both in `references/accepted-revision.md`.

## Reviewer syntax in the commit message

`moz-phab` reads reviewers from the `r?`/`r=` line: `!` suffix makes a reviewer blocking
(`r?alice!`), bare is non-blocking, `#` prefixes a group (`r?#urlbar-reviewers`), commas
combine. Confirm blocking-vs-not from the user's wording rather than defaulting a named
blocking reviewer to bare `r?`. Full syntax, and why a shorter `r?` line never drops
accumulated reviewers: `references/reviewers-and-status.md`.

## Preview before pushing

`moz-phab` has no dry-run, but declining its confirmation prints the full plan:

```
printf 'no\n' | moz-phab submit --single <commit>
```

Read it for surprises before the real push: `(New)` vs `(D…)` (a new revision
vs an in-place update), a `!! "Changes Planned" will change to "Request Review"`
WIP flip, a `!! Bug ID … will change` re-attribution.

**Preview every re-push, not only the first submit.** That `(New)` line is the only thing
that surfaces a commit whose `Differential Revision` trailer went missing, and a re-push is
when that has happened.

## Submit just your commit

Plain `moz-phab submit` walks the whole stack down to the landed autoland base
and errors trying to re-submit an already-closed base ("… has already been
closed"). Submit only your commit:

```
moz-phab submit --single <commit> -m "<update note>" --yes
```

`--single` targets a specific commit by hash (works mid-stack) and sets no stack edges; an
existing revision keeps its edges. The `-m` is mandatory once a reviewer has seen the diff
(next section); a first push or a WIP takes `--yes` alone. A contiguous range, a new
mid-stack revision and its edge wiring: `references/conduit-and-edges.md`.

## Tell the reviewer what changed — mandatory once a reviewer has seen the diff

When a submit *updates* a revision that is or has been under review, the reviewer
**must** hear what changed between diffs, either as an update note (`-m "<summary>"`,
a per-update note attached to the revision, **independent of the commit message**)
or as replies to the comments the update addresses, so they don't meet a bare "updated."
This is the step that reliably gets skipped, because `--yes` suppresses the interactive
update-message prompt: `--single --yes` with no `-m` runs clean and silently lands a
note-less update, so the omission is invisible until a reviewer is staring at an unexplained
new diff. Treat "update a revision" and "pass `-m` or post the replies" as one action. If you
catch yourself having pushed without one, repair it at once: `references/accepted-revision.md`.

**Usually it is a note *or* replies, not both.** Addressing review comments in a
straightforward way ("done", the suggested rename applied, the nit fixed) gets one update
note and no per-comment replies. Wanting to clarify something with the reviewer (why you
took a different route, a question about their suggestion, a case their comment missed)
gets a reply on that comment, posted through the `phab-comments` skill. A mix is fine when
the update does both kinds of work, but then the note covers only what the replies don't:
a note restating a reply, or a reply restating the note, is noise the reviewer reads twice.

**Show the user the note (and any replies) and wait before pushing.** It is one-shot: it
lands in the revision's timeline attached to that diff, and the only repair is another
comment beside it. Print the exact `-m` text and get the user's nod on the wording, folded
into the approval gate so it is one round trip: the note and what the preview says the
submit will do, answered together.

**An update that does what the reviewer asked, in the way they would expect, gets an
elliptic note: a two- or three-word phrase naming the topic, no sentence.** "handle
submenus", "rename as suggested", "add the missing await". The reviewer wrote the request
and will read the diff against it, so a sentence restating what they asked for is noise.
Offer that form first and marked recommended. A full sentence is for a delta they did not
ask for or would not expect from their comments (a different route than the one they
suggested, a fix that reached into another file, a behavior change beyond the nit), and
even then the shortest one that carries the delta: cut the clause explaining *why* or what
it makes possible, since the reviewer has the diff and the commit message for that. If the
note seems to need a paragraph, the change may deserve its own commit.

## Open the revision after pushing

A push isn't done until you can see it. After a successful submit, open the
revision in the browser automatically; don't just print the URL and stop:

```
python3 -c 'import sys, webbrowser; webbrowser.open(sys.argv[1])' <revision-url>
```

The submit prints each `-> https://…/D<id>`; open it. (A sandbox may log a
namespace/`EPERM` warning while still launching the browser; ignore it.)

## Update the plan status after pushing

Skip this section when the work isn't tracked in a plan or roadmap doc. Otherwise a submit changes review reality, so reconcile the doc in the same turn: set the
just-submitted work's status to **IN REVIEW**, and add the tracker link if the bug was newly
filed. Where the overview shows that work (a progress-table row, a findings-list entry),
mirror the change into it in the same turn (through `plan-to-overview`'s targeted-edit mode where that skill is installed); with no overview file, the overview is the tracking bug's description. A WIP push is not this trigger; the status stays IN PROGRESS until you request
review. Where installed, `plan-status-sync` does a sweep across a plan and its sub-plans.

## Verify after

The revision updated as intended (diff and/or message), and the local branch is
intact: a mid-stack `--single` shouldn't rewrite it; confirm the tip and that
the working tree is clean.

A submit stopped partway (a rejected or killed call, a timeout) can already have
created the revision for a new commit without writing its `Differential
Revision` trailer back, so the retry creates a second one. After any interrupted
submit, list the bug's open revisions before retrying, and reuse or abandon what
the first attempt left (`scripts/phab.py abandon`, after `unlink` from its
parent).

## Watch for the review bots

Two bots report on every non-WIP diff: `reviewbot` (Code Review Bot) runs lint and static analysis as a Harbormaster build, and `reviewhelper-bot` (Review Helper) leaves review comments. Start the watcher as a background Bash task (`run_in_background: true`) right after the push, without being asked:

```
scripts/bot-watch.py D<rev> [D<rev>...]
```

It waits 10 minutes before its first poll (`--first-delay 0` when starting it well after the push), then polls every 5 minutes, re-reading only the revisions still pending; keep that interval, since every `urllib` Conduit script on the host shares one throttle bucket. It counts only comments on the current diff, and exits 0 once every revision has settled (build finished and Review Helper commented, a reviewbot comment, or a failed build), 3 after an hour. The Review Helper reviews a revision once, so on a re-push of a revision it already commented on, a finished build is enough; expect its comments on new revisions only. Its exit re-invokes you: read the new comments with the `phab-comments` skill (`--include-bots`) and bring them to the user with a proposed fix per finding. A `build=failed` with no bot comment usually means the bot could not apply the diff, e.g. a stack pushed before its parent edge existed; say so and offer a re-push rather than reading it as a lint failure. A reviewbot comment saying an analysis task "failed, but we could not detect any defect" is a real failure the bot could not parse, not noise: open the task's log and treat it as a blocker before landing. For `source-test-doc-upload` it is the docs build, which autoland runs as `source-test-doc-generate` and backs out on (D340510 shipped two `myst.xref_missing` links past two such comments and was backed out); reproduce it with `./mach doc --no-serve --no-open` at that commit. Skip the watcher for a WIP push, which the bots ignore.
