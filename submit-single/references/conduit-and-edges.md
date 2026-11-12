# Reading the stack graph, and the bundled Conduit helper

How to read the edges before and after wiring them, and what the bundled `phab.py` does on
your behalf. `submit-single` and `submit-stack` §3 both point here; §3 owns the *decision*
about which edges should exist.

## The single-commit corner: `--single` threads no edge

**When the revision genuinely does belong mid-stack, `--single` will not thread
it in.** It creates the revision with the right diff base but sets **neither**
stack-dependency edge: not the parent (new → the revision below it) nor the child
(the revision above it → new). So a freshly-inserted commit hangs disconnected —
its own graph shows nothing below it (bottom "cut off"), and the commit that
should now sit on top of it still points at the old parent. Wire **both** edges
by hand via Conduit `differential.revision.edit` (`parents.add` the below-revision
on the new one; on the above-revision `parents.remove` the old parent and
`parents.add` the new one), then re-read the graph to confirm the full chain —
below → new → above. (This is the single-commit corner of the stack-edge
re-wiring the submit-stack skill covers; `--single` doesn't do it for you.)

## Read the edges before and after

**Read the edges from `stackGraph` — before wiring anything and again after.**
`differential.revision.search` returns the dependency graph in its `stackGraph`
field (a map of revision PHID → list of parent PHIDs); `edge.search` with type
`revision.parent` is the alternative. There is **no** `parentRevisionIDs` field:
reading one returns `None` whether or not an edge exists, so a guessed field name
reads as "not threaded" and invites a redundant `parents.add`. Check the actual
field before concluding the tool left an edge unset.

## Use the bundled helper rather than hand-rolled curl

`python3 ~/.claude/skills/submit-single/scripts/phab.py` wraps the Conduit calls
this skill needs — `show`, `stack` (edges resolved to D-numbers), `link`/`unlink`,
`comment`, `wip`, `request-review`, `abandon`, `reviewers`, `title`, `summary`.
Every write is a dry run printing the exact method + transaction payload until you
pass `--yes`, and it re-reads the object afterward (`--help` for details).

Mozilla's Fastly edge throttles Conduit by TLS fingerprint, not by API token: plain `urllib` (which sends no ALPN), curl and `moz-phab`'s urllib3 each count against a separate bucket, and every `urllib` script on the host (this one, `bot-watch.py`, `phab-comments`) shares the first. On the first 429 the script prints `got HTTP 429; using curl from here on` and sends every later call in that process through curl. It retries a 502/503/504 with 5s/15s/45s backoff for reads only: a write (`differential.revision.edit`, `createcomment`, `createinline`) exits instead, since a 5xx can arrive after the write landed, so re-read the revision before retrying by hand. A `rate limited, try again later` exit means curl was throttled too; wait rather than hand-rolling curl round trips. The `urllib` throttle never blocks `moz-phab submit`, which was measured getting 200 throughout one.

## Whether an edge should exist at all

**First decide whether the revision should depend on anything at all — local
stacking is not review dependency.** Commits frequently share a branch only
because that is where you happened to be working. A revision depends on the one
below it when its diff genuinely needs that change (it would not apply, or it
builds on it) — *not* merely because the commit sits on top of another. Two
unrelated patches on one branch are **independent revisions**, and inventing an
edge between them gates one behind a review it has nothing to do with. Default to
independent; when the two are plausibly related, ask rather than infer. In
particular, a sensible-looking diff *base* proves nothing: moz-phab bases the diff
on the parent commit whether or not the changes are related.

## Local stacking is not review dependency

Commits frequently share a branch only because that is where you happened to be working,
and a sensible-looking diff *base* proves nothing: moz-phab bases the diff on the parent
commit whether or not the changes are related. Default to independent revisions and ask
when the two are plausibly related; inventing an edge gates one patch behind a review it has
nothing to do with. When the revision genuinely does belong mid-stack, `--single` sets
**neither** stack edge, so it hangs disconnected until both are wired by hand (above).

## `start_rev` is exclusive

`moz-phab submit <start> <end>` submits `<start>..<end>` in git's sense, so `<start>` itself
is *not* submitted. Naming the first commit you want drops it silently; everything else in
the range is correct, so only the count betrays it. Pass the commit *below* the first one you
want, and read the preview's list rather than its exit status.
