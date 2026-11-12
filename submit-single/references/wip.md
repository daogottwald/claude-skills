# Pushing a WIP: for your own look, or for someone to peek at

A **local** review is the default for "just let me look": `lrv` (the `lrv` skill) shows the
diff or series in a browser with no server footprint and feeds comments back. Reach for a
WIP *push* when someone needs the Phabricator-rendered view or a shareable server
checkpoint: the author wanting a first look in the tool, or a reviewer or colleague who
asked to peek before the patch is ready. Either way the revision requests nothing of anyone:
share its URL by hand when someone is waiting for it. `submit-single` and `submit-stack`
both route here; a bare §N refers to `submit-stack`.

## Keep it detached

Keep a WIP **detached** so it touches neither the bug nor any reviewer:

- **`--no-bug`**, and strip the `Bug NNNNNN` from the commit message, so the revision isn't
  attached to the bug. `--no-bug` is *required* here: without a bug moz-phab otherwise
  refuses with "Unable to submit commits".
- **Omit reviewers** (no `r?` in the message, none passed) so the tool creates it as WIP /
  "Changes Planned", visible at its URL, no notifications. (`--wip` also forces WIP, but it's
  *omitting reviewers* that prevents the ping.)
- **No update note.** A note is for a reviewer reading a new diff against one they already
  reviewed; a WIP has no such reader, so `-m` on a `--wip` push or on the later flip to
  Request Review lands a timeline entry about a delta nobody saw, in the place a reviewer
  arriving fresh starts reading. `--wip` on the command is the tell: `--yes` alone.
- An attached `--wip` push whose message names `r?` reviewers sets none on the revision
  (D340746, 2026-11-08), so nobody is pinged; resubmitting without `--wip` adds them.
- Preview (decline) and confirm `(New)`, no bug, and no reviewers before the real push.

This is about *why* you're pushing, not *how many* commits: it holds for a single patch as
much as a stack. Don't attach a WIP to its bug just because the commit happens to carry one.
A multi-commit WIP stack needs the extra steps in the last section.

## Going official afterwards

Rather than abandoning the WIP revision, restore the bug and the `r?` reviewers in the
message and re-submit: the tool updates it in place (WIP → review requested) and attaches
the bug. Going official from a *stack* is richer (stamping trailers onto the real-stack
commits), see submit-stack §5.

## Fixing a WIP you already pushed attached

A WIP that went out attached to the bug, with reviewers or with a stack edge doesn't need
re-creating: clear them via Conduit `differential.revision.edit` (`bugzilla.bug-id` set to
`""`, `reviewers.remove`, and `parents.remove` the stack edge), then re-fetch the revision to
confirm all three actually cleared, since a Conduit edit can silently no-op (submit-stack §3).

## A stack adds two things, so it stays fully disconnected

A multi-commit WIP stack beside an official one needs two more things so it stays fully
**disconnected** from the official stack:

- **Strip every `Differential Revision:` trailer** from the commits, or the submit updates
  the official revisions instead of creating new ones. Watch for a trailer inherited via
  **cherry-pick**: a picked commit carries its origin's trailer.
- **Base the leaves on a disconnected commit** so the tool doesn't wire the bottom leaf's
  "Depends On" to a real revision: `git commit-tree <target-tree> -p <upstream> -m "review
  base, not for landing"` gives a trailer-less base with the right tree; rebase the leaves
  onto it (`rebase --onto <base> <old-base>`) and submit `<base>..<tip>` (the base is the
  exclusive start-rev, never submitted). If the natural base is itself a known revision, the
  tool links to it; the synthetic base avoids that.
- **Preview (decline) and confirm every commit shows `(New)`**, no bug, no reviewers, and no
  official revision is touched before submitting for real.
- **When the review's done, don't reflexively abandon the WIP revisions**; decide by what
  happens to the *content*:
  - *Reviewed idea rejected or rebuilt differently* → abandon them (and detach, §3).
  - *Reviewed content is kept and going official* → **reuse them, don't abandon** (cheaper,
    and the review history carries over): stamp each WIP revision's `Differential Revision`
    trailer onto the corresponding real-stack commit, restore the bug number and `r?`
    reviewers in those messages, and submit the official stack. **Stamp the trailer as a
    literal line** (`printf '\nDifferential Revision: <url>\n' >> $msg`, or append it in an
    editor): `git commit --trailer "Differential Revision=<url>"` **mangles a URL value**
    into `Differential Revision=<url>:` (wrong separator, trailing colon), which moz-phab
    won't match, so it silently creates `(New)` revisions instead of reusing them. When
    mapping several trailers at once, key them by **commit position** in the rebase todo
    (Nth pick → Nth revision), not by grepping the subject. Always confirm the preview shows
    `(D…)` not `(New)` before the real push. The tool updates the same revisions in place
    (WIP → review requested) and links them to the official base; a message-only change
    needs no diff re-push. Then re-wire the seam edges the tool leaves stale (§3). Only if
    the promotion *replaces* existing revisions (a reorg/resplit) do you then abandon +
    detach the superseded originals (§3); purely additive work supersedes nothing.
