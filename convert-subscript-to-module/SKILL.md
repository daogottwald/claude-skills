---
name: convert-subscript-to-module
description: "Turn a chrome-window script that `Services.scriptloader.loadSubScript` loads into a per-window `.mjs` or system `.sys.mjs` module, landed as commits that keep the file's history. Use whenever a custom element or browser-window subscript is being modularized or taken out of `browser-main.js`'s load list, when the indentation-preserving `{ }` block around a class has to go, when eslint reports `no-shadow`, `no-undef` or `'import' and 'export' may appear only with 'sourceType: module'` after such a change, or when deciding whether a reindent belongs in `.git-blame-ignore-revs` and `.hg-annotate-ignore-revs`."
---

# Convert a chrome-window subscript to a module

`browser/docs/BrowserWindowScripts.md` explains how the browser window's scripts load,
which globals each kind of script sees, and the outline of a conversion. Read it first.
This skill adds the mechanics the page leaves out. Section names below refer to that page.

This covers files loaded into the browser window. A `.sys.mjs` that is already a module, a
content script, or a devtools bundle hits none of the mechanics below.

## Check the script's globals first

Only a script that just defines a custom element wraps its code in a `{ }` block; other
scripts declare top-level globals that the rest of the window uses (see section "Globals
Shared Between Scripts"). Check which kind you have. Every consumer of a global declared
outside a block has to import it after the conversion, so the job grows with the number of
consumers. The rest of this skill assumes a block-wrapped script.

## Pick the realm before touching the file

Section "Choose Where the Module Runs" gives the rule and the reasons: a custom element goes
into a per-window `.mjs` imported with `{ global: "current" }`, and other code can go into
a system `.sys.mjs` that takes the window as a parameter. `tabgroup.mjs`,
`tabsplitview.mjs` and `tabs.mjs` took the per-window route, `Tabbrowser.sys.mjs` the
system one. A helper extracted out of an element script can go into a system module even
when the element itself cannot.

**The directory's `tsconfig.json` decides whether tsc checks the module, not the realm.**
Read its `include` list before you convert. A pattern that matches the new file (e.g.
`*.sys.mjs`, `content/*.mjs`) makes it a checked root, so its errors join the project's. A
file outside `include` only lends its types to the files that import it, and the
`checkRootOnly` plugin suppresses its own errors (see section "Update the Module Paths").
`typescript-element-types` covers what a per-window element then costs to type.

## Three commits

Removing the block reindents the whole file, and prettier won't let you keep the old
indentation. Git then matches no rename even at `-M20%`, so `git log --follow`,
`hg annotate` and the review tool lose the file's history unless the conversion lands as
section "Keep the File's History" lays out. Plan the split up front; `stack-split-commit`
is for a commit that already exists.

1. A pure `git mv` to `.mjs`, with the path updates.
2. Block removal, de-indent, `export`, and the switch to `importESModule`, in one commit.
3. A follow-up patch adding commit 2 to the blame and annotate ignore files.

The landed subjects for one such conversion, as a shape to copy:

    Bug NNNNNN - Rename the tab strip element script to .mjs.
    Bug NNNNNN - Turn the tab strip element into a module.
    Bug NNNNNN - Ignore the tab strip reindent in blame and annotate.

Each body states the one thing the reviewer has to check and nothing else: the local
renames in 1, the indivisibility and `git diff -w` in 2.

### 1. The rename

Make the path updates the page lists, and also update `moz.build` where it lists the file.
Grep the chrome URI tree-wide rather than only the code that loads it: the performance
tests' expected-reflow stacks hold it as string data, and in-tree docs may name it. A
subscript with no `globals-browser-window` entry gets its globals only from the load list,
so add its entry in commit 2, where the need arises. The subscript loader ignores the
extension, so this commit should load and run as before; build and test it rather than
assuming.

Drop `"use strict"` here; eslint rejects it the moment the extension makes the file parse
as a module. The file stays sloppy until commit 2 makes it a real module, and nothing
outside the class body depends on strictness.

**Lint this commit on its own.** Section "Keep the File's History" explains the
`no-shadow` errors the extension turns on. The rule comes from the `**/*.mjs` block in
`eslint-plugin-mozilla`'s recommended config. The measured case reported `name`, `parent`
and `closed`, including the conventional `attributeChangedCallback(name, …)` parameter;
the rule allows `event`. Rename those locals in this commit and give the renames a line in
the commit message, since unexplained local renames inside a rename commit read as
unrelated drive-bys.

**Pick the lint-globals route now.** Section "Give the Module Its Lint Globals" describes
the two routes and why you can't use both. Choose by type-checking:

- **The `globals-browser-window` entry** gives the module the whole browser-window set,
  system modules included, so the body needs no rewriting. It suits a module that no
  `checkJs` project covers.
- **A `/* global … */` comment** suits a file tsc checks. `tools/@types/browser-window.d.ts`
  leaves system modules out, so a bare `SessionStore` type-checks only when the module
  imports it. The module imports every system module it reads (`XPCOMUtils.declareLazy`
  for a lazy one), and the file comes out of the config list. The tabbrowser window
  modules (`tab.mjs`, `tabs.mjs`, `tabgroup.mjs`, `tabsplitview.mjs`) use this route, and
  the eslint owner accepted it there.

The config entry has no effect until commit 2: while the file is on the load list, the
`import-browser-window-globals` rule gives it the browser-window globals regardless (see
section "Globals Shared Between Scripts"). So test the route by linting commit 2. Measured
on a three-thousand-line element script at that commit: dropping the entry reports 78
`no-undef` errors, restoring it clears them, and a `/* global … */` comment on top of the
entry reports 15 `no-redeclare` "already defined as a built-in global variable" errors.
The rename commit passes lint either way and proves nothing.

**On the comment route, a system-module import cannot come before commit 2.** While the
`loadSubScript` line still loads the renamed file, it hands the file the window set, so a
local `const { ContentSharingUtils } = ChromeUtils.importESModule(…)` fails `no-shadow`
against the window's getter of that name. Two consequences:

- A local import the subscript already had has to move out of the way before the
  rename. A commit ahead of the rename can put it into the file's `lazy` module getters,
  and commit 2 then switches those getters to `declareLazy`.
- A file that reads system modules bare does not type-check after the rename, since
  tsc has no declaration for them. Put it on the project's tsconfig `exclude` list in the
  rename commit, and take it off in commit 2, where the imports land. A file that reads
  no system module needs no entry.

On the config route the set is large, so a top-level name in the module that another
browser-window script also declares trips `no-shadow`, e.g. a local `const DeferredTask`
against the one another subscript declares. The measured file had no such collision; check
rather than assume.

**Do not reach for `EXTRA_SCRIPTS`** in
`eslint-plugin-mozilla/lib/environments/browser-window.mjs` instead. That helper parses
every listed file as a *script*, so the first `export` raises `'import' and 'export' may
appear only with 'sourceType: module'` and eslint fails for the **whole tree**, not just
your file.

### 2. Block removal, `export`, and the loader switch

Section "Keep the File's History" explains why these edits form one commit. Say so in the
commit message, and tell the reviewer that `git diff -w` shows the actual change: the
`export`, the `import`, and whatever prettier re-joined.

Import the module with `{ global: "current" }` from the line that loaded the subscript. On
the config route the body needs no rewriting. On the comment route this commit adds the
`/* global … */` comment and the system-module imports, drops the file's config entry, and
takes the file off the tsconfig `exclude` list. If another file imports the module's
types, run `./mach ts paths` (see section "Update the Module Paths"). It rewrites
`tools/@types/generated/tspaths.json` and `lib.gecko.modules.d.ts`; commit only your own
entries, since it also picks up unrelated drift.

### 3. The blame and annotate ignore entries

Section "Keep the File's History" explains why this is a follow-up patch. The same bug is
its natural home.

The two files take different hashes and different formats:

    .git-blame-ignore-revs      <git sha> # Bug NNNNNN - <subject>
    .hg-annotate-ignore-revs    <hg changeset> - <Author Name> - Bug NNNNNN - <subject>

Take the mercurial changeset from Lando's mapping rather than reusing the git SHA:
`curl -s https://lando.moz.tools/api/git2hg/firefox/<full git sha>` returns
`{"git_hash": …, "hg_hash": …}`. Sanity-check it once on an entry already in both files
before trusting it. Both files are tree-wide and want a tree-wide reviewer rather than the
component's rotation; run `find-reviewer`.

**Judge the entry by measurement, not by whether the commit is whitespace-*only*.** It
never is: two fewer indentation columns let prettier re-join lines that now fit in eighty
columns, and in-tree entries include eslint sweeps that changed real code. Isolate the real
changes with `git diff -w` and count the reindented lines the entry rescues against them.
One element pair measured ~1300 surviving lines it would otherwise misattribute against
~10 it would hide, which is not close. On the much larger `Tabbrowser.sys.mjs`, `git diff
-w` came to 49 hunks collapsing 180 lines into 62, and the entry still won.

## Where the system-module route differs

All three commits hold, with the rename going to `.sys.mjs`; `Tabbrowser.sys.mjs` is the
worked example of the last two. The lint-globals choice disappears, and the cost moves into
the body: a system module has none of the window's globals (see section "Choose Where the
Module Runs"), so every bare `gBrowser`, `document` or `SessionStore` becomes an import, a
parameter, or a member read off one. `Tabbrowser.sys.mjs` takes the window in its
constructor and keeps `this.documentGlobal` and `this.document`. That is real work with
real review risk, not a lint fix. The same section covers the wiring, a `browser-window-`
category in `BrowserComponents.manifest` in place of the load list.

Non-window globals need checking too, and the webidl misleads: `UserInteraction.webidl`
says `[ChromeOnly, Exposed=Window]`, yet the system global has `UserInteraction`. Probe
every bare global the file uses instead, which takes under a minute:

```bash
LD_LIBRARY_PATH=obj-*/dist/bin obj-*/dist/bin/xpcshell -f probe.js
```

Have `probe.js` print `typeof X` for each name inside a try/catch, since a missing global
throws `ReferenceError`. Without `LD_LIBRARY_PATH` pointing at the objdir's `dist/bin`,
xpcshell fails to load `libxul.so`. Measured on the system global: `Services`, `Ci`, `Cc`,
`Cu`, `ChromeUtils`, `Glean`, `console`, `Localization`, `WebExtensionPolicy`,
`UserInteraction` and DOM constructors such as `Node`, `MouseEvent`, `HTMLAnchorElement`
and `CustomEvent` are present. `setTimeout`, `requestAnimationFrame`, `performance` and
`AppConstants` are absent, which is why `Timer.sys.mjs` exports `setTimeout`. An absent
global comes from an import or from the window (`this.documentGlobal.setTimeout`). Read a
present one off the window anyway when its value is per-window: timers must die with the
window.

## Verify

`./mach build faster` after commit 1, because the jar manifest changed and the objdir needs
the new symlink; content-only edits afterwards are live without a rebuild.

On the per-window route, confirm the element **upgraded** rather than that the window
opened: read `customElements.get("<tag>")` and a member off the live element in a
chrome-context probe. The in-tree `desktop-theme-probing` skill's `firefox-probe.py --context chrome` drives that; do not hand-roll a
Marionette on the default port, which attaches to whatever Firefox another worktree already
has listening.

Lint each commit on its own rather than only the tip, and run the component's browser-chrome
directory at the tip.
