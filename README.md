# SE-k8s-reuse-playbooks

Kubernetes repair-playbook reuse study. Author: CHEN XU.

## Run the image

Install and start Docker. AMD64 and ARM64 are supported.
Keep at least 6 GiB of free disk space.

```bash
docker run --rm --network none ghcr.io/bdysj/se-k8s-reuse-playbooks:v1.0.0
```

The image recomputes the archived results. It prints the table and PASS.
It includes the evidence. No API key is needed.

To save HTML, JSON and CSV reports on macOS or Linux:

```bash
mkdir -p reproduced
docker run --rm --network none --user "$(id -u):$(id -g)" \
  -v "$PWD/reproduced:/app/outputs" \
  ghcr.io/bdysj/se-k8s-reuse-playbooks:v1.0.0
```

Open `reproduced/tables-*/report.html`.

## Run the source

Install Git and Python 3.12.13. Use macOS, Linux or WSL.
Keep at least 6 GiB of free disk space.

```bash
git clone https://github.com/Bdysj/SE-k8s-reuse-playbooks.git
cd SE-k8s-reuse-playbooks
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
./reproduce.sh result
```

The first run downloads the checked evidence.
Open `outputs/tables-*/report.html`.
Both source and image must match [expected-result.sha256](reproduction/expected-result.sha256).
The full precision numerical checks and 44 offline conformance checks must pass.

## Expected results

| Scope | Executions | Successful | Confirmed failures |
|---|---:|---:|---:|
| RQ1 main | 200 | 125 | 75 |
| RQ3a proposed | 50 | 50 | 0 |
| RQ3a fault matching | 60 | 37 | 23 |
| RQ3a whole environment | 46 | 46 | 0 |
| RQ3b incidents | 45 | 45 | 0 |

RQ1 also has 95 controls. RQ3a has 240 accepted responses, including non-execution responses.
RQ3b has 9 sequences. Archived-result reconstruction makes no new model calls.

## Inspect all logs

```bash
./reproduce.sh restore
./reproduce.sh verify
```

The package retains 48,567 historical files, 95 runtime log/metadata files and 3,767 relative links.
Failed, excluded and retried attempts are included.
[Release downloads](https://github.com/Bdysj/SE-k8s-reuse-playbooks/releases/tag/v1.0.0) contain the evidence and frozen Docker image backups.
[release-assets.json](reproduction/release-assets.json) lists every archive URL and checksum.
Thirteen diagnostic copies have cluster credentials removed. Frozen scientific inputs are unchanged.

## Run a new live repair

Use ARM64 Docker with AMD64 emulation, at least 7 GiB RAM and 20 GiB free disk space.
Install k3d 5.9.0, kubectl 1.34.1 and Helm 4.3.0.

```bash
./reproduce.sh live --cleanup
```

This creates a separate three-node testbed. It restores the original database seed.
It verifies the baseline, injects a routing fault and runs the unchanged P5 repair.
It checks recovery for at least 210 seconds. New logs go under `outputs/live-*/`.
`--cleanup` deletes only this new cluster.
This is a smoke test. It does not repeat the full formal collection.
New experiment timings and new model responses can differ.

## Build your own image

```bash
./reproduce.sh image-assets
docker build -t se-k8s-reproduction .
docker run --rm --network none se-k8s-reproduction
```

See [verification receipts](reproduction/release-verification.json) and the [full reproduction guide](reproduction/GUIDE.md).
Study-owned code uses MIT. See [third-party licenses](THIRD_PARTY_NOTICES.md).
