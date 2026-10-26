#!/usr/bin/env python3
"""Render the pinned Robot Shop baseline manifest.

Section 3.5.1 requires the testbed to be pinned to a recorded source commit,
Helm chart revision and container image digests. Section 3.5.3 requires each
trial to start from a declared snapshot, and a repair that deletes and recreates
a Service drops Helm ownership metadata, so reset must apply a pre-rendered
manifest rather than run helm upgrade.

Output: manifests/baseline.yaml, with every image pinned to a digest.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "sources" / "robot-shop" / "K8s" / "helm"
LOAD_MANIFEST = ROOT / "sources" / "robot-shop" / "K8s" / "load-deployment.yaml"
LOCK = ROOT / "sources" / "sources.lock.yaml"
OUT = ROOT / "manifests" / "baseline.yaml"
OUT_LOCAL = ROOT / "manifests" / "baseline.arm64.yaml"
NAMESPACE = "robot-shop"


def digest_map(local: bool = False) -> dict[str, str]:
    """Map chart image references to pinned ones.

    With local=False the published amd64 digests are used. With local=True the
    locally built images recorded under local_images are used instead, which is
    what step 1 of the native-architecture sequence requires.
    """
    lock = yaml.safe_load(LOCK.read_text(encoding="utf-8"))
    imgs = lock["images"]
    tag = imgs["robotshop"]["tag_at_resolution"]

    if local:
        builds = lock["local_images"]["builds"]
        out = {}
        for name, b in builds.items():
            # Chart writes robotshop/<name>:<version>; redirect to the local ref.
            out[f"robotshop/{name}:{tag}"] = b["ref"]
            out[f"robotshop/{name}:latest"] = b["ref"]
        # redis and rabbitmq stay upstream and are multi-arch, so the node pulls
        # the native variant. Keep them on their recorded digests.
        for entry in imgs["infrastructure"].values():
            ref = entry["ref"]
            out[ref] = f"{ref.split(':')[0]}@{entry['digest']}"
        return out

    out = {f"robotshop/{n}:{tag}": f"robotshop/{n}@{d}"
           for n, d in imgs["robotshop"].items() if n != "tag_at_resolution"}
    for entry in imgs["infrastructure"].values():
        ref = entry["ref"]
        out[ref] = f"{ref.split(':')[0]}@{entry['digest']}"
    # The load generator manifest ships a :latest tag.
    out["robotshop/rs-load:latest"] = out[f"robotshop/rs-load:{tag}"]
    return out


def add_mysql_work_volume(docs: list[dict]) -> list[dict]:
    """Give mysql an independent work volume, restored from the seed each round.

    The chart gives mysql no volume at all: the Dockerfile's VOLUME /data line is a
    Docker directive that Kubernetes ignores, and the effective datadir is
    /var/lib/mysql rather than /data, so the datadir lived in the container
    writable layer and every pod start re-imported the dump.

    A work volume alone would be worse than none, because state would then persist
    across trials and contaminate them. It is therefore paired with the restore
    step in reset_baseline.py, which rewrites the volume from the fixed read-only
    seed at the start of each round and never during a round.

    The update strategy is forced to Recreate. With the default RollingUpdate the
    replacement pod would be scheduled before the old one terminated and both would
    claim the same ReadWriteOnce volume, which deadlocks.
    """
    pvc = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {"name": "mysql-data", "labels": {"service": "mysql"}},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "storageClassName": "local-path",
            "resources": {"requests": {"storage": "4Gi"}},
        },
    }
    out = []
    for d in docs:
        if d.get("kind") == "Deployment" and d["metadata"]["name"] == "mysql":
            spec = d["spec"]
            spec["strategy"] = {"type": "Recreate"}
            tspec = spec["template"]["spec"]
            tspec.setdefault("volumes", []).append(
                {"name": "mysql-data",
                 "persistentVolumeClaim": {"claimName": "mysql-data"}})
            for c in tspec["containers"]:
                if c["name"] == "mysql":
                    c.setdefault("volumeMounts", []).append(
                        {"name": "mysql-data", "mountPath": "/var/lib/mysql"})
        out.append(d)
    out.insert(0, pvc)
    print("added mysql work volume (PVC mysql-data at /var/lib/mysql, strategy Recreate)")
    return out


def apply_memory_profile(docs: list[dict]) -> list[dict]:
    """Apply the declared baseline memory profile from sources.lock.yaml."""
    prof = yaml.safe_load(LOCK.read_text(encoding="utf-8"))["baseline_overrides"]["memory_profile"]
    # name -> (requests_memory, limits_memory)
    plan = {n: (prof["new_values"]["requests_memory"], prof["new_values"]["limits_memory"])
            for n in prof["applied_to"]}
    cpu_limits: dict[str, str] = {}
    for name, spec in (prof.get("additional") or {}).items():
        plan[name] = (spec["requests_memory"], spec["limits_memory"])
        if spec.get("limits_cpu"):
            cpu_limits[name] = spec["limits_cpu"]

    # The declared workload level also carries its own resource overrides.
    work = yaml.safe_load(LOCK.read_text(encoding="utf-8"))["baseline_overrides"]["workload"]
    for name, spec in (work.get("also_applied") or {}).items():
        plan[name] = (spec["requests_memory"], spec["limits_memory"])
    num_clients = str(work["setting"].split("=")[-1].strip())

    applied = []
    for d in docs:
        if d.get("kind") not in ("Deployment", "StatefulSet"):
            continue
        name = d["metadata"]["name"]
        entry = plan.get(name)
        if not entry:
            continue
        req, lim = entry
        for c in d["spec"]["template"]["spec"]["containers"]:
            r = c.setdefault("resources", {})
            r.setdefault("requests", {})["memory"] = req
            r.setdefault("limits", {})["memory"] = lim
            if name in cpu_limits:
                r["limits"]["cpu"] = cpu_limits[name]
            if name == "load":
                for e in c.get("env", []):
                    if e.get("name") == "NUM_CLIENTS":
                        e["value"] = num_clients
            applied.append(f"{name}/{c['name']}")
    print(f"memory profile applied to {len(applied)} containers")
    return docs


def pin_images(text: str, mapping: dict[str, str]) -> tuple[str, list[str]]:
    unpinned: list[str] = []

    def repl(m: re.Match) -> str:
        ref = m.group(2)
        if ref in mapping:
            return f"{m.group(1)}{mapping[ref]}"
        if "@sha256:" in ref:
            return m.group(0)
        unpinned.append(ref)
        return m.group(0)

    return re.sub(r"(image:\s*)(\S+)", repl, text), unpinned


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local-images", action="store_true",
                    help="use the locally built images recorded under local_images")
    args = ap.parse_args()
    out_path = OUT_LOCAL if args.local_images else OUT

    if not CHART.exists():
        print(f"missing pinned chart: {CHART}", file=sys.stderr)
        return 1

    mapping = digest_map(local=args.local_images)

    # Chart defaults keep the web Service as LoadBalancer (nodeport: false).
    # k3d provides a built-in LoadBalancer, and keeping the default preserves the
    # Service contract that the Service-routing candidate is compared against.
    rendered = subprocess.run(
        ["helm", "template", "robot-shop", str(CHART),
         "--namespace", NAMESPACE,
         "--set", "image.version=2.1.0",
         # The chart defaults to the storage class name "standard"; k3s provides
         # "local-path". Recorded as a declared baseline setting, not a repair.
         "--set", "redis.storageClassName=local-path"],
        capture_output=True, text=True, check=True,
    ).stdout

    load = LOAD_MANIFEST.read_text(encoding="utf-8")

    combined = (
        "# Pinned Robot Shop baseline. Generated by tools/render_baseline.py.\n"
        "# Do not edit by hand. Regenerate after changing sources.lock.yaml.\n"
        f"# chart: {CHART.relative_to(ROOT)} (robot-shop 1.1.0)\n"
        "# All images pinned to digests from sources.lock.yaml.\n"
        "---\n" + rendered + "\n---\n" + load
    )
    combined, unpinned = pin_images(combined, mapping)

    docs = [d for d in yaml.safe_load_all(combined) if d]
    docs = apply_memory_profile(docs)
    docs = add_mysql_work_volume(docs)
    combined = (
        "# Pinned Robot Shop baseline. Generated by tools/render_baseline.py.\n"
        "# Do not edit by hand. Regenerate after changing sources.lock.yaml.\n"
        f"# chart: {CHART.relative_to(ROOT)} (robot-shop 1.1.0)\n"
        "# Images pinned to digests and baseline_overrides applied from\n"
        "# sources.lock.yaml.\n"
        + "".join("---\n" + yaml.safe_dump(d, sort_keys=False, width=4096) for d in docs)
    )
    kinds: dict[str, int] = {}
    for d in docs:
        kinds[d.get("kind", "?")] = kinds.get(d.get("kind", "?"), 0) + 1

    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(combined, encoding="utf-8")

    print(f"wrote {out_path.relative_to(ROOT)}: {len(docs)} objects")
    for k, n in sorted(kinds.items()):
        print(f"  {k}: {n}")
    pinned = len(re.findall(r"image:\s*\S+@sha256:", combined))
    print(f"images pinned to digest: {pinned}")
    if unpinned:
        print(f"WARNING unpinned images: {sorted(set(unpinned))}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
