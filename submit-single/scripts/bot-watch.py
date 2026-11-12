#!/usr/bin/env python3
"""Wait for the review bots to report on freshly pushed revisions.

    bot-watch.py [--interval 300] [--timeout 3600] [--first-delay 600] [--once] D123 ...

A revision is settled once the Harbormaster build of its current diff has
finished and the Review Helper (reviewhelper-bot) has commented on that diff,
or once the build failed, the Code Review Bot (reviewbot) commented on that
diff, or the revision is closed or abandoned. Only comments posted after the
current diff was created count. The Review Helper reviews a revision once, so
on a re-push of a revision it already commented on, a finished build settles it.

Waits --first-delay seconds before the first poll, since no build finishes
sooner; pass 0 when starting it well after the push. Then polls every
--interval seconds, re-reading only the revisions that have not settled, and
prints one line per revision when anything changed. Every Conduit call goes
through phab.py, which shares one throttle bucket with the other urllib scripts
on the host, so keep the interval long. Exits 0 when every revision has settled, 3 at --timeout with the
unsettled ones listed, 2 on a Conduit error. A diff the bots could not apply
(e.g. its parent revision was missing at push time) shows `build=failed` with
no bot comment.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phab  # noqa: E402

BOTS = ("reviewbot", "reviewhelper-bot")
FINAL = {"passed", "failed"}


def bot_phids():
    users = phab.conduit("user.search", {"constraints": {"usernames": list(BOTS)}})[
        "data"
    ]
    return {u["phid"]: u["fields"]["username"] for u in users}


def poll(ids, bot_of):
    revs = phab.conduit("differential.revision.search", {"constraints": {"ids": ids}})[
        "data"
    ]
    diffs = {
        d["phid"]: d
        for d in phab.conduit(
            "differential.diff.search",
            {"constraints": {"phids": [r["fields"]["diffPHID"] for r in revs]}},
        )["data"]
    }
    builds = {
        b["fields"]["objectPHID"]: b["fields"]["buildableStatus"]["value"]
        for b in phab.conduit(
            "harbormaster.buildable.search",
            {"constraints": {"objectPHIDs": list(diffs)}},
        )["data"]
    }
    rows = {}
    for r in sorted(revs, key=lambda r: r["id"]):
        diff = diffs[r["fields"]["diffPHID"]]
        since = diff["fields"]["dateCreated"]
        counts = dict.fromkeys(BOTS, 0)
        reviewed_before = False
        for t in phab.conduit("transaction.search", {"objectIdentifier": r["phid"]})[
            "data"
        ]:
            who = bot_of.get(t["authorPHID"])
            if who and t["comments"]:
                if t["dateCreated"] >= since:
                    counts[who] += 1
                elif who == "reviewhelper-bot":
                    reviewed_before = True
        build = builds.get(diff["phid"], "none")
        status = r["fields"]["status"]["value"]
        settled = (
            status in ("published", "abandoned")
            or build == "failed"
            or counts["reviewbot"] > 0
            or (build in FINAL and (counts["reviewhelper-bot"] > 0 or reviewed_before))
        )
        rows[r["id"]] = (
            settled,
            f"D{r['id']}  diff={diff['id']}  build={build}  "
            + "  ".join(f"{b}={n}" for b, n in counts.items())
            + ("  settled" if settled else ""),
        )
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument(
        "--first-delay",
        type=int,
        default=600,
        help="seconds to wait before the first poll (ignored with --once)",
    )
    ap.add_argument("--once", action="store_true", help="poll once and exit")
    ap.add_argument("revs", nargs="+")
    a = ap.parse_args()
    ids = [int(r.rsplit("/", 1)[-1].lstrip("D")) for r in a.revs]
    deadline = time.monotonic() + a.timeout
    if not a.once:
        time.sleep(min(a.first_delay, a.timeout))
    bot_of = bot_phids()
    rows = {}
    last = None
    while True:
        rows.update(poll([i for i in ids if not rows.get(i, (False,))[0]], bot_of))
        lines = [rows[i][1] for i in sorted(rows)]
        if lines != last:
            print(time.strftime("%H:%M:%S"), flush=True)
            print("\n".join(lines), flush=True)
            last = lines
        pending = [f"D{i}" for i, (ok, _) in rows.items() if not ok]
        if not pending:
            print("SETTLED", flush=True)
            return 0
        if a.once or time.monotonic() + a.interval > deadline:
            print("TIMEOUT, unsettled: " + " ".join(pending), flush=True)
            return 3
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
