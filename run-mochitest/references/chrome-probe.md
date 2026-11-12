# Probing the browser directly instead of counting a test

A mochitest count only answers *whether* a thing passes; for *why*, drive the browser directly with a short Marionette script that reads the state and logs the events the test waits on, so the answer doesn't depend on who has focus. `run-mochitest`'s §2 holds the rule that sends you here.

Use mozilla-central's in-tree `desktop-theme-probing` skill for the probe itself. Its `firefox-probe.py` runs a snippet in the chrome context (`--context chrome`) or in a content document, and that skill covers the invocation, the display rung a probe needs and a stale build under a probe. Two recipes it doesn't spell out:

- `--context chrome --setup 'window.minimize()' --sample-ms 100,300,600,1200` prints a table of the same snippet over time, which is how to tell a state that sticks from one that gets reverted.
- Don't hand-roll the Marionette client instead. `Marionette(bin=...)` with no port attaches to whatever already listens on the default 2828, often a Firefox from another worktree: the probe then reads that build's markup, and its mutations land in someone else's browser. The tell for a foreign instance is `Services.dirsvc.get("XCurProcD", Ci.nsIFile).path`, or fetching the chrome URL of a file you just edited and finding your change absent.

## Reading the accessibility tree

`nsIAccessibilityService::getAccessibleFor` returns null for every element until an accessibility client is active, so a null on its own means nothing. Check a control element that certainly has an accessible (e.g. the urlbar input) before reading a null as a finding. To force the service on from the chrome context, set `Services.prefs.setIntPref("accessibility.force_disabled", 0)`, call `Cc["@mozilla.org/accessibilityService;1"].getService(Ci.nsIAccessibilityService)`, and poll `getAccessibleFor(document)` until it is non-null before reading any subtree (`--async` suits the poll); clear the pref afterwards. `accSvc.getStringRole(a.role)` gives readable roles, and `a.parent`, `a.childCount` and `a.getChildAt(i)` walk ancestry and subtrees.

A state that a focus handler clears synchronously never reaches a screen reader, because the accessibility focus event is delivered only once the accessible exists. A capture-phase `focus` listener catches it, but don't report it as a user-facing defect.
