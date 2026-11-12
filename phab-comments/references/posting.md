# Replying to and posting Phabricator comments

Everything posted here is published at once and for good. `phab_post_inline.py` lives in `~/.claude/skills/phab-comments/scripts/`.

## Replying to a comment

**Read the whole thread before answering.** Run the default (done inlines included) over the revision and check whether the author already answered the point, in an inline reply or a general comment; a duplicate answer is published for good and reads as not having read the review.

**A reply to an inline comment is itself an inline, anchored on the same line.**
Post it with `phab_post_inline.py --reply-to <inline id>` (see *Posting an inline comment* below), which
anchors it where the current diff shows the reviewer's inline, and with no
`-m`; the anchor supplies the context, so no quote is needed. Give the path
and line by hand only for a fresh inline of your own. A header of `path:first-last` is a range: pass `first`
as the line and `--length` = `last - first` (or pass `first-last` as the line
and the script does that), or the reply renders under the
first line alone and reads as anchored to the wrong code (a reply to a
`menupopup.js:53-64` inline posted at bare `53` did exactly that, and cannot
be moved). Only a single-line header takes a bare line. A quoted general comment reads as a reply only to
whoever scrolls the timeline and leaves the inline looking unanswered in the
file view. Reply to several inlines with one invocation each; every invocation
publishes all your pending drafts on the revision.

**The line number belongs to the diff Phabricator holds, and a published
inline can be neither moved nor deleted, by you or by the author.** When the
patch has changed locally since the reviewer's inline, reply first, on the
diff the reviewer's inline sits on, then resubmit: the reply shares the
thread's diff, and Phabricator moves both together. Translating the anchor
to the local commit and posting before the resubmit lands the reply on
unrelated lines of the old diff for good; three replies on D327251 sit there.
The script fetches the revision's current diff and refuses to post until
`--expect` names a snippet of the anchored line (`--force` overrides), which
turns that mistake into an error before anything is published.

**An inline the reviewer left on an older diff is stored in that diff's line
numbers, and the UI shows it ported onto the current diff, to a line no model
has predicted reliably.** The reader prints the diff id on each inline's
second line; when it is older than the revision's current diff, neither the
printed line nor a line derived from it is where the thread appears:

- The stored number put a reply on D327677 on the comment line above the
  `queueMicrotask` call the thread hangs on, because the comment had been
  reworded in between.
- A "first line after the change" rule put a reply on D327251 two lines below
  smaug's thread (a three-line replacement kept the thread on its own line,
  `:239`, and the reply sits on `:241` for good).
- An offset port put two replies on D328788 away from robwu's threads, although
  the threads' own lines were unchanged and `--expect` matched the same code on
  the new diff.

So `--reply-to <inline id>` on an older-diff inline stops, prints the model's
guess, and requires `--at <line>`, the line the web UI shows the thread on.
Ask the user for it, or open the revision yourself; never pass the guess back
as `--at`. `--expect` still checks the line, and cannot catch a wrong one,
since the same code sits there either way.

A reply to a **general** comment is a general comment. Quote what you answer in
Remarkup's reply-block form, which needs no id:

    >>! @marco wrote:
    > What if we didn't have any limitation on extensions and just synced everything?

    <reply, one paragraph per line so Remarkup does the wrapping>

The web UI's Quote action writes an anchored variant (`>>! In D325789#11315593,
@marco wrote:`), and that number is not reconstructable from Conduit: on one
measured comment `transaction.search` gave transaction 11315595 and comment id
1736843, while the UI cited 11315593 -- the revision's `accept`. The anchorless
form renders the same block and loses only the backlink, so prefer it.

`remarkup.process` renders a block without posting it (`{"context":
"differential", "contents": [body]}`) -- how to check one before it goes out.

Post with `phab.py comment D<id> -m "<body>" --yes`; the script lives in
`submit-single`.

**Offer the follow-up when the suggestion is sound but out of scope** -- "Let's
handle that in a followup if wanted?" leaves the reviewer a decision rather than
an argument to accept. When you decline because it is unworkable or misguided,
give the reason and stop there; an offered follow-up promises work nobody should
do, and the reviewer has to guess which of the two you meant.

## Posting an inline comment

Anchor a point on the line it is about rather than describing the location in a
general comment:

```bash
python3 ~/.claude/skills/phab-comments/scripts/phab_post_inline.py \
  D321758 toolkit/content/widgets/moz-page-nav/moz-page-nav.nova.tokens.json 30 \
  --expect '"$value"' < body.md
```

The path is the one the diff prints and the line is the one the **new** file has
on the revision's current diff (`--old` anchors the left-hand side, `--length N`
spans more lines). The script prints that line's text and requires `--expect
'<snippet>'` to match it, or `--force`; a line outside every hunk also needs
`--force`, since a reply to a reviewer sits on a changed line. `-m` adds a
general comment to the same submission; without it only the inline goes out.

**`differential.createinline` creates a draft, and nothing you would reach for
next publishes it.** The draft is invisible to `transaction.search` and to every
other reader, and `differential.revision.edit` -- what `phab.py comment` sends --
leaves it sitting there, so the general comment posts and reads as the whole
review. `differential.createcomment` with `attach_inlines` is what publishes,
and an empty `message` publishes the drafts without adding a general comment.
The script does both, then re-reads the revision to confirm; the error it prints
means the draft is still a draft, not that the text is lost.

**To finish a review with Request Changes, pass `--request-changes` on the last inline.** After the inline is published, the script sends a `reject` transaction through `differential.revision.edit` and prints the status it reads back (`needs-revision`). For a review with several inlines, post the others first, so that every inline is out before the status changes.

**`attach_inlines` publishes every pending draft of yours on that revision**,
including one typed into the web UI and never submitted. Check the revision in
the browser for strays before posting to one you have been reading there.
