# Isolated real displays on Linux

The Linux commands for a test run on the rungs `display-rungs.md` lists. mozilla-central's in-tree `desktop-theme-probing` skill covers the displays themselves in `references/platform-linux.md` (DISPLAYS, HEADLESS, FONT, SCROLLBARS), including the Wayland-environment leak and the packages to install. This file holds what is specific to running a test, and the one rung that skill lacks. None of it carries over to macOS or Windows.

## `xvfb-run`: real X11, no window manager

```
mkdir -p artifacts
xvfb-run -a -s "-screen 0 1600x1200x24" \
  env -u WAYLAND_DISPLAY MOZ_ENABLE_WAYLAND=0 GDK_BACKEND=x11 \
  ./mach mochitest <path> > artifacts/mochitest.log 2>&1; echo "exit=$?"
```

- Strip the Wayland environment explicitly, as above, and check that `WAYLAND_DISPLAY` is unset in the child's environment before believing a run was isolated.
- `./mach wpt` leaks to the user's desktop the same way under `xvfb-run -a`; use `./mach wpt --headless`. A `mach wpt` window that stays open after the run reports PASS is harness behaviour, reproducible with an existing test such as `testing/web-platform/mozilla/tests/css/css-images/moz-image-decoding.html`, so don't look for the cause in your own test's cleanup.
- `-a` auto-allocates: never hardcode `:99`, and never kill an `Xvfb` or delete a `/tmp/.X*-lock` you didn't create.
- Grep `Xvfb failed to start` separately, or a display that never came up scores as failures.
- With no window manager, anything resting on WM policy (e.g. minimize/maximize/restore and their events, cross-window activation, focus handoff) fails whatever the patch.
- Installing the package needs a TTY an agent lacks: ask the user, or run `pkexec apt install -y xvfb`.

## mutter's headless backend: Wayland, no input seat

`mutter|gnome-shell --headless --virtual-monitor 1920x1080 --wayland-display=<name>` gives a real compositor with a WM. Its `wl_seat` advertises `capabilities(0)` (`WAYLAND_DEBUG=1` prints it), so the browser never gets keyboard focus and a focus-waiting harness like browser-chrome wedges before its first assertion, reading as a hang. When no nested compositor is available either, a Wayland-specific failure needs the user's session or a try run.

## X11 under a compositing WM: Xwayland of a private headless mutter

Headless and a bare Xvfb give an opaque window with every surface opaque and corner radii 0, so nothing about blended or masked composites reproduces there. mutter 50 has no `--x11` window-manager mode, so without another compositing X11 WM, the rung is a private `mutter --headless --virtual-monitor 1600x1200` under `dbus-run-session`, with Firefox talking X11 to its Xwayland. `~/.claude/skills/run-mochitest/scripts/xmutter.sh <command> [args...]` does the setup and runs the command inside it, here with `desktop-theme-probing`'s probe:

```
~/.claude/skills/run-mochitest/scripts/xmutter.sh ./mach python --virtualenv wpt \
  .claude/skills/desktop-theme-probing/scripts/firefox-probe.py \
  --binary <objdir>/dist/bin/firefox --context chrome --no-headless \
  --setpref gfx.webrender.software=true -e '...'
```

- The script gives mutter a private `XDG_RUNTIME_DIR`, reads the display from mutter's `Using public X11 display :N` log line, and runs the command with `WAYLAND_DISPLAY` unset, `GDK_BACKEND=x11`, `XAUTHORITY=$XDG_RUNTIME_DIR/.mutter-Xwaylandauth.*` and `XDG_CURRENT_DESKTOP=ubuntu:GNOME`. It sets `GIO_USE_VFS=local` so gvfs doesn't mount a fuse dir there, and on exit kills its own mutter by PID and unmounts. `XMUTTER_KEEP=1` keeps the runtime dir with `mutter.log`.
- Pass `gfx.webrender.software=true` explicitly: a non-headless run otherwise picks hardware WebRender on llvmpipe.
- Check that it took: `customtitlebar` is set on the root, `windowProtocol` reads `x11`, and a WebRender capture shows content surfaces with `rounded_clip_radii` 15 and the chrome slice as an Alpha surface. That matches CI's talos linux2404 captures, which run in a real GNOME X11 session under mutter.
- A Marionette-driven probe gets focus here: `document.hasFocus()`, `Services.focus.activeWindow` and `gURLBar.focus()` all work. The seat caveat in the section above still applies to a focus-waiting harness such as browser-chrome.
