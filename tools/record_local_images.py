#!/usr/bin/env python3
"""Record the locally built images into sources.lock.yaml.

Step 1 of the agreed sequence replaces the published amd64 images with locally
built ones. Section 3.5.1 requires the testbed to be pinned to recorded image
identifiers, so the provenance changes from "published digest" to "locally built
from a recorded Dockerfile at a recorded source commit, with the resulting image
ID". These identifiers freeze the observed images; byte-identical future rebuilds have not been demonstrated.

Writes the local_images block by querying docker, so the identifiers are never
transcribed by hand.
"""

from __future__ import annotations

import json
from datetime import date
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "sources" / "sources.lock.yaml"

# service -> (image ref, Dockerfile used, why it deviates)
BUILDS = {
    "rs-cart":      ("ddn-eval/rs-cart:arm64",      "sources/robot-shop/cart/Dockerfile", "unmodified"),
    "rs-catalogue": ("ddn-eval/rs-catalogue:arm64", "sources/robot-shop/catalogue/Dockerfile", "unmodified"),
    "rs-user":      ("ddn-eval/rs-user:arm64",      "sources/robot-shop/user/Dockerfile", "unmodified"),
    "rs-payment":   ("ddn-eval/rs-payment:arm64",   "sources/robot-shop/payment/Dockerfile", "unmodified"),
    "rs-web":       ("ddn-eval/rs-web:arm64",       "sources/robot-shop/web/Dockerfile", "unmodified"),
    "rs-mongodb":   ("ddn-eval/rs-mongodb:arm64",   "sources/robot-shop/mongo/Dockerfile", "unmodified"),
    "rs-load":      ("ddn-eval/rs-load:arm64-grouped", "arch-eval/load.Dockerfile", "Preserve HTTP workload; group dynamic statistics and classify upstream intentional errors; reuse Locust 2.34.0"),
    "rs-shipping":  ("ddn-eval/rs-shipping:arm64",  "arch-eval/shipping.Dockerfile",
                     "runtime base openjdk:8-jdk replaced with eclipse-temurin:8-jdk; "
                     "openjdk:8-jdk no longer exists on Docker Hub"),
    "rs-ratings":   ("ddn-eval/rs-ratings:arm64",   "arch-eval/ratings.Dockerfile",
                     "unzip dropped because bullseye security packages moved to "
                     "archive.debian.org and the pinned version 404s on both "
                     "architectures; composer stage pinned to composer:2.7; "
                     "Instana AutoProfile ini dropped"),
    "rs-dispatch":  ("ddn-eval/rs-dispatch:arm64",  "arch-eval/dispatch.Dockerfile",
                     "go-sensor pinned to v1.52.0, the last release before the "
                     "pinned Robot Shop commit; the repository commits no go.mod "
                     "so the published build floated on latest and can no longer "
                     "resolve under Go 1.17"),
    "rs-mysql-db":  ("ddn-eval/rs-mysql-db:amd64",  "sources/robot-shop/mysql/Dockerfile",
                     "kept on amd64 under emulation; MySQL 5.7 publishes no arm64 "
                     "image and it is not a measured hotspot"),
}


def inspect(ref: str) -> dict:
    r = subprocess.run(["docker", "inspect", ref], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"image not found: {ref}")
    d = json.loads(r.stdout)[0]
    return {"id": d["Id"], "architecture": d["Architecture"],
            "size_bytes": d["Size"]}


def main() -> int:
    block = {}
    for name, (ref, dockerfile, note) in BUILDS.items():
        info = inspect(ref)
        block[name] = {
            "ref": ref,
            "image_id": info["id"],
            "architecture": info["architecture"],
            "size_bytes": info["size_bytes"],
            "built_from": dockerfile,
            "deviation": note,
        }

    text = LOCK.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    previous = doc.get("local_images", {})
    for name, entry in block.items():
        prior = previous.get("builds", {}).get(name, {})
        if prior.get("image_id") and prior["image_id"] != entry["image_id"]:
            entry["previous_recorded_id"] = prior["image_id"]
        for key in ("previous_ref", "previous_image_id", "previous_recorded_id"):
            if key in prior:
                entry.setdefault(key, prior[key])
    doc["local_images"] = {
        "purpose": (
            "Locally built replacements for the published amd64 images. Provenance "
            "changes from a published registry digest to a local build, so the "
            "provenance records the source and Dockerfile and the exact observed image ID. "
            "Runtime verification rejects different IDs; byte-identical future builds are not established. Published digests remain under "
            "'images' for comparison."
        ),
        "recorded": str(date.today()),
        "third_party_unchanged": {
            "redis": "redis:4.0.6, multi-arch upstream, pulled natively per node",
            "rabbitmq": ("rabbitmq:3.7-management-alpine, multi-arch upstream, "
                         "pulled natively per node"),
        },
        "note": (
            "The active resource profile is recorded in baseline_overrides. This is a "
            "pilot testbed, not a controlled architecture benchmark; original chart "
            "defaults have not been restored wholesale."
        ),
        "builds": block,
    }
    for key in ("identity_reconciliation",):
        if key in previous:
            doc["local_images"][key] = previous[key]
    LOCK.write_text(yaml.safe_dump(doc, sort_keys=False, width=100,
                                   allow_unicode=True), encoding="utf-8")
    print(f"recorded {len(block)} local images into {LOCK.relative_to(ROOT)}")
    for n, b in block.items():
        print(f"  {n:<14} {b['architecture']:<6} {b['image_id'][7:19]}  {b['built_from']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
