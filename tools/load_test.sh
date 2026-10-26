#!/usr/bin/env bash
# Concurrent business load against the baseline, with resource sampling.
#
# Step 2 of the agreed sequence. The earlier feasibility check used sequential
# requests, which cannot reveal the failure mode that actually broke the baseline:
# Apache prefork in ratings spawning workers under concurrency. This issues a
# concurrent mix through the web Service so every request traverses the Service
# selector.
#
# Written for bash 3.2 as shipped by macOS.
#
# Usage: load_test.sh [concurrency] [requests_per_client] [namespace]

set -uo pipefail
CONC="${1:-10}"
PER="${2:-40}"
NS="${3:-robot-shop}"
OUT="$(cd "$(dirname "$0")/.." && pwd)/results/load-test"
mkdir -p "$OUT"
STAMP=$(date +%Y%m%d-%H%M%S)

# Business mix. Read paths plus one write path so the database is exercised.
PATHS='/
/api/catalogue/products
/api/catalogue/product/Watson
/api/user/uniqueid
/api/shipping/codes
/api/shipping/cities/AU
/api/payment/health
/api/cart/add/LOADTEST/Watson/1
/api/cart/cart/LOADTEST'

echo "=== concurrent load: ${CONC} clients x ${PER} requests"
BEFORE=$(kubectl get pods -n "$NS" --no-headers 2>/dev/null \
         | grep -v '^probe' | awk '{s+=$4} END{print s+0}')
echo "  restarts before: $BEFORE"

kubectl delete pod loadgen -n "$NS" --ignore-not-found >/dev/null 2>&1
sleep 3

# One pod, CONC parallel curl loops, so client scheduling cost is not charged to
# the services under test and the request mix is identical across runs.
kubectl run loadgen -n "$NS" --image=curlimages/curl:8.11.1 --restart=Never \
  --command -- sh -c "
cat > /tmp/paths <<'EOF'
$PATHS
EOF
i=0
while [ \$i -lt $CONC ]; do
  (
    n=0
    while [ \$n -lt $PER ]; do
      while read -r p; do
        [ -z \"\$p\" ] && continue
        curl -s -o /dev/null -w '%{http_code} %{time_total}\n' --max-time 30 \
          \"http://web:8080\$p\"
      done < /tmp/paths
      n=\$((n+1))
    done
  ) &
  i=\$((i+1))
done
wait
" >/dev/null 2>&1

# Sample resources while the load pod runs.
: > "/tmp/loadtest-$STAMP.stats"
for _ in $(seq 1 200); do
  phase=$(kubectl get pod loadgen -n "$NS" -o jsonpath='{.status.phase}' 2>/dev/null)
  [ "$phase" = "Succeeded" ] && break
  [ "$phase" = "Failed" ] && break
  docker stats --no-stream --format '{{.Name}} {{.CPUPerc}}' 2>/dev/null \
    | grep k3d | awk '{gsub(/%/,"",$2); s+=$2} END{print s+0}' \
    >> "/tmp/loadtest-$STAMP.stats"
  sleep 5
done

kubectl logs loadgen -n "$NS" > "/tmp/loadtest-$STAMP.raw" 2>/dev/null
AFTER=$(kubectl get pods -n "$NS" --no-headers 2>/dev/null \
        | grep -vE '^probe|^loadgen' | awk '{s+=$4} END{print s+0}')
NOTREADY=$(kubectl get pods -n "$NS" --no-headers 2>/dev/null \
           | grep -vE '^probe|^loadgen' | awk '$2!="1/1"' | wc -l | tr -d ' ')

# Per-service peak memory against its declared limit, which is what the OOM
# question turns on.
kubectl get pods -n "$NS" -o json 2>/dev/null > "/tmp/loadtest-$STAMP.pods"
kubectl top pods -n "$NS" --no-headers 2>/dev/null > "/tmp/loadtest-$STAMP.top" || true

kubectl delete pod loadgen -n "$NS" --ignore-not-found >/dev/null 2>&1

python3 - "$CONC" "$PER" "$BEFORE" "$AFTER" "$NOTREADY" \
  "/tmp/loadtest-$STAMP.raw" "/tmp/loadtest-$STAMP.stats" \
  "/tmp/loadtest-$STAMP.top" "/tmp/loadtest-$STAMP.pods" \
  "$OUT/load-$STAMP.json" <<'PY'
import json, sys, statistics as st
conc, per, before, after, notready, raw, stats, top, pods, out = sys.argv[1:]
codes, times = [], []
for line in open(raw, errors='ignore'):
    p = line.split()
    if len(p) == 2 and p[0].isdigit():
        codes.append(p[0])
        try: times.append(float(p[1]) * 1000)
        except ValueError: pass
cpu = []
for line in open(stats, errors='ignore'):
    try: cpu.append(float(line.strip()))
    except ValueError: pass
limits = {}
for d in json.load(open(pods)).get('items', []):
    for c in d['spec']['containers']:
        lim = (c.get('resources') or {}).get('limits', {}).get('memory')
        if lim: limits[d['metadata']['name']] = lim
usage = {}
for line in open(top, errors='ignore'):
    p = line.split()
    if len(p) >= 3 and p[2].endswith('Mi'):
        usage[p[0]] = int(p[2][:-2])
ok = sum(1 for c in codes if c.startswith('2'))
times.sort()
def pct(q): return round(times[min(int(len(times)*q), len(times)-1)],1) if times else None
r = {
 "concurrency": int(conc), "requests_per_client_cycle": int(per),
 "responses": len(codes), "success_2xx": ok,
 "error_rate_pct": round(100*(1-ok/len(codes)),2) if codes else None,
 "status_codes": {c: codes.count(c) for c in sorted(set(codes))},
 "latency_ms": {"p50": pct(.50), "p95": pct(.95), "p99": pct(.99),
                "max": round(max(times),1) if times else None},
 "k3d_cpu_pct_total": {"mean": round(st.mean(cpu),1) if cpu else None,
                       "max": round(max(cpu),1) if cpu else None,
                       "samples": len(cpu), "of_available": 800},
 "restarts_before": int(before), "restarts_after": int(after),
 "restarts_during_load": int(after)-int(before),
 "pods_not_ready_after": int(notready),
 "mem_usage_vs_limit": {k: {"used_Mi": v, "limit": limits.get(k)}
                        for k, v in sorted(usage.items())},
}
json.dump(r, open(out,'w'), indent=2)
print(f"  responses={len(codes)} 2xx={ok} err={r['error_rate_pct']}%")
print(f"  p50={r['latency_ms']['p50']}ms p95={r['latency_ms']['p95']}ms p99={r['latency_ms']['p99']}ms")
print(f"  k3d cpu mean={r['k3d_cpu_pct_total']['mean']}% max={r['k3d_cpu_pct_total']['max']}% of 800%")
print(f"  restarts during load={r['restarts_during_load']}  not ready after={notready}")
print(f"  status codes: {r['status_codes']}")
print(f"  -> {out}")
PY
