# Display rungs for a local mochitest

`run-mochitest`'s §2 holds the decision table; this file holds what a mochitest needs past it. mozilla-central's in-tree `desktop-theme-probing` skill describes the rungs themselves: `references/running-a-probe.md` §1 says what each one supplies, and `references/platform-linux.md`, `platform-macos.md` and `platform-windows.md` give each platform's headless behaviour and displays. Read those for the rung, and this file for how a test run fails on it.

**Name what the failing config requires before picking one** (e.g. real widget backend, a window manager, an input seat, a value the desktop supplies), then take the cheapest rung that supplies it and check it actually did. Each rung fails in a way that mimics a product bug.

## 1. Headless

- **Native event synthesis is a stand-in.** Async pan/zoom (APZ) itself runs headless: `gfxPlatform::AsyncPanZoomEnabled` and `nsIWidget::UseAPZ` have no headless branch, the harness defines `NativePanHandlerForHeadless`, and momentum comes from `apz.test.headless.simulate_momentum`. A gesture-driven measurement here measures the stand-in rather than the platform, and a green headless run says nothing about a debug build on a real display: an ASR-related assert in `nsIFrame::BuildDisplayListForChild` fired only on one.
- **No drag service on Linux.** The GTK `nsDragService::GetInstance` returns null under headless, so `EventUtils.startDragSession` throws `NS_ERROR_XPC_GS_RETURNED_FAILURE` and kills the test before its first assertion.
- **Size modes do work**: `HeadlessWidget::SetSizeMode` notifies `SizeModeChanged` itself, so minimize/maximize/restore and the events they emit run with no WM and no contention. The cross-platform logic above the widget backend is testable here even when the backend bug itself is not.
- Its own noise (e.g. tooltip tests, and primary-selection asserts on Linux): treat a headless-only failure as suspect until checked elsewhere.

## 2. An isolated real display

Only Linux has one; on macOS and Windows a non-headless run opens on the logged-in desktop whatever you pass it, so the ladder goes from headless straight to §3 or §4. The Linux commands for a test run are in `references/linux-display.md`. Whatever supplies the display, allocate a fresh one rather than a fixed number, never kill a display server or delete a lock you didn't create, and grep the log for the display's own startup failure separately: a display that never came up otherwise scores as a wall of test failures.

## 3. The user's live session

Ask for it only when the failure needs the real backend plus a WM plus an input seat and no isolated rung supplies them. Say what you'll run and roughly how long, ask them to stay off mouse, keyboard and window-switching, tell them when the desktop is theirs again, and ask once for a whole batch. **A run they touched is unscored**: discard it, since the same binary can score 18-pass/3-fail and 3-pass/2-fail minutes apart on a contended desktop.

## 4. CI

A try run supplies the real backend under isolation on every platform. Prefer it to the user's session for a backend-specific failure on macOS or Windows. When no rung can supply what the failure needs, report the repro as environment-limited and ask, rather than passing off a same-shaped failure from another cause.
