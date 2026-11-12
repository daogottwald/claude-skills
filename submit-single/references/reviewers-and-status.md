# Reviewer syntax, and reading the status back

The full `r?` syntax, why the revision's reviewer list is routinely longer than the message
says, and what the status after a submit does and does not follow from. `submit-single` keeps
the one-line versions of these inline.

`moz-phab` reads reviewers from the `r?`/`r=` line of the commit message, and the
marker on each name sets whether that review is *blocking* — get it right before
preview, because a wrong marker submits with the wrong gating:

- **Blocking reviewer** — suffix the name with `!`: `r?alice!`. A blocking
  reviewer must approve before the revision can land. Use `!` whenever the user
  names someone as *the* or a *blocking* reviewer.
- **Non-blocking** — bare `r?alice` (no `!`): a requested but non-blocking
  review.
- **Group reviewer** — prefix with `#`: `r?#urlbar-reviewers` (blocking group:
  `r?#urlbar-reviewers!`).
- Combine with commas: `r?alice!,#urlbar-reviewers`.

Confirm blocking-vs-not from the user's wording ("blocking reviewer" → `!`); don't
default a named blocking reviewer to bare `r?`.

**A shorter `r?` line does not drop reviewers the revision already carries.** A
revision accumulates reviewers beyond its commit message — Herald adds a group
when the file set changes, and people add themselves in the UI — so the remote
list is routinely longer than the `r?` line. Re-submitting leaves the extras in
place: a revision holding eight reviewers came back with all eight after a submit
whose message said only `r?dave!`. So don't pad the `r?` line to match the
remote list before pushing, and don't read the short line in the preview as a
pending clobber. (Conduit's `reviewers.set` *does* clobber, which is what makes
this worth stating — that's the API, not `moz-phab submit`.)


**After submitting, read the status back.** Mozilla's Phabricator keeps
`Accepted` across an update whose diff stays within the same area — two
revisions re-submitted with real new test code both came back still `Accepted`,
which is why the request below has to be explicit. But a diff that grows into a
*new* area can clear the accept by itself: Herald adds that area's reviewer group
when the file set changes, and if it adds a **blocking** reviewer the status drops
to `Needs Review` on its own, with the earlier accepts left intact. Folding a
`browser/themes/shared/urlbar.css` hunk into an accepted urlbar revision pulled
in `#desktop-theme-reviewers` plus a blocking individual and moved the status with
no request from us. So the status after a submit doesn't follow from the diff you
pushed — read it rather than inferring it. The read leaves nothing on the
revision; requesting review on one Herald already moved lands a redundant entry
in its timeline.

## When a summary sync returns `HTTP 406`

A WAF is matching the body text, not Conduit rejecting it: bisect the string by POSTing it to
`user.whoami` as an ignored `probe=` param (no transactions land while you narrow), then
either reword the trigger or re-submit, whose path isn't filtered.

## A message-only change: sync, don't re-push

A change to the commit message alone needs no diff re-push, which would re-upload an
identical diff as reviewer noise. Sync the revision instead:

```
python3 ~/.claude/skills/submit-single/scripts/phab.py title D<id> -m "<subject>"
python3 ~/.claude/skills/submit-single/scripts/phab.py summary D<id> -m "<body>"
```

and amend the local commit to match, or the next submit reverts it. An `HTTP 406` here is a
WAF matching the body text rather than Conduit rejecting it; bisect the body as above.
