#!/usr/bin/env python3
"""S1 static screening of KubePlaybook candidates (D-DN-3.1 Section 3.5.2).

Screens every playbook in the declared scope (Real_time_fault) at the pinned
commit, in a fixed order of repository identifiers, and reports the outcome and
rejection reason for each one. This implements the fixed screening order so that
candidate choice does not depend on post-hoc judgement.

Static screening only. It cannot establish that a candidate repairs a fault;
that requires the registration qualification trials of Section 3.5.2.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent / "sources" / "kubeplaybook"
SCOPE = "Real_time_fault"

# Ansible task keys that are control constructs rather than modules.
CONTROL_KEYS = {
    "name", "when", "register", "loop", "with_items", "ignore_errors",
    "changed_when", "failed_when", "until", "retries", "delay", "args",
    "environment", "delegate_to", "no_log", "tags", "loop_control",
    "run_once", "check_mode", "async", "poll", "become", "become_user",
    "vars", "notify", "block", "rescue", "always",
}

# Mutating actions mapped to the repair family they serve. A bare
# "patch deployment" is deliberately not a family on its own: the family depends
# on which field path the patch writes.
FAMILY_ACTIONS = {
    "workload recovery": [r"rollout\s+restart", r"delete\s+pod"],
    "replica adjustment": [r"\bscale\b", r"\bautoscale\b"],
    "resource adjustment": [r"set\s+resources", r"/resources/", r'"resources"'],
    "configuration repair": [r"set\s+env", r"/env/", r'"env"', r"patch\s+(cm|configmap)"],
    "Service routing repair": [r"delete\s+(svc|service)", r"\bexpose\b",
                               r"patch\s+(svc|service)"],
}

# Actions that are normally a follow-up step of another repair rather than the
# primary intent. Used only to resolve an ambiguous family mapping.
SECONDARY_ACTIONS = {"workload recovery": [r"rollout\s+restart", r"delete\s+pod"]}
ALL_MUTATING = [
    "scale", "patch", "set resources", "set env", "set image",
    "rollout restart", "rollout undo", "delete", "apply", "create",
    "edit", "label", "annotate", "expose", "autoscale", "replace",
]

# Resources inside the supported scope of Section 3.3.2.
IN_SCOPE_RESOURCES = {
    "deployment", "deployments", "deploy", "pod", "pods", "po",
    "replicaset", "replicasets", "rs", "service", "services", "svc",
    "endpointslice", "endpointslices", "configmap", "configmaps", "cm",
}
# Resources outside it. HPA is conditional (3.3.2) and flagged, not auto-rejected.
OUT_OF_SCOPE_RESOURCES = {"node", "nodes", "namespace", "namespaces", "pv", "pvc", "daemonset", "statefulset"}


def find_playbooks() -> list[Path]:
    """Return playbooks in a fixed order: numeric case, then numeric variant."""
    files = [p for p in (REPO / SCOPE).rglob("*.y*ml") if p.is_file()]

    def key(p: Path) -> tuple:
        rel = p.relative_to(REPO / SCOPE).parts
        nums = [int(x) if x.isdigit() else 0 for x in rel[:-1]]
        while len(nums) < 2:
            nums.append(0)
        return (nums[0], nums[1], p.name)

    return sorted(files, key=key)


def modules_used(doc) -> set[str]:
    found: set[str] = set()

    def walk(tasks):
        for t in tasks or []:
            if not isinstance(t, dict):
                continue
            for blk in ("block", "rescue", "always"):
                if blk in t:
                    walk(t[blk])
            for k in t:
                if k not in CONTROL_KEYS:
                    found.add(k.split(".")[-1])

    if isinstance(doc, list):
        for play in doc:
            if isinstance(play, dict):
                walk(play.get("tasks"))
    return found


def screen(path: Path) -> dict:
    rel = str(path.relative_to(REPO))
    text = path.read_text(encoding="utf-8", errors="ignore")
    row = {
        "id": str(path.parent.relative_to(REPO / SCOPE)),
        "file": rel,
        "verdict": "",
        "reason": "",
        "family": "",
        "flags": [],
    }

    # C4a: must be valid YAML, otherwise ansible cannot run it at all.
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark else ""
        row["verdict"] = "reject"
        row["reason"] = f"C4 invalid YAML{where}: not executable by ansible-playbook"
        return row

    if not isinstance(doc, list):
        row["verdict"] = "reject"
        row["reason"] = "C4 not a playbook (top level is not a list of plays)"
        return row

    mods = modules_used(doc)
    cmds = [" ".join(c.split()) for c in re.findall(r"kubectl\s+([^\n|;&]+)", text)]

    # C4b: no interactive tasks.
    if re.search(r"kubectl\s+edit\b", text):
        row["verdict"] = "reject"
        row["reason"] = "C4 uses 'kubectl edit', which requires an interactive editor"
        return row

    # C1: must perform at least one mutating action.
    verbs = sorted({v for c in cmds for v in ALL_MUTATING
                    if re.match(rf"(-n\s+\S+\s+)?{re.escape(v)}\b", c)})
    if not verbs:
        row["verdict"] = "reject"
        row["reason"] = "C1 read-only: no mutating kubectl verb, so it is not a repair action"
        return row
    row["verbs"] = verbs

    # C1: map to one of the five declared repair families. Match against the
    # whole kubectl command text so that JSON patch field paths are visible.
    blob = " ".join(cmds)
    families = sorted({fam for fam, pats in FAMILY_ACTIONS.items()
                       if any(re.search(p, blob) for p in pats)})

    # If several families match, drop a family that is only present as a
    # follow-up step (for example a restart after a configuration change).
    if len(families) > 1:
        for fam, pats in SECONDARY_ACTIONS.items():
            if fam in families and any(re.search(p, blob) for p in pats):
                others = [f for f in families if f != fam]
                if others:
                    families = others
                    row["flags"].append(
                        f"'{fam}' action present as a follow-up step, not the primary intent")
    if len(families) > 1:
        row["flags"].append(
            f"ambiguous family mapping {families}: a primary repair family must be "
            "declared before registration, and the additional action registered as "
            "part of the frozen candidate")

    row["family"] = ", ".join(families)

    # C3: resource scope.
    out_of_scope = set()
    for c in cmds:
        for tok in c.split():
            base = tok.split("/")[0].lower()
            if base in OUT_OF_SCOPE_RESOURCES:
                out_of_scope.add(base)
    if re.search(r"kubectl\s+(autoscale|.*\bhpa\b)", text):
        row["flags"].append("creates/reads HPA: conditional admission under 3.3.2")
    if out_of_scope:
        row["verdict"] = "reject"
        row["reason"] = f"C3 touches resources outside the supported scope: {', '.join(sorted(out_of_scope))}"
        return row

    # Flags that do not reject but must become recorded annotations (3.3.2).
    if any(re.search(r"\$\(\(|\$\[", c) for c in cmds):
        row["flags"].append("arithmetic expansion: annotate the written value")
    if any("|" in c for c in re.findall(r"kubectl\s+([^\n;&]+)", text)):
        row["flags"].append("shell pipeline discovery: annotate target resolution")
    if re.search(r"stdout_lines\[\d+\]", text):
        row["flags"].append("positional selection from command output: annotate resolved target")
    if re.search(r"/containers/\d+/", text):
        row["flags"].append("positional container path: annotate that index maps to the intended container")
    if "ignore_errors" in mods or "ignore_errors: yes" in text or "ignore_errors: true" in text:
        row["flags"].append("ignore_errors present: declare task criticality per 3.6")
    if not families:
        row["verdict"] = "reject"
        row["reason"] = f"C1 mutating verbs {verbs} do not map to a declared repair family"
        return row

    row["verdict"] = "pass-S1"
    row["reason"] = "passes static screening; requires registration qualification trials"
    return row


def main() -> int:
    if not REPO.exists():
        print(f"missing pinned source: {REPO}", file=sys.stderr)
        return 1

    rows = [screen(p) for p in find_playbooks()]

    out_dir = Path(__file__).resolve().parent.parent / "results"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "s1_screening.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")

    passed = [r for r in rows if r["verdict"] == "pass-S1"]
    print(f"screened {len(rows)} playbooks in {SCOPE}: "
          f"{len(passed)} pass S1, {len(rows) - len(passed)} rejected\n")

    for r in rows:
        mark = "PASS" if r["verdict"] == "pass-S1" else "rej "
        print(f"[{mark}] {r['id']:<6} {r.get('family') or '-':<24} {r['reason']}")
        for f in r["flags"]:
            print(f"         flag: {f}")

    # Assign each S1 pass to exactly one family using the pre-declared primary
    # family, so that an ambiguous action set does not decide the sample.
    decl_path = Path(__file__).resolve().parent / "primary_family.yaml"
    decl = yaml.safe_load(decl_path.read_text(encoding="utf-8"))["candidates"]

    missing = [r["id"] for r in passed if r["id"] not in decl]
    if missing:
        print(f"\nWARNING: no declared primary family for {missing}", file=sys.stderr)

    order: dict[str, list[str]] = {}
    for r in passed:
        d = decl.get(r["id"])
        if not d:
            continue
        r["primary_family"] = d["primary"]
        order.setdefault(d["primary"], []).append(r["id"])

    (out_dir / "s1_screening.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")

    print("\nS1 passes by declared primary family, in fixed screening order")
    print("(take the first entry to S2; move to the next only if it fails there):")
    for fam in FAMILY_ACTIONS:
        ids = order.get(fam)
        print(f"  {fam:<24} {' -> '.join(ids) if ids else 'NONE — needs an adapted, generated or manual candidate'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
