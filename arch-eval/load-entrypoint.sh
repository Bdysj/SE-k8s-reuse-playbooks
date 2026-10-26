#!/bin/sh
set -eu
case "${NUM_CLIENTS:-5}" in *[!0-9]*|'') exit 2;; esac
exec locust -f /load/grouped-load.py --host "$HOST" --headless -r 1 -u "$NUM_CLIENTS" --only-summary > /tmp/locust.log 2>&1
