#!/usr/bin/env python3
"""Study-owned adapter for the pinned MicroRemed fault specifications.

Provenance: sources/microremed at f579d32279188ea9e5843877d3311d8d7b4ea2c3.
No MicroRemed source file is copied into this adapter. Resource values/action and
PodChaos duration follow that specification; label binding, named container,
kubeconfig/context, and victim identity are explicit for Robot Shop/k3d.
"""
import argparse
import json
import subprocess


def inject(kind, workload, container, namespace="robot-shop", context="k3d-lab", kubeconfig=None, resource_values=None):
    prefix = ["kubectl", "--context", context, "-n", namespace]
    if kubeconfig:
        prefix += ["--kubeconfig", kubeconfig]

    def run(*args, payload=None):
        p = subprocess.run(prefix + list(args), input=payload, capture_output=True, text=True, timeout=120)
        if p.returncode:
            raise RuntimeError(p.stderr.strip())
        return p.stdout

    deployment = json.loads(run("get", "deployment", workload, "-o", "json"))
    if not any(c["name"] == container for c in deployment["spec"]["template"]["spec"]["containers"]):
        raise ValueError(f"container {container} not present in Deployment {workload}")
    # Bind through API ownership, not a label that another workload can share.
    rs_items=json.loads(run('get','replicasets','-o','json'))['items']
    owned_rs={r['metadata']['uid'] for r in rs_items if any(
        o.get('kind')=='Deployment' and o.get('uid')==deployment['metadata']['uid']
        and o.get('controller') for o in r['metadata'].get('ownerReferences',[]))}
    victims = [p for p in json.loads(run("get", "pods", "-o", "json"))["items"]
               if any(o.get('kind')=='ReplicaSet' and o.get('uid') in owned_rs
                      and o.get('controller') for o in p['metadata'].get('ownerReferences',[]))]
    if not victims:
        raise ValueError(f"no victim Pod owned by Deployment {workload}")
    victim = next((p for p in sorted(victims,key=lambda p:p['metadata']['name'])
                   if not p["metadata"].get("deletionTimestamp")), None)
    if victim is None:
        raise ValueError("all matching Pods are terminating")
    if kind == "pod-config-error":
        resources = resource_values or {"requests": {"cpu": "1m", "memory": "100Mi"}, "limits": {"cpu": "2m", "memory": "200Mi"}}
        patch = {"spec": {"template": {"spec": {"containers": [{"name": container,
                 "resources": resources}]}}}}
        run("patch", "deployment", workload, "--type=strategic", "-p", json.dumps(patch))
    elif kind == "pod-failure":
        run("annotate", "namespace", namespace, "chaos-mesh.org/inject=enabled", "--overwrite")
        obj = {"apiVersion": "chaos-mesh.org/v1alpha1", "kind": "PodChaos",
               "metadata": {"name": "pilot-pod-fail", "namespace": namespace},
               "spec": {"selector": {"namespaces": [namespace],
                                     "pods": {namespace: [victim["metadata"]["name"]]}},
                        "mode": "one", "duration": "1200s", "action": "pod-failure", "gracePeriod": 0}}
        run("apply", "-f", "-", payload=json.dumps(obj))
    else:
        raise ValueError(kind)
    return {"kind": kind, "workload": workload, "container": container, "namespace": namespace,
            "context": context, "resource_values": resource_values, "victim_name": victim["metadata"]["name"],
            "deployment_uid": deployment['metadata']['uid'], "owner_binding": "Pod -> ReplicaSet -> Deployment UID",
            "victim_uid": victim["metadata"]["uid"], "duration_seconds":1200 if kind=="pod-failure" else None}


if __name__ == "__main__":
    p=argparse.ArgumentParser();p.add_argument("kind",choices=["pod-config-error","pod-failure"])
    p.add_argument("--workload",required=True);p.add_argument("--container",required=True)
    p.add_argument("--namespace",default="robot-shop");p.add_argument("--context",default="k3d-lab")
    p.add_argument("--kubeconfig")
    a=p.parse_args();print(json.dumps(inject(a.kind,a.workload,a.container,a.namespace,a.context,a.kubeconfig),indent=2))
