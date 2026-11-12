#!/bin/bash
# xmutter.sh <probe.sh> <args...>  -- run a probe as a GTK X11 client of a private
# headless mutter (Xwayland under a compositing WM: CSD, RGBA rounded window),
# the talos linux2404 compositor state. mutter 50 has no X11 WM mode, so the WM
# is a Wayland compositor and Firefox talks X11 to its Xwayland.
set -u
rt=$(mktemp -d "${TMPDIR:-/tmp}/xmutter-rt.XXXX")
chmod 700 "$rt"
wl=wl-$$
env -u WAYLAND_DISPLAY -u DISPLAY XDG_RUNTIME_DIR="$rt" GIO_USE_VFS=local dbus-run-session -- \
  mutter --headless --virtual-monitor 1600x1200 --wayland-display "$wl" >"$rt/mutter.log" 2>&1 &
mpid=$!
disp=
for i in $(seq 100); do
  disp=$(sed -n 's/.*Using public X11 display \(:[0-9]*\).*/\1/p' "$rt/mutter.log" | head -1)
  [ -n "$disp" ] && break
  sleep 0.1
done
cleanup() {
  for p in $(pgrep -P "$mpid" -x mutter) "$mpid"; do kill "$p" 2>/dev/null; done
  wait "$mpid" 2>/dev/null
  for m in "$rt"/gvfs "$rt"/doc; do mountpoint -q "$m" && fusermount -u "$m"; done
  [ -n "${XMUTTER_KEEP:-}" ] || rm -rf "$rt"
}
if [ -z "$disp" ]; then echo "xmutter: no X display appeared" >&2; cat "$rt/mutter.log" >&2; cleanup; exit 1; fi
auth=$(ls "$rt"/.mutter-Xwaylandauth.* 2>/dev/null | head -1)
sleep 1
echo "xmutter: DISPLAY=$disp XAUTHORITY=$auth runtime=$rt" >&2
env -u WAYLAND_DISPLAY DISPLAY="$disp" XAUTHORITY="$auth" GDK_BACKEND=x11 \
  XDG_CURRENT_DESKTOP=ubuntu:GNOME HEADLESS=--no-headless "$@"
rc=$?
cleanup
exit $rc
