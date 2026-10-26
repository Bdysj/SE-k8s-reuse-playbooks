#!/usr/bin/env python3
"""Deploy, reset and confirm the Robot Shop baseline.

Section 3.5.3 requires each trial to start from a declared snapshot and requires
the controller to restore the environment and confirm the reset before another
trial. This tool implements that, and it is written to fail closed: any check
that cannot be evaluated is a failure, never a pass.

Design points that earlier versions got wrong:

1. Strict tiering. Applying the whole manifest and then scaling down races: the
   application and load Pods start before the scale-down lands. Here the manifest
   is applied with every workload already at zero replicas, then tiers are raised
   in order, and each tier waits for the previous generation of Pods to be gone.

2. Fail closed. Every kubectl call is checked for a non-zero exit code and for
   empty output. A probe that cannot be created, or an exec that returns nothing,
   is a failed verification rather than a silent pass.

3. Complete acceptance. Verification asserts the expected workload set, readiness,
   a restart budget, ready endpoints, HTTP status *and response content*, and
   compares a declared baseline fingerprint against the live cluster. Status alone
   is not sufficient: the nginx front end returns a styled error page, and a
   misrouted upstream can still produce a 2xx from a cached or error path.

Usage:
  reset_baseline.py               reset in place, then confirm
  reset_baseline.py --hard        delete the namespace first, then deploy
  reset_baseline.py --verify      confirm only, change nothing
  reset_baseline.py --write-fingerprint   record the current declared fingerprint
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "manifests" / "baseline.arm64.yaml"
FINGERPRINT = MANIFEST.with_suffix(".fingerprint.json")
NS = "robot-shop"
CONTEXT = "k3d-lab"

TIERS = [
    ("data stores", ["mysql", "mongodb", "redis", "rabbitmq"]),
    ("app services", ["catalogue", "user", "cart", "payment", "shipping",
                      "ratings", "dispatch", "web"]),
    ("workload", ["load"]),
]
ALL_WORKLOADS = [n for _, names in TIERS for n in names]

# Restart budget for a confirmed baseline. Zero: a restart during bring-up means
# the declared settings or the tier order are still wrong, and Section 3.6 would
# otherwise attribute the resulting failure flags to a repair.
RESTART_BUDGET = 0

# Business checks. Each asserts an HTTP status and a content predicate, because a
# status check alone passes on an nginx error page.
CHECKS = [
    ("home",      "/",                          200, r"Stan's Robot Shop"),
    ("catalogue", "/api/catalogue/products",    200, r'"sku"\s*:'),
    ("shipping",  "/api/shipping/codes",        200, r'"code"\s*:'),
    ("user",      "/api/user/uniqueid",         200, r'"uuid"\s*:'),
    ("payment",   "/api/payment/health",        200, r"OK"),
    ("ratings",   "/api/ratings/api/fetch/Watson", 200,
                  r'"rating_count"\s*:\s*\d+'),
]
PROBE_IMAGE = "curlimages/curl@sha256:c1fe1679c34d9784c1b0d1e5f62ac0a79fca01fb6377cdd33e90473c6f9f9a69"
PROBE = "probe"


class Failure(Exception):
    """A verification step could not be evaluated or did not hold."""


def kubectl(*args: str, namespaced: bool = True, allow_fail: bool = False) -> str:
    cmd = ["kubectl", "--context", CONTEXT] + (["-n", NS] if namespaced else []) + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        if allow_fail:
            return ""
        raise Failure(f"kubectl {' '.join(args)} failed rc={r.returncode}: "
                      f"{r.stderr.strip()[:300]}")
    return r.stdout


def kjson(*args: str, allow_fail: bool = False) -> dict:
    out = kubectl(*args, "-o", "json", allow_fail=allow_fail)
    if not out.strip():
        if allow_fail:
            return {}
        raise Failure(f"kubectl {' '.join(args)} returned no output")
    return json.loads(out)


def workload_kind(name: str) -> str:
    return "statefulset" if name == "redis" else "deployment"


# --------------------------------------------------------------------------- #
# normalisation
# --------------------------------------------------------------------------- #
# The API server rewrites resource quantities into its own canonical form, for
# example 1024Mi to 1Gi and 2000m to 2, and defaults an unset targetPort to the
# port value. Comparing raw strings would report those as drift. Section 3.4.1
# requires documented normalisation so that a harmless difference is not treated
# as a contradiction; the same principle applies to confirming a reset.

_MEM_UNITS = {"": 1, "k": 10**3, "M": 10**6, "G": 10**9, "T": 10**12,
              "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40}


def norm_quantity(v) -> object:
    """Canonicalise a Kubernetes quantity to an integer, CPU in milli-units."""
    if v is None:
        return None
    s = str(v).strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(m|[kMGT]i?|)", s)
    if not m:
        return s
    num, unit = float(m.group(1)), m.group(2)
    if unit == "m":                      # CPU milli-units
        return int(num)
    if unit in ("", "k", "M", "G", "T", "Ki", "Mi", "Gi", "Ti"):
        # A bare number is ambiguous: CPU cores for cpu keys, bytes otherwise.
        # Callers pass cpu values through norm_cpu instead.
        return int(num * _MEM_UNITS[unit])
    return s


def norm_cpu(v) -> object:
    if v is None:
        return None
    s = str(v).strip()
    if s.endswith("m"):
        return int(float(s[:-1]))
    try:
        return int(float(s) * 1000)
    except ValueError:
        return s


def norm_resources(res: dict) -> dict:
    out: dict = {}
    for section in ("requests", "limits"):
        if section not in (res or {}):
            continue
        vals = {}
        for k, v in res[section].items():
            vals[k] = norm_cpu(v) if k == "cpu" else norm_quantity(v)
        out[section] = vals
    return out


def norm_env(env) -> list:
    """Compare environment names AND values.

    An earlier version compared only names, so a changed value looked identical.
    That matters directly here: a configuration-repair candidate works by changing
    an env value, so a fingerprint blind to values could not detect drift or
    confirm a reset. valueFrom entries are rendered as a stable descriptor rather
    than resolved, because their resolved value is not knowable from the manifest.
    """
    out = []
    for e in env or []:
        if "value" in e:
            out.append([e["name"], str(e["value"])])
        elif "valueFrom" in e:
            src = e["valueFrom"]
            kind = next(iter(src), "?")
            ref = src.get(kind, {})
            desc = ref.get("fieldPath") or ref.get("key") or ref.get("name") or ""
            out.append([e["name"], f"valueFrom:{kind}:{desc}"])
        else:
            out.append([e["name"], ""])
    return sorted(out)


def norm_ports(ports: list[dict]) -> list[dict]:
    out = []
    for p in ports or []:
        tp = p.get("targetPort")
        # An unset or zero targetPort defaults to the port value.
        if tp in (None, 0, "0"):
            tp = p.get("port")
        out.append({"name": p.get("name"), "port": p.get("port"), "targetPort": tp})
    return sorted(out, key=lambda p: (p["port"] or 0, str(p["name"])))


# --------------------------------------------------------------------------- #
# fingerprint
# --------------------------------------------------------------------------- #

def workload_contract(spec: dict) -> dict:
    template = spec["template"]
    return {"pod_labels": template["metadata"].get("labels", {}),
            "volumes": sorted(template["spec"].get("volumes", []), key=lambda v: v["name"]),
            "strategy_type": spec.get("strategy", {}).get("type", "RollingUpdate"),
            "annotations": template["metadata"].get("annotations", {}),
            "affinity": template["spec"].get("affinity", {}),
            "nodeSelector": template["spec"].get("nodeSelector", {})}


def container_contract(c: dict) -> dict:
    return {"image": c["image"], "resources": norm_resources(c.get("resources", {})),
            "env": norm_env(c.get("env")), "command": c.get("command", []),
            "args": c.get("args", []),
            "volumeMounts": sorted(c.get("volumeMounts", []), key=lambda v: v["name"])}


def declared_fingerprint() -> dict:
    """Fingerprint of the declared snapshot, read from the rendered manifest."""
    import yaml
    fp: dict = {"workloads": {}, "services": {}}
    for d in yaml.safe_load_all(MANIFEST.read_text(encoding="utf-8")):
        if not d:
            continue
        kind, name = d.get("kind"), d["metadata"]["name"]
        if kind in ("Deployment", "StatefulSet"):
            spec = d["spec"]
            fp["workloads"][name] = {
                "kind": kind,
                "replicas": spec.get("replicas", 1),
                **workload_contract(spec),
                "selector": spec.get("selector", {}).get("matchLabels"),
                "containers": {
                    c["name"]: container_contract(c)
                    for c in spec["template"]["spec"]["containers"]
                },
            }
        elif kind == "Service":
            fp["services"][name] = {
                "type": d["spec"].get("type", "ClusterIP"),
                "selector": d["spec"].get("selector"),
                "ports": norm_ports(d["spec"].get("ports", [])),
            }
    return fp


def live_fingerprint() -> dict:
    """Same fields, read from the live cluster, for reset confirmation."""
    fp: dict = {"workloads": {}, "services": {}}
    for kind in ("deployments", "statefulsets"):
        for d in kjson("get", kind).get("items", []):
            name = d["metadata"]["name"]
            spec = d["spec"]
            fp["workloads"][name] = {
                "kind": d["kind"] if d.get("kind") else
                        ("StatefulSet" if kind == "statefulsets" else "Deployment"),
                "replicas": spec.get("replicas", 1),
                **workload_contract(spec),
                "selector": spec.get("selector", {}).get("matchLabels"),
                "containers": {
                    c["name"]: container_contract(c)
                    for c in spec["template"]["spec"]["containers"]
                },
            }
    for d in kjson("get", "services").get("items", []):
        fp["services"][d["metadata"]["name"]] = {
            "type": d["spec"].get("type", "ClusterIP"),
            "selector": d["spec"].get("selector"),
            "ports": norm_ports(d["spec"].get("ports", [])),
        }
    return fp


def diff_fingerprint(declared: dict, live: dict) -> list[str]:
    """Report every difference. StatefulSet Pod template kind is normalised."""
    diffs: list[str] = []
    for section in ("workloads", "services"):
        dk, lk = set(declared[section]), set(live[section])
        for missing in sorted(dk - lk):
            diffs.append(f"{section}: {missing} declared but absent")
        for extra in sorted(lk - dk):
            diffs.append(f"{section}: {extra} present but not declared")
        for name in sorted(dk & lk):
            a, b = declared[section][name], live[section][name]
            for field in a:
                if field == "kind":
                    continue
                if a[field] != b.get(field):
                    diffs.append(f"{section}: {name}.{field} declared={a[field]!r} "
                                 f"live={b.get(field)!r}")
    return diffs


# --------------------------------------------------------------------------- #
# bring-up
# --------------------------------------------------------------------------- #

def pods_of(name: str) -> list[dict]:
    sel = f"service={name}" if name != "load" else "service=load"
    return kjson("get", "pods", "-l", sel, allow_fail=True).get("items", [])


def wait_old_pods_gone(names: list[str], timeout: int) -> None:
    """Wait until no Pod of these workloads is still terminating."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        lingering = [p["metadata"]["name"] for n in names for p in pods_of(n)]
        if not lingering:
            return
        time.sleep(5)
    raise Failure(f"Pods of {names} were still present after {timeout}s")


def wait_tier_ready(names: list[str], timeout: int) -> None:
    deadline = time.time() + timeout
    pending = list(names)
    while pending and time.time() < deadline:
        still = []
        for n in pending:
            obj = kjson("get", workload_kind(n), n, allow_fail=True)
            if not obj:
                still.append(n)
                continue
            want = obj["spec"].get("replicas", 1)
            got = obj.get("status", {}).get("readyReplicas", 0)
            if got < want:
                still.append(n)
        if not still:
            return
        if still != pending:
            print(f"    still waiting: {', '.join(still)}")
        pending = still
        time.sleep(15)
    raise Failure(f"tier not ready within {timeout}s, outstanding: {pending}")


def apply_at_zero() -> None:
    """Replace declared workload/Service specs, including fields added by repairs.

    Plain apply cannot reliably remove env/affinity/annotations introduced by
    imperative patches outside its field ownership. Full replacement restores the
    contract; preserve only immutable/allocated Service networking fields.
    """
    import yaml
    docs = [d for d in yaml.safe_load_all(MANIFEST.read_text()) if d]
    expected = {kind: {d["metadata"]["name"] for d in docs if d["kind"] == kind}
                for kind in ("Deployment", "StatefulSet", "Service")}
    for kind, names in expected.items():
        for obj in kjson("get", kind.lower()).get("items", []):
            if obj["metadata"]["name"] not in names:
                kubectl("delete", kind.lower(), obj["metadata"]["name"], "--wait=true")
    # Remove round-local fault controllers through their normal recovery path.
    if kubectl("api-resources", "--api-group=chaos-mesh.org", "-o", "name", namespaced=False, allow_fail=True):
        kubectl("delete", "podchaos", "--all", "--ignore-not-found", "--timeout=60s")
    for d in docs:
        kind, name = d["kind"], d["metadata"]["name"]
        if kind in ("Deployment", "StatefulSet"):
            d["spec"]["replicas"] = 0
        live = kjson("get", kind.lower(), name, allow_fail=True)
        if live and kind in ("Deployment", "StatefulSet", "Service"):
            d["metadata"]["resourceVersion"] = live["metadata"]["resourceVersion"]
            if kind == "Service":
                for key in ("clusterIP", "clusterIPs", "ipFamilies", "ipFamilyPolicy",
                            "healthCheckNodePort", "allocateLoadBalancerNodePorts"):
                    if key in live["spec"]:
                        d["spec"][key] = live["spec"][key]
                old_ports = {p.get("name", p["port"]): p for p in live["spec"].get("ports", [])}
                for port in d["spec"].get("ports", []):
                    old = old_ports.get(port.get("name", port["port"]), {})
                    if "nodePort" in old:
                        port["nodePort"] = old["nodePort"]
            action = "replace"
        else:
            action = "apply"
        r = subprocess.run(["kubectl", "--context", CONTEXT, "-n", NS, action, "-f", "-"],
                           input=json.dumps(d), capture_output=True, text=True, timeout=120)
        if r.returncode:
            raise Failure(f"{action} {kind}/{name}: {r.stderr.strip()[:300]}")


# --------------------------------------------------------------------------- #
# verification
# --------------------------------------------------------------------------- #

SEED_IMAGE = "ddn-eval/mysql-seed:5.7"
SEED_TAR = "/seed/mysql-5.7-seed.tar"
SEED_SHA256 = "270b4241a511d725e08b577632bd69a17fe25c71a49e432ac1634136169547c5"
# Seed content that must be present after a restore. Taken from seed.meta.yaml.
SEED_CITIES_ROWS = 948833
DB_CLIENT_IMAGE = "ddn-eval/rs-mysql-db:amd64"


def restore_mysql_seed(timeout: int = 900) -> None:
    """Rewrite the mysql work volume from the fixed read-only seed.

    Runs only at the start of a round, never during one. An ordinary pod restart
    must not restore the seed, or it would silently change the fault and the repair
    effect, so this is a Job invoked by reset rather than an initContainer on the
    mysql Deployment.

    mysql must already be scaled to zero, otherwise the ReadWriteOnce volume is
    still attached and the Job cannot mount it.
    """
    kubectl("delete", "job", "mysql-restore", "--ignore-not-found", allow_fail=True)
    for _ in range(24):
        if not kjson("get", "pods", "-l", "job-name=mysql-restore",
                     allow_fail=True).get("items"):
            break
        time.sleep(5)

    job = {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": "mysql-restore"},
        "spec": {
            "backoffLimit": 0,
            "template": {"spec": {
                "restartPolicy": "Never",
                "volumes": [{"name": "work",
                             "persistentVolumeClaim": {"claimName": "mysql-data"}}],
                "containers": [{
                    "name": "restore",
                    "image": SEED_IMAGE,
                    "imagePullPolicy": "IfNotPresent",
                    "volumeMounts": [{"name": "work", "mountPath": "/target"}],
                    # Verify the archive, clear the volume, then extract. Clearing
                    # is what stops state leaking between rounds.
                    "command": ["sh", "-euc", (
                        f"echo '{SEED_SHA256}  {SEED_TAR}' | sha256sum -c - && "
                        "find /target -mindepth 1 -delete && "
                        f"tar -xf {SEED_TAR} -C /target && "
                        "test -d /target/mysql && test -d /target/cities && "
                        "echo RESTORE_OK"
                    )],
                }],
            }},
        },
    }
    r = subprocess.run(["kubectl", "--context", CONTEXT, "apply", "-n", NS, "-f", "-"],
                       input=json.dumps(job), capture_output=True, text=True)
    if r.returncode != 0:
        raise Failure(f"could not create restore job: {r.stderr.strip()[:300]}")

    deadline = time.time() + timeout
    while time.time() < deadline:
        st = kjson("get", "job", "mysql-restore", allow_fail=True).get("status", {})
        if st.get("succeeded"):
            logs = kubectl("logs", "job/mysql-restore", allow_fail=True)
            if "RESTORE_OK" not in logs:
                raise Failure("restore job succeeded but did not report RESTORE_OK")
            kubectl("delete", "job", "mysql-restore", "--ignore-not-found",
                    allow_fail=True)
            return
        if st.get("failed"):
            logs = kubectl("logs", "job/mysql-restore", allow_fail=True)
            raise Failure(f"seed restore failed: {logs.strip()[-400:]}")
        time.sleep(5)
    raise Failure(f"seed restore did not finish within {timeout}s")


def run_db_client(query: str, account: str = "shipping", timeout: int = 90) -> str:
    """Query through the Service from a separate pod; reject command failures."""
    name = "db-client"
    obj = kjson("get", "pod", name, allow_fail=True)
    if not obj:
        # A declared nodeSelector on mysql also places this short-lived client
        # there, avoiding another large MySQL image copy. It remains a separate
        # Pod and still connects via the Service with the application account.
        import yaml
        selectors=[d['spec']['template']['spec'].get('nodeSelector',{})
                   for d in yaml.safe_load_all(MANIFEST.read_text()) if d and
                   d.get('kind')=='Deployment' and d['metadata']['name']=='mysql']
        placement=selectors[0] if selectors else {}
        extra=['--override-type=merge','--overrides='+json.dumps({'spec':{'nodeSelector':placement}})] if placement else []
        kubectl("run", name, f"--image={DB_CLIENT_IMAGE}", "--restart=Never",*extra,
                "--command", "--", "sleep", "infinity")
        kubectl("wait", "--for=condition=Ready", f"pod/{name}", f"--timeout={timeout}s")
    password = {"shipping": "secret", "ratings": "iloveit"}[account]
    return kubectl("exec", name, "--", "env", f"MYSQL_PWD={password}",
                   "mysql", "--connect-timeout=5", "--protocol=TCP", "-h", "mysql",
                   "-P", "3306", f"-u{account}", "--batch", "--skip-column-names",
                   "-e", query).strip()


def wait_mysql_serving(timeout: int = 900) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            cities = run_db_client("SELECT COUNT(*) FROM cities.cities")
            ratings = run_db_client("SELECT COUNT(*) FROM ratings.ratings", "ratings")
            if cities == str(SEED_CITIES_ROWS) and ratings == "0":
                print(f"    mysql serving via Service and application accounts: cities={cities}, ratings={ratings}", flush=True)
                return
            last = f"unexpected seed content: cities={cities}, ratings={ratings}"
        except (Failure, subprocess.TimeoutExpired) as e:
            last = str(e)[:180]
        time.sleep(5)
    raise Failure(f"mysql serving gate failed after {timeout}s: {last}")


def check_db_content(pristine: bool = False) -> list[str]:
    cities = run_db_client("SELECT COUNT(*) FROM cities.cities")
    ratings = run_db_client("SELECT COUNT(*) FROM ratings.ratings", "ratings")
    problems = []
    if cities != str(SEED_CITIES_ROWS):
        problems.append(f"cities={cities}, expected exactly {SEED_CITIES_ROWS}")
    if not ratings.isdigit():
        problems.append(f"invalid ratings count: {ratings!r}")
    elif pristine and ratings != "0":
        problems.append(f"ratings={ratings}, expected 0 before the load starts")
    print(f"  db content: cities={cities}, ratings={ratings}, pristine={pristine}", flush=True)
    return problems


def clear_redis_volume(timeout: int = 180) -> None:
    """Only at round start while Redis is stopped. MySQL uses its own fixed seed."""
    job = {
        "apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "redis-restore"},
        "spec": {"backoffLimit": 0, "activeDeadlineSeconds": timeout,
                 "template": {"spec": {"restartPolicy": "Never",
                    "volumes": [{"name": "work", "persistentVolumeClaim": {"claimName": "data-redis-0"}}],
                    "containers": [{"name": "restore", "image": SEED_IMAGE,
                        "volumeMounts": [{"name": "work", "mountPath": "/target"}],
                        "command": ["sh", "-euc", "find /target -mindepth 1 -delete; echo REDIS_RESTORE_OK"]}]}}}}
    kubectl("delete", "job", "redis-restore", "--ignore-not-found")
    apply_object(job)
    kubectl("wait", "--for=condition=complete", "job/redis-restore", f"--timeout={timeout}s")
    if "REDIS_RESTORE_OK" not in kubectl("logs", "job/redis-restore"):
        raise Failure("Redis restore job did not confirm its work")
    kubectl("delete", "job", "redis-restore", "--wait=true", "--ignore-not-found")


def apply_object(obj: dict) -> None:
    r = subprocess.run(["kubectl", "--context", CONTEXT, "-n", NS, "apply", "-f", "-"],
                       input=json.dumps(obj), capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise Failure(r.stderr.strip()[:300])


def wait_other_data_stores(timeout: int = 240) -> None:
    """Fresh MongoDB/RabbitMQ writable layers and empty Redis at round start."""
    pending = {"mongodb", "redis", "rabbitmq"}
    deadline = time.monotonic() + timeout
    mongo_script = ("var c=db.getSiblingDB('catalogue').products.count(); "
                    "var u=db.getSiblingDB('users').users.count(); "
                    "if(c!==11||u!==3) quit(2); print('MONGO_SEED_OK');")
    while pending and time.monotonic() < deadline:
        for name in list(pending):
            try:
                if name == "mongodb":
                    out = kubectl("exec", "deploy/mongodb", "--", "mongo", "--host", "mongodb", "--quiet", "--eval", mongo_script)
                    if "MONGO_SEED_OK" not in out:
                        continue
                elif name == "redis":
                    if kubectl("exec", "redis-0", "--", "redis-cli", "DBSIZE").strip() != "0":
                        raise Failure("Redis not empty after round reset")
                else:
                    kubectl("exec", "deploy/rabbitmq", "--", "rabbitmqctl", "await_startup", "--timeout", "30")
                pending.remove(name)
            except (Failure, subprocess.TimeoutExpired):
                pass
        if pending:
            time.sleep(5)
    if pending:
        raise Failure(f"data stores did not reach declared seed state: {sorted(pending)}")
    print("    MongoDB seed=11 products/3 users; Redis=0 keys; RabbitMQ fresh pod", flush=True)


def ensure_probe() -> None:
    kubectl("delete", "pod", PROBE, "--grace-period=1", "--ignore-not-found", allow_fail=True)
    # Wait for the old probe to disappear so the readiness census is clean.
    for _ in range(24):
        if not kjson("get", "pods", "-l", f"run={PROBE}", allow_fail=True).get("items"):
            break
        time.sleep(5)
    kubectl("run", PROBE, f"--image={PROBE_IMAGE}", "--restart=Never",
            "--command", "--", "sleep", "infinity")
    r = subprocess.run(["kubectl", "--context", CONTEXT, "-n", NS, "wait", "--for=condition=Ready",
                        f"pod/{PROBE}", "--timeout=240s"], capture_output=True, text=True)
    if r.returncode != 0:
        raise Failure("probe Pod did not become ready, cannot run business checks")


def http_check(path: str) -> tuple[int, str]:
    """Return (status, body). Raises if the request could not be made at all."""
    marker = "===STATUS==="
    script = (f"curl -s --max-time 25 -w '{marker}%{{http_code}}' "
              f"'http://web:8080{path}'")
    r = subprocess.run(["kubectl", "--context", CONTEXT, "-n", NS, "exec", PROBE, "--", "sh", "-c", script],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise Failure(f"probe exec failed for {path}: {r.stderr.strip()[:200]}")
    if marker not in r.stdout:
        raise Failure(f"probe returned no status for {path} "
                      f"(curl produced no result, output={r.stdout[:120]!r})")
    body, _, status = r.stdout.rpartition(marker)
    if not status.strip().isdigit():
        raise Failure(f"probe returned a non-numeric status for {path}: {status!r}")
    return int(status), body


def verify_local_image_ids(pods: list[dict]) -> list[str]:
    import yaml
    lock = yaml.safe_load((ROOT / "sources" / "sources.lock.yaml").read_text())
    expected = {b["ref"]: b["image_id"] for b in lock["local_images"]["builds"].values()}
    problems = []
    checked = 0
    for pod in pods:
        statuses = {c["name"]: c for c in pod.get("status", {}).get("containerStatuses", [])}
        for c in pod["spec"]["containers"]:
            if c["image"] not in expected:
                continue
            actual = statuses.get(c["name"], {}).get("imageID", "").removeprefix("docker://")
            if actual != expected[c["image"]]:
                problems.append(f"image ID mismatch: {pod['metadata']['name']}/{c['name']}")
            checked += 1
    print(f"  local image IDs checked: {checked}", flush=True)
    return problems


def verify(pristine: bool = False) -> None:
    """Confirm the reset. Raises Failure on the first unmet condition."""
    failures: list[str] = []

    # 1. Expected workload set.
    live_wl = {d["metadata"]["name"] for k in ("deployments", "statefulsets")
               for d in kjson("get", k).get("items", [])}
    missing = sorted(set(ALL_WORKLOADS) - live_wl)
    extra = sorted(live_wl - set(ALL_WORKLOADS))
    if missing:
        failures.append(f"workloads absent: {missing}")
    if extra:
        failures.append(f"unexpected workloads: {extra}")
    print(f"  workload set: {len(live_wl)} present, expected {len(ALL_WORKLOADS)}")

    # 2. Readiness and restart budget, excluding the probe.
    pods = [p for p in kjson("get", "pods").get("items", [])
            if p["metadata"]["name"] not in (PROBE, "db-client")]
    not_ready, restarts = [], 0
    for p in pods:
        statuses = p.get("status", {}).get("containerStatuses") or []
        if not statuses:
            not_ready.append(f"{p['metadata']['name']}(no container status)")
        for cs in statuses:
            restarts += cs.get("restartCount", 0)
            if not cs.get("ready"):
                not_ready.append(f"{p['metadata']['name']}/{cs['name']}")
    if not pods:
        failures.append("no Pods found in the namespace")
    if not_ready:
        failures.append(f"containers not ready: {not_ready}")
    if restarts > RESTART_BUDGET:
        failures.append(f"restarts {restarts} exceed budget {RESTART_BUDGET}")
    print(f"  pods: {len(pods)}, not ready: {len(not_ready)}, restarts: {restarts}")

    failures.extend(verify_local_image_ids(pods))

    # 3. Every Service must have at least one ready endpoint.
    bad = []
    slices = kjson("get", "endpointslices").get("items", [])
    per_svc: dict[str, int] = {}
    for e in slices:
        svc = e["metadata"]["labels"].get("kubernetes.io/service-name", "?")
        per_svc[svc] = per_svc.get(svc, 0) + sum(
            1 for ep in e.get("endpoints", []) if ep.get("conditions", {}).get("ready"))
    expected_services = set(declared_fingerprint()["services"])
    for svc in sorted(expected_services):
        n = per_svc.get(svc, 0)
        if n < 1:
            bad.append(svc)
    if not slices:
        failures.append("no EndpointSlices found")
    if bad:
        failures.append(f"Services with no ready endpoint: {bad}")
    print(f"  services with a ready endpoint: "
          f"{sum(1 for n in per_svc.values() if n >= 1)}/{len(per_svc)}")

    # 4. Business checks: status and content.
    ensure_probe()
    checked = 0
    for name, path, want_status, pattern in CHECKS:
        status, body = http_check(path)
        ok_status = status == want_status
        ok_body = re.search(pattern, body, re.S) is not None
        checked += 1
        mark = "ok" if (ok_status and ok_body) else "FAIL"
        print(f"  check {name:<10} status={status} content={'ok' if ok_body else 'NO MATCH'}  {mark}")
        if not ok_status:
            failures.append(f"{name}: status {status}, expected {want_status}")
        if not ok_body:
            failures.append(f"{name}: body did not match /{pattern}/ "
                            f"(first 80 chars: {body[:80]!r})")
    if checked != len(CHECKS):
        failures.append(f"only {checked} of {len(CHECKS)} business checks ran")
    kubectl("delete", "pod", PROBE, "--grace-period=1", "--ignore-not-found", allow_fail=True)

    # 5. Database is back at the declared seed state. A fingerprint over
    #    Kubernetes objects cannot see this: the objects are identical whether or
    #    not the work volume was restored.
    failures.extend(check_db_content(pristine))
    kubectl("delete", "pod", "db-client", "--grace-period=1", "--ignore-not-found")

    # 6. Declared fingerprint against the live cluster.
    if FINGERPRINT.exists():
        recorded = json.loads(FINGERPRINT.read_text(encoding="utf-8"))
        live = live_fingerprint()
        if pristine:
            recorded = json.loads(json.dumps(recorded))
            recorded["workloads"]["load"]["replicas"] = 0
        diffs = diff_fingerprint(recorded, live)
        print(f"  fingerprint differences: {len(diffs)}")
        for d in diffs[:12]:
            print(f"    {d}")
        if diffs:
            failures.append(f"{len(diffs)} fingerprint differences from the "
                            f"recorded baseline")
    else:
        failures.append(f"no recorded fingerprint at {FINGERPRINT.name}; "
                        f"run --write-fingerprint against a confirmed baseline")

    if failures:
        raise Failure("baseline NOT confirmed:\n  - " + "\n  - ".join(failures))
    print("  baseline confirmed")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", default="k3d-lab")
    ap.add_argument("--namespace", default="robot-shop")
    ap.add_argument("--pristine", action="store_true", help="verify the zero-write snapshot before load")
    ap.add_argument("--hard", action="store_true",
                    help="delete the namespace first for a clean snapshot")
    ap.add_argument("--verify", action="store_true", help="confirm only")
    ap.add_argument("--write-fingerprint", action="store_true",
                    help="record the declared fingerprint from the manifest")
    # The app-services tier is gated by shipping, a Spring Boot service whose
    # Hibernate initialisation is very slow under binary translation.
    ap.add_argument("--tier-timeout", type=int, default=1800)
    ap.add_argument("--manifest", default=None,
                    help="baseline manifest to apply, default manifests/baseline.yaml")
    args = ap.parse_args()

    global MANIFEST, FINGERPRINT, CONTEXT, NS
    CONTEXT, NS = args.context, args.namespace
    if args.manifest:
        MANIFEST = Path(args.manifest)
        if not MANIFEST.is_absolute():
            MANIFEST = ROOT / args.manifest
        # Each manifest variant carries its own declared fingerprint.
        FINGERPRINT = MANIFEST.with_suffix(".fingerprint.json")

    try:
        if args.write_fingerprint:
            FINGERPRINT.write_text(json.dumps(declared_fingerprint(), indent=2,
                                              sort_keys=True), encoding="utf-8")
            print(f"wrote {FINGERPRINT.relative_to(ROOT)}")
            return 0

        if args.verify:
            print("confirming baseline")
            verify(pristine=args.pristine)
            return 0

        if args.hard:
            print(f"deleting experiment namespace {NS}", flush=True)
            kubectl("delete", "namespace", NS, "--ignore-not-found", "--timeout=90s", namespaced=False)
        kubectl("create", "namespace", NS, namespaced=False, allow_fail=True)
        print("applying manifest with all workloads at zero replicas", flush=True)
        apply_at_zero()
        wait_old_pods_gone(ALL_WORKLOADS, 300)
        print("restoring fixed MySQL seed to independent work PVC", flush=True)
        restore_mysql_seed()
        # Redis's StatefulSet PVC may not exist until its first pod is scheduled.
        if not kjson("get", "pvc", "data-redis-0", allow_fail=True):
            kubectl("scale", "statefulset", "redis", "--replicas=1")
            wait_tier_ready(["redis"], 240)
            kubectl("scale", "statefulset", "redis", "--replicas=0")
            wait_old_pods_gone(["redis"], 120)
        clear_redis_volume()
        for label, names in TIERS:
            if label == "workload":
                # Workload writes ratings/carts. Check the pristine snapshot BEFORE it starts.
                verify(pristine=True)
            print(f"starting tier: {label}", flush=True)
            for n in names:
                kubectl("scale", workload_kind(n), n, "--replicas=1")
            wait_tier_ready(names, args.tier_timeout)
            if label == "data stores":
                wait_mysql_serving(args.tier_timeout)
                wait_other_data_stores()
            print(f"  tier '{label}' ready", flush=True)

        print("confirming baseline")
        verify()
        return 0

    except (Failure, subprocess.TimeoutExpired) as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
