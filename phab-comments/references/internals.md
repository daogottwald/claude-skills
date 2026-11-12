# How the scripts read a review, and what the API cannot do

How a suggested edit turns into the diff `phab_comments.py` prints, the two API routes that
look like they would help and don't, and what to read before extending either script or
acting on a `warning:` line it printed.

## How it works

1. Conduit `differential.revision.search` for the title, `transaction.search`
   (paginated) for general comments and inlines, `user.search` + `phid.query` to
   resolve author PHIDs to names.
2. An inline transaction's `fields` carry `path`, `line`, `length`, `isNewFile`,
   `diff`, `isDone`, `replyToCommentPHID`, `hasSuggestion` and `suggestionText`.
   `suggestionText` is the reviewer's replacement for exactly the anchored range,
   `line` through `line + length - 1` on the new file (the old file when `isNewFile`
   is false): Phabricator pre-fills the Suggest Edit box with those lines, so the text
   is the whole range after editing, with or without a trailing newline. A
   suggestion-only inline has `comments[0].content.raw == ""` beside it.
3. For every diff that carries a suggestion, one `differential.querydiffs` call
   returns each changed file's hunks with `oldOffset`/`newOffset` and a `corpus` of
   ` `/`-`/`+`-prefixed lines. The script numbers the lines on the anchored side,
   takes the anchored range plus two lines of context either side, and diffs it
   against the suggestion with `difflib`, which is how the web UI's
   `inline-suggestion-view` table comes out.

`--summary` stops after step 1. Otherwise step 3 is scoped to the inlines that survive
`--inline` / `--skip-done` filtering, so a run narrowed to one comment fetches only
that comment's diff, and a revision whose inlines are all filtered out fetches none.

Notes if you need to debug or extend this:

- `differential.getrawdiff` is not a substitute for `querydiffs`: it trims context to
  a few lines around each change, while `querydiffs` keeps the context moz-phab
  uploaded, which is the whole file unless it ran with `--less-context`. An anchored
  line outside the stored hunks is reported as `the original lines are not in the
  diff` and the suggestion prints as `+` lines only.
- Comments stay anchored to the diff they were left on, so a revision with newer
  diffs uploaded still resolves: step 3 uses the diff id Conduit reports per comment,
  not the newest diff.
- The suggestion fields come from mozilla-conduit/phabricator#102 (Bug 2024394), in
  production since 2026-09-17. If `transaction.search` stops returning
  `hasSuggestion` on inlines, the script prints a warning and shows text-less inlines
  as `(no comment text)`; the warning means the server changed, not that the review
  is empty.

## Dead end: do not try to bulk-read comments

`differential.getrevisioncomments` (legacy) looks like the answer, since it takes an
`ids` array and returns every listed revision's comments in **one** request. It
cannot answer this question:

- Its `inlines` parameter is ignored on Mozilla's install. No record carries an
  `inlines` key; the only fields returned are `revisionID`, `action`, `authorPHID`,
  `dateCreated`, `content`.
- It emits a record with `action: "none"` and `content: null` for inline comments
  *and* for reviewer-status changes alike, with nothing to tell them apart. A
  suggestion-only inline is therefore indistinguishable from an accept.
- Filtering those records by author does not rescue it: revisions with no comments at
  all still carry null-content records from reviewers, so it reports false positives
  on most of a stack.

`differential.revision.search` has no comment-count field and no comment attachment
either. `transaction.search` takes a single `objectIdentifier`, so one request per
revision is the floor -- which is what the script spends.

## Dead end: you cannot mark an inline done

Once a comment is addressed, ticking its **Done** box is web-UI only. Conduit exposes
no write for it, so a sweep can report what is outstanding but never clear it. The
server enumerates what it will accept -- send `differential.revision.edit` a
deliberately invalid transaction type and the error lists every valid one:

```
update, title, author, summary, testPlan, reviewers.*, repositoryPHID,
tasks.*, parents.*, children.*, plan-changes, request-review, close, reopen,
abandon, accept, reclaim, reject, commandeer, resign, draft, view, edit,
projects.*, subscribers.*, phabricator:auditors, bugzilla.bug-id,
uplift.request, comment
```

Nothing for inline state, and no `inline.*` namespace anywhere in the API.
`differential.createinline` creates an inline; it cannot mark one addressed.
Validation rejects an unknown type atomically, so that probe applies nothing -- it is
a safe way to re-check if you suspect the surface has grown.

The **read** side does work and is worth using: `transaction.search` carries
`isDone`, which is why the script reports done state.
