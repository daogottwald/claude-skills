# Token setup, throttling, and what a run costs

Needed on a machine that has never run the script, and whenever a run reports a 429 or
exits 3.

## Setup

**A Conduit API token is required.** Read from `$PHABRICATOR_API_TOKEN`,
`$CONDUIT_TOKEN`, `$PHABRICATOR_TOKEN`, then `~/.arcrc` (which `moz-phab` already
writes, so on a machine set up for `moz-phab` there is nothing to do). Otherwise mint
one at <https://phabricator.services.mozilla.com/settings/panel/apitokens/>.
`phab_post_inline.py` imports `phab_comments.py` from the same directory for that
plumbing, so keep the two together.

**No web session is needed.** Every request is a Conduit call with the token, which
reads whatever revisions your account can, restricted ones included.

## Rate limiting

**Mozilla's Fastly edge throttles Conduit by TLS fingerprint, not by API token or
User-Agent.** The 429 arrives with `Via: 1.1 varnish` and an empty body, from the CDN
rather than Phabricator. Plain `urllib` sends no ALPN in its TLS handshake and counts
against one bucket; curl, `requests` and `moz-phab`'s urllib3 send ALPN and counted
against others. Measured on 2026-11-06, interleaved three seconds apart on one token:
plain `urllib` got 429 under any User-Agent, while curl (even with a Python
User-Agent), urllib3 and `urllib` with `ctx.set_alpn_protocols(["http/1.1"])` got
200. Every `urllib` script on the host shares the throttled bucket: both of this
skill's scripts, `submit-single`'s `phab.py` and `bot-watch.py`, and other sessions'
runs of them. The block lifted on its own after a long throttled stretch; its
threshold and duration are unmeasured.

Consequences:

- **A tripped throttle does not block `moz-phab submit`**, which uses urllib3.
- **On the first 429, the scripts switch to curl for the rest of the process**, printing
  `got HTTP 429; using curl from here on`. Retrying `urllib` into the block only feeds
  it. Don't enable ALPN in `urllib` to dodge the block: it would likely move these
  scripts into `moz-phab`'s bucket, where a sweep could block a submit.
- **A 502/503/504 is retried with 5s/15s/45s backoff for reads only.** A write
  (`differential.createinline`, `createcomment`, `revision.edit`) raises instead, since
  a 5xx can arrive after the write landed; check the revision's drafts before retrying.
- **If curl is throttled too, the run stops (exit 3)** instead of issuing requests that
  would only extend the block. Wait several minutes and raise `--delay` for the next run.

A rate limit is reported as such; the "Phabricator may have changed" warning means
something else entirely (see `references/internals.md`).

`conduit.ping` is useless as a probe, since it needs no token. Test with an
authenticated call through `urllib`, the client that gets throttled:

```bash
python3 -c "import json,os,urllib.parse,urllib.request as u;h=json.load(open(os.path.expanduser('~/.arcrc')))['hosts'];k=list(h)[0];d=urllib.parse.urlencode({'api.token':h[k]['token']}).encode();print(u.urlopen(k+'user.whoami',d).status)"
```

## What a run costs

One `differential.revision.search` for up to 100 revisions (titles, authors, status,
stack graph), one more for `--stack`, then **one `transaction.search` per revision**,
then one or two calls to resolve author PHIDs to names, each PHID looked up once per
run so a stack's shared authors cost nothing after the first revision. A typical
revision has 20-50 transactions, well inside a single 100-item page, so pagination
almost never adds a request. At the default 4s gap that sweeps a 19-revision stack in
about 90 seconds.

Without `--summary`, add one `differential.querydiffs` per revision covering every
diff there that carries a suggested edit. `querydiffs` returns each changed file's
full hunks, so it is the one response whose size scales with the patch rather than
with the comments.
