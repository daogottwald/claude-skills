# Updating a revision that is already Accepted

Mozilla's Phabricator keeps `Accepted` across an update: a re-submit does not clear it. So a
substantive change pushed onto an accepted revision lands code nobody signed off under a
green status, unless someone acts.

## Before the push

Read the status (`python3 ~/.claude/skills/submit-single/scripts/phab.py show D<id>`). If it
is `Accepted` and the change is more than a rebase, a comment tweak or a message reword, you
are adding code to something a reviewer already accepted: **ask the user whether to
re-request review** and show them what the addition is. It is their call.

## After the push

Read the status back rather than inferring it. `Accepted` usually survives, but a diff that
grows into a *new* area can clear it by itself when Herald adds a blocking reviewer, and
requesting review on a revision Herald already moved lands a redundant timeline entry.

If it is still `Accepted`, request review explicitly and post a comment naming what is new
since the accept, so the reviewer looks at the addition instead of re-reading the whole
diff:

```
python3 ~/.claude/skills/submit-single/scripts/phab.py request-review D<id> --yes
python3 ~/.claude/skills/submit-single/scripts/phab.py comment D<id> -m "<what is new since the accept>" --yes
```

Both are dry runs printing the transaction payload until you pass `--yes`.

## `Draft` right after a first push is transient

Phabricator creates a new revision as a draft and publishes it once the diff finishes
processing, so a status read moments after submitting reports a state already on its way
out. Never run `request-review` on it: the revision was going to `Needs Review` by itself,
and the transaction only lands a redundant entry on a reviewer's feed. Read the status later
if you want to confirm.

## Repairing an update that went out without a note

If you updated a reviewed revision with `--yes` and no `-m`, post the delta summary as a
comment right away (`phab.py comment D<id> -m "<delta>" --yes`, a Conduit
`differential.revision.edit` `comment` transaction). Don't leave the update unexplained.
