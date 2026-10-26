# SE-k8s-reuse-playbooks

Reproduction materials for the Kubernetes repair-playbook reuse study.

This is a local release candidate. Local evidence and image archives are verified.
Public uploads are pending.
Fresh-cluster experiments have not yet been validated from this package.

## Install

Use Python 3.12. The tested interpreter is Python 3.12.13.

```bash
git clone https://github.com/Bdysj/SE-k8s-reuse-playbooks.git
cd SE-k8s-reuse-playbooks
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

`requirements.txt` pins the Python packages and their hashes.
The recorded core package versions are preserved.
Transitive packages are locked for this release candidate.

## Restore evidence

The local candidate already contains `results/` and `runtime-logs/`.
For a source-only checkout, restore the checked assets:

```bash
./reproduce.sh restore --from /path/to/release-assets
```

Add `--seed` to restore the database seed too.
Existing different files are never overwritten.
Public download links will be added after upload.
Asset status is recorded in [release-assets.json](reproduction/release-assets.json).

## Rebuild tables

```bash
./reproduce.sh check
./reproduce.sh verify
./reproduce.sh tables
```

These commands do not need Docker or an API key.
`verify` checks every inventoried evidence file and relative link.
`tables` checks frozen inputs and rebuilds the RQ1, RQ2, RQ3a and RQ3b results.
It also runs 44 offline conformance checks.
It blocks network connections, child processes and writes outside its new output directory.

Results are saved under `outputs/tables-*/`.
Read `acceptance.json` for the comparison result.
CSV files contain trial, route and sequence tables.
Earlier output directories are preserved.

## Live environment

```bash
./reproduce.sh check --live
```

Live runs need Docker, k3d, kubectl and Helm.
The recorded testbed used k3d 5.9.0, k3s 1.33.5-k3s1 and Chaos Mesh 2.8.4.
The original host used Apple silicon, 16 GiB RAM and an 8-CPU Docker VM.
Application images use ARM64. MySQL 5.7.44 uses AMD64 emulation.
Image identities are recorded in [images.json](reproduction/images.json).
The local image backup contains 11 application images, one seed image and four infrastructure images.
It is split into two files. Each file has a checksum.
Redis, RabbitMQ, the curl probe and cluster-managed images are separate dependencies.
GHCR publication and fresh deployment checks are pending.

For the optional offline image backup, run this in the assets directory:

```bash
cat frozen-images-20261026-v1.tar.gz.part-01 frozen-images-20261026-v1.tar.gz.part-02 | gzip -dc | docker image load
```

This load command has not yet been tested in a fresh Docker environment.
The seed retains disposable testbed accounts and generated test certificates.
Use it inside the isolated experiment environment.

Historical collection code is retained under `tools/` and `offline_work/`.
New online model experiments need separately configured model access.
Archived-response analysis does not make a new model call.
New timings and new model responses may differ from the archived measurements.

## Evidence and source

The main collection has 295 accepted records. RQ3a has 240 accepted responses.
RQ3b has 45 formal incidents across 9 sequences.
Failed, excluded and retried attempts remain in the evidence.
Historical acceptance files describe their own collection dates.
Thirteen diagnostic publication copies have cluster credentials removed.
Their original and public hashes are listed in [publication-redactions.json](reproduction/publication-redactions.json).
Frozen scientific inputs are unchanged.

Large evidence and the database seed will use GitHub Releases.
Container images will use GHCR. They do not belong in Git history.
See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for source attribution.
Study-owned release code uses the MIT license. Vendored code keeps its upstream license.
