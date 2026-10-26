#!/usr/bin/env bash
# Matched architecture comparison for one service build.
#
# Build success is not resource validation. This script therefore runs the
# service against the real seeded MySQL, issues an identical business request
# profile to each build, and records startup time, CPU, memory, latency and
# error rate. Idle startup figures are not sufficient.
#
# Both architecture groups use identical images apart from architecture, an
# identical database instance, an identical request profile and an identical
# number of requests. Builds are run one at a time so they never contend with
# each other.
#
# Usage: measure.sh <service> <arch> <requests>

set -uo pipefail

SVC="${1:?service: shipping|ratings}"
ARCH="${2:?arch: arm64|amd64}"
N="${3:-200}"
NET=ddn-eval-net
NAME="ddn-${SVC}-${ARCH}"
OUT_DIR="$(cd "$(dirname "$0")/.." && pwd)/results/arch-eval"
mkdir -p "$OUT_DIR"

case "$SVC" in
  shipping) PORT=8080; READY="/health";  PROFILE=("/health" "/count" "/codes" "/cities/AU" "/cities/US" "/match/AU/syd") ;;
  ratings)  PORT=80;   READY="/_health"; PROFILE=("/_health" "/api/fetch/Watson" "/api/fetch/EMM" "/api/fetch/RMC") ;;
  *) echo "unknown service $SVC" >&2; exit 2 ;;
esac

docker rm -f "$NAME" >/dev/null 2>&1

# ratings needs the catalogue URL; it is unreachable here, so only DB-backed and
# health paths are exercised. Recorded as a limitation of this harness.
ENVS=(-e DB_HOST=mysql)
[ "$SVC" = ratings ] && ENVS=(-e PDO_URL="mysql:host=mysql;dbname=ratings;charset=utf8mb4" \
                              -e MYSQL_USER=ratings -e MYSQL_PASSWORD=iloveit)

echo "=== $SVC / $ARCH : starting"
T0=$(date +%s)
docker run -d --name "$NAME" --platform "linux/${ARCH}" --network "$NET" \
  "${ENVS[@]}" "ddn-eval/${SVC}:${ARCH}" >/dev/null || { echo "start failed"; exit 1; }

# Time to first ready response, measured from container start.
READY_S=""
for _ in $(seq 1 120); do
  code=$(docker run --rm --network "$NET" curlimages/curl:8.11.1 \
           -s -o /dev/null -w '%{http_code}' --max-time 5 \
           "http://${NAME}:${PORT}${READY}" 2>/dev/null)
  if [ "$code" = "200" ]; then READY_S=$(( $(date +%s) - T0 )); break; fi
  sleep 5
done
if [ -z "$READY_S" ]; then
  echo "  NEVER became ready within 600s"
  docker logs "$NAME" 2>&1 | tail -5
  docker rm -f "$NAME" >/dev/null 2>&1
  exit 1
fi
echo "  ready after ${READY_S}s"

# Warm up so JIT and opcache state are comparable, then settle.
for i in $(seq 1 20); do
  p="${PROFILE[$(( i % ${#PROFILE[@]} ))]}"
  docker run --rm --network "$NET" curlimages/curl:8.11.1 \
    -s -o /dev/null --max-time 15 "http://${NAME}:${PORT}${p}" >/dev/null 2>&1
done
sleep 5

IDLE=$(docker stats --no-stream --format '{{.CPUPerc}} {{.MemUsage}}' "$NAME" 2>/dev/null)
echo "  idle after warmup: $IDLE"

# Load phase. A single curl container issues all requests so that client cost is
# not counted against the service, and the request order is identical per run.
echo "  issuing $N requests"
SCRIPT=""
for i in $(seq 1 "$N"); do
  p="${PROFILE[$(( i % ${#PROFILE[@]} ))]}"
  SCRIPT+="curl -s -o /dev/null -w '%{http_code} %{time_total}\n' --max-time 20 'http://${NAME}:${PORT}${p}';"
done

LOAD_START=$(date +%s)
docker run --rm --network "$NET" --name "${NAME}-client" curlimages/curl:8.11.1 \
  sh -c "$SCRIPT" > "/tmp/${NAME}.raw" 2>/dev/null &
CLIENT_PID=$!

# Sample the service while the client runs.
: > "/tmp/${NAME}.stats"
while kill -0 "$CLIENT_PID" 2>/dev/null; do
  docker stats --no-stream --format '{{.CPUPerc}} {{.MemUsage}}' "$NAME" 2>/dev/null \
    >> "/tmp/${NAME}.stats"
  sleep 2
done
wait "$CLIENT_PID"
LOAD_S=$(( $(date +%s) - LOAD_START ))

RESTARTS=$(docker inspect "$NAME" --format '{{.RestartCount}}' 2>/dev/null)
OOM=$(docker inspect "$NAME" --format '{{.State.OOMKilled}}' 2>/dev/null)
docker rm -f "$NAME" >/dev/null 2>&1

python3 - "$SVC" "$ARCH" "$N" "$READY_S" "$LOAD_S" "$RESTARTS" "$OOM" \
         "/tmp/${NAME}.raw" "/tmp/${NAME}.stats" "$OUT_DIR/${SVC}-${ARCH}.json" <<'PY'
import json, sys, statistics as st
svc, arch, n, ready, load_s, restarts, oom, raw, stats, out = sys.argv[1:]
codes, times = [], []
for line in open(raw):
    parts = line.split()
    if len(parts) == 2:
        codes.append(parts[0])
        try: times.append(float(parts[1]) * 1000)
        except ValueError: pass
cpu, mem = [], []
for line in open(stats):
    p = line.split()
    if not p: continue
    try: cpu.append(float(p[0].rstrip('%')))
    except (ValueError, IndexError): pass
    if len(p) >= 2 and p[1].endswith(('MiB', 'GiB')):
        v = float(p[1][:-3]); mem.append(v * 1024 if p[1].endswith('GiB') else v)
ok = sum(1 for c in codes if c == '200')
times.sort()
def pct(q):
    return round(times[min(int(len(times) * q), len(times) - 1)], 1) if times else None
r = {
  "service": svc, "arch": arch, "requests_issued": int(n),
  "responses_received": len(codes), "http_200": ok,
  "error_rate_pct": round(100 * (1 - ok / len(codes)), 2) if codes else None,
  "distinct_status_codes": sorted(set(codes)),
  "ready_seconds": int(ready), "load_wall_seconds": int(load_s),
  "restarts": int(restarts) if restarts.isdigit() else restarts, "oom_killed": oom,
  "latency_ms": {"p50": pct(0.50), "p95": pct(0.95), "max": round(max(times),1) if times else None,
                 "mean": round(st.mean(times),1) if times else None},
  "cpu_pct_under_load": {"mean": round(st.mean(cpu),1) if cpu else None,
                         "max": round(max(cpu),1) if cpu else None, "samples": len(cpu)},
  "mem_mib_under_load": {"mean": round(st.mean(mem),1) if mem else None,
                         "max": round(max(mem),1) if mem else None},
}
json.dump(r, open(out, "w"), indent=2)
print(f"  ready={r['ready_seconds']}s  load={r['load_wall_seconds']}s  "
      f"200={ok}/{len(codes)}  err={r['error_rate_pct']}%  "
      f"p50={r['latency_ms']['p50']}ms p95={r['latency_ms']['p95']}ms  "
      f"cpu_mean={r['cpu_pct_under_load']['mean']}%  mem_max={r['mem_mib_under_load']['max']}MiB")
print(f"  -> {out}")
PY
