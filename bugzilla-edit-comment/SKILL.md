---
name: bugzilla-edit-comment
description: Edit an existing bugzilla.mozilla.org comment (including comment 0, the description) instead of posting a follow-up correction. The obvious REST paths 404; editing is supported.
---

# Editing a Bugzilla comment

BMO comments **are** editable through the API. The obvious REST paths 404, which reads like the
feature does not exist; it does, under a different resource. Do not tell the user it is impossible.

## The endpoint

```
PUT /rest/editcomments/comment/<comment_id>
{"new_comment": "<the full replacement text>"}
```

Two things that waste a round trip if guessed:

- The resource is `editcomments`, not `bug/comment` or `bug/<id>/comment/<cid>`. Both of those
  return `404 code 32614`.
- The field is `new_comment`, not `new_text`. Sending the wrong name returns
  `400 code 50 "requires a new_comment argument"` and writes nothing, so a probe is safe.

The body replaces the comment **whole**. There is no patch form: fetch the current text, edit it,
send all of it back.

## Auth

`X-BUGZILLA-API-KEY` header. The key lives in `~/.config/python-bugzilla/bugzillarc` under an
`api_key` entry (the section name varies, so scan the sections rather than hardcoding one).

## Comment id is not comment number

`GET /rest/bug/<bug_id>/comment` returns each comment with both a `count` (the 0-based number in
the UI, where comment 0 is the description) and an `id` (the global comment id). The edit endpoint
takes the **`id`**. Taking `count` silently edits some unrelated bug's comment if that id exists,
so map it explicitly.

## Use the bundled script

`scripts/bz-comment.py` does all of the above and keeps the comment body out of your context:

```
bz-comment.py get  <bug> [--count N] [-o FILE]   # fetch the text to edit
bz-comment.py diff <bug> <file> [--count N]      # what would change, no write
bz-comment.py put  <bug> <file> [--count N]      # write, then verify
```

`<bug>` takes a number or an alias; `--count` defaults to 0, the description. `put` reads the
stored text back, compares it to what it sent, and prints one line —
`bug 2071608 comment 0 (id 18336825): VERIFIED, 3 lines changed, 59 lines stored`. It exits
non-zero on a mismatch, and refuses to write at all when the file already matches the comment,
so a re-run costs nothing and adds no edit-history entry.

**Don't hand-roll the call to save a step, and don't print the response.** The PUT echoes the
stored `text` *and* its rendered `html` — every URL a second time inside its `href`, every table
as markup — so printing it costs thousands of tokens on a description-sized comment, against one
line for the script's verdict. Reading the text back to judge it by eye is both the expensive
option and the one that cannot support a claim that nothing else changed; only a diff does that,
which is what `put` computes.

## Edit or follow up

Edit when the comment should never have said what it says: a wrong number, a claim a later
measurement refutes, a bad link, a typo. The reader gets one coherent comment instead of
reconciling two versions, and BMO marks it edited and keeps the revision history, so nothing is
concealed.

Follow up instead when:

- Someone has already replied to or quoted the comment. Editing out from under a reply makes the
  thread incoherent.
- **A needinfo is pending on someone.** This fires earlier than a reply does: you have invited
  someone to read the record, so it must stop moving under them even though they have not answered
  yet. Once a needinfo is set, every correction goes in a new comment, however small.
- The change is genuinely new information rather than a correction — post it as its own comment.
- The comment is the one a sheriff, release manager or automated process acted on. Editing it
  rewrites the stated reason for a decision already taken.

When you edit out a substantive claim, drop it cleanly rather than leaving "(edited: this was
wrong)" prose in the body. The edit marker and history already say that, and the body should read
as if the mistake was never in it.

## Failure modes

- `404 code 32614` — wrong resource path. Use `editcomments`.
- `400 code 50` — wrong field name. Use `new_comment`.
- A permissions error rather than a 404 means the account cannot edit *that* comment. BMO limits
  editing by account and by comment age; post a follow-up instead.
- A comment that is already the product of an edit edits the same way; the history accumulates.

## Related

Reading and posting comments, and changing bug fields, go through the ordinary
`PUT /rest/bug/<id>` with a `comment` object and field keys — a different resource from this one.
A single `PUT /rest/bug/<id>` can carry both a new comment and field changes (`regressed_by`,
`depends_on`, …) in one transaction.
