#!/usr/bin/env bash
# Build the remaining Robot Shop services for linux/arm64.
#
# Step 1 of the agreed sequence. Only the architecture changes here; resource
# limits stay as currently declared so that the architecture change is the single
# variable under test. Chart defaults are deliberately NOT restored yet.
#
# shipping and ratings already have arm64 builds from the feasibility check and
# use their own adapted Dockerfiles in this directory. mysql stays amd64 for now,
# because it is not the hotspot and MySQL 5.7 has no arm64 image.
#
# Written for bash 3.2, which macOS ships, so no associative arrays.
#
# Usage: build_arm64.sh [service ...]     default: all remaining

set -uo pipefail
cd "$(dirname "$0")/.."
SRC=sources/robot-shop
LOG=/tmp/ddn-arm64-build
mkdir -p "$LOG"

# Each entry: service:build-context:image-name
# The image name matches the Robot Shop name used in the baseline manifest so the
# mapping stays obvious when the manifest is repointed.
ALL="cart:cart:rs-cart
catalogue:catalogue:rs-catalogue
user:user:rs-user
payment:payment:rs-payment
dispatch:dispatch:rs-dispatch
web:web:rs-web
mongo:mongo:rs-mongodb
load:load-gen:rs-load"

if [ $# -gt 0 ]; then
  WANT="$*"
else
  WANT=""
fi

ok=""; fail=""
while IFS=: read -r svc ctx img; do
  [ -z "$svc" ] && continue
  if [ -n "$WANT" ] && ! echo " $WANT " | grep -q " $svc "; then continue; fi
  printf '%-12s building ... ' "$svc"
  if docker buildx build --platform linux/arm64 \
        -t "ddn-eval/$img:arm64" --load "$SRC/$ctx" \
        > "$LOG/$svc.log" 2>&1; then
    arch=$(docker inspect "ddn-eval/$img:arm64" --format '{{.Architecture}}' 2>/dev/null)
    size=$(docker images "ddn-eval/$img:arm64" --format '{{.Size}}' 2>/dev/null | head -1)
    echo "ok   arch=$arch size=$size"
    ok="$ok $svc"
  else
    echo "FAILED  (see $LOG/$svc.log)"
    grep -iE '^ERROR|E: |error:|404|not found|cannot|npm ERR' "$LOG/$svc.log" \
      | tail -3 | sed 's/^/             /'
    fail="$fail $svc"
  fi
done <<EOF
$ALL
EOF

echo
echo "built ok:${ok:- none}"
if [ -n "$fail" ]; then echo "failed:  $fail"; exit 1; fi
exit 0
