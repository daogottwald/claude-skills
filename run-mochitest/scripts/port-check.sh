#!/bin/bash
# Who holds the mochitest httpd port (fixed 127.0.0.1:8888, host-wide, so another
# worktree is no protection). --wait blocks until it is free. Read-only: never kills.
set -u
busy() { ss -ltn | grep -q '127\.0\.0\.1:8888'; }
if [ "${1-}" = --wait ]; then
  while busy; do sleep 20; done
  echo "port 8888 free"
elif busy; then
  ss -ltn | grep 8888
  pgrep -af 'mochitest|runtests\.py' | grep -v pgrep | cut -c1-100
else
  echo "port 8888 free"
fi
