# Driving a content document from a browser-chrome test

A `SpecialPowers.spawn` task runs in a **system-principal sandbox in the same content
process as the page**, so an element the parent cannot reach is synchronously reachable
there. That makes it the right place to run a driver — and it is a different realm and a
different process from the test, so five things that look like product failures are
harness failures instead.

## `BrowserTestUtils` cannot be imported in a content process

`BrowserTestUtils.sys.mjs` calls `registerActors()` at module scope, which reaches
`ChromeUtils.registerWindowActor` and a `MOZ_ASSERT(XRE_IsParentProcess())` in
`JSActorService.cpp`. A debug build crashes; an opt build registers into the wrong table
and carries on. So no shared helper that a content task imports may pull it in, even
transitively, and the usual reaches — `waitForEvent`, `isVisible`, `waitForPopupEvent`,
`browserLoaded` — need a process-selected indirection through `ContentTaskUtils`.

## Install the sandbox's imports once, at head.js top level

`SpecialPowers.addTaskImport(symbol, url)` (`SpecialPowersChild.sys.mjs`) installs an
export as a sandbox global, and the import list rides along in every `spawn` message, so
one call at a head.js's top level covers every task in the file. `resource://testing-common/`
is registered process-wide by the harness, so a helper module needs no `FINAL_TARGET_FILES`
entry. The sandbox already supplies `Assert`/`ok`/`is`/`info` reporting into the mochitest
log, `ChromeUtils`, `ContentTaskUtils`, and an `EventUtils` global
(`SpecialPowersEventUtils.sys.mjs` — the real `EventUtils.js` loaded with an empty
`window`, so a helper that passes the window explicitly carries over with `content` as
that argument). In-tree precedent: `toolkit/components/narrate/test/NarrateTestUtils.sys.mjs`.

## A sandbox `Assert` outlives its task

The sandbox is new per spawn while the ESM registry is per-process, so a module instance
imported into one task is still alive for the next, holding the old task's `Assert`. A
callback landing after its task settles reaches `SpecialPowersParent`'s `_taskActors`
lookup and throws a `TypeError` **attributed to whichever test runs next**. Fix it with
both a bounded scope binding (read the scope off the task's own global with
`Cu.getGlobalForObject` at call time) and an optional-call sweep (`this.Assert?.`);
either alone is insufficient.

## Focus and blur only fire while the content document has focus

An opener must `await SimpleTest.promiseFocus(tab.linkedBrowser)` before it returns.
Without it a focus call sets no `focused` state and a `blur()` moves the active element
silently, and the symptom reads as a plausible product failure rather than a harness
error. Foregrounding the tab is the parent's job, so a content-side driver should be
handed a no-op `SimpleTest.promiseFocus`.

## A content-realm exception never reaches the log

Product code that wraps handlers in a try/catch reporting through `console.error` swallows
the failure entirely: a content process's `console.error` does not reach the mochitest
log. Use `dump()` when probing.

## Cost

One inter-process await per helper call. Fine for assertions; a tight sequence should take
one spawn and call several methods inside it. Below roughly six distinct behaviours,
hand-rolled spawns win and a facade is over-engineering.
