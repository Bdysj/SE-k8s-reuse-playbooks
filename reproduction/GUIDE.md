# SE-k8s-reuse-playbooks

Reproduction package for the Kubernetes repair-playbook reuse study.
Author: CHEN XU.

## Run the published image

Install Docker. Then run:

```bash
docker run --rm --network none ghcr.io/bdysj/se-k8s-reuse-playbooks:v1.0.0
```

The image includes the evidence. It recomputes the results at startup.
It prints the result table and a PASS message.
It needs no API key. It supports Linux AMD64 and ARM64.
Keep 6 GiB of free disk space for the image and reports.

To save HTML, JSON and CSV files on macOS or Linux:

```bash
mkdir -p reproduced
docker run --rm --network none --user "$(id -u):$(id -g)" \
  -v "$PWD/reproduced:/app/outputs" \
  ghcr.io/bdysj/se-k8s-reuse-playbooks:v1.0.0
```

Open `reproduced/tables-*/report.html`.
The image digest is recorded in [release-verification.json](release-verification.json).

## Run the source

Install Git and Python 3.12.13. Keep 6 GiB of free disk space.

```bash
git clone https://github.com/Bdysj/SE-k8s-reuse-playbooks.git
cd SE-k8s-reuse-playbooks
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
./reproduce.sh result
```

The first run downloads the checked evidence from GitHub Releases.
It then runs the same analysis as the image.
Open `outputs/tables-*/report.html`.
`requirements.txt` locks all Python packages and their hashes.

Both paths must print this scientific result SHA-256:

```text
c11eddbf468ab37563adb46bd64ae16960d083ca8bc552f830770b197d229115
```

The digest excludes the new RQ3b generation timestamp.
It rounds floats to 8 decimal places for the cross-platform check.
The analysis also compares full precision values with the archived tables.
Its tolerance is `rel_tol=1e-12, abs_tol=1e-9`.
Full precision output files are retained.

## Build the image from source

After installing the requirements:

```bash
./reproduce.sh image-assets
docker build -t se-k8s-reproduction .
docker run --rm --network none se-k8s-reproduction
```

The Python base image is pinned by digest.
The build checks all evidence files and links.
The run recomputes the tables again.

## Expected results

| Scope | Executions | Successful | Confirmed failures |
|---|---:|---:|---:|
| RQ1 main | 200 | 125 | 75 |
| RQ3a proposed | 50 | 50 | 0 |
| RQ3a fault matching | 60 | 37 | 23 |
| RQ3a whole environment | 46 | 46 | 0 |
| RQ3b incidents | 45 | 45 | 0 |

RQ1 also contains 95 accepted controls.
RQ3a contains 240 accepted responses. Some responses do not execute a live repair.
RQ3b contains 9 sequences and 23 archived model calls.
The result command makes no new model call. It runs 44 offline conformance checks.
This command recomputes archived experiments. It does not collect new cluster experiments.

## Inspect all archived logs

```bash
./reproduce.sh restore
./reproduce.sh verify
```

The archive has 48,567 historical evidence and supplemental files.
It also has 95 runtime log and metadata files, plus 3,767 relative links.
Files are restored under `results/`, `archive-supplemental/` and `runtime-logs/`.
Failed, excluded and retried attempts are retained.
See [release-assets.json](release-assets.json) for sizes, URLs and SHA-256 values.
Use `restore --from /path/to/assets` for local archives.
Add `--seed` to restore the separate database seed tar file.

Thirteen diagnostic publication copies have cluster credentials removed.
Their original and public hashes are in [publication-redactions.json](publication-redactions.json).
Frozen scientific inputs are unchanged.

## Run a new live repair experiment

Use ARM64 Docker with AMD64 emulation. Allocate at least 7 GiB RAM.
Keep at least 20 GiB of free disk space on a new machine.
Install k3d 5.9.0, kubectl 1.34.1 and Helm 4.3.0.
Then run:

```bash
./reproduce.sh live --cleanup
```

This command downloads the frozen application image backup if needed.
It checks all application and seed image IDs.
It creates a separate one-server, two-agent k3s 1.33.5 testbed.
It restores the original MySQL seed and checks its 948,833 city rows.
This is Robot Shop shipping lookup data. The row count checks seed integrity.
It checks all 13 workloads and the business responses.
It injects a web Service routing fault.
It runs the unchanged P5 playbook and observes recovery for at least 210 seconds.
It saves new logs under `outputs/live-*/`.
`--cleanup` deletes only the cluster created by that command.
Omit it to keep the new testbed. Use a new `--cluster ddn-repro-NAME` for another run.

This is a deployment and repair smoke test.
It does not repeat every formal experiment.
The full historical protocols and collection code remain in `results/`, `tools/` and `offline_work/`.
The historical fault-injection experiments also use Chaos Mesh 2.8.4.
Its chart and values are in `tools/vendor/` and `tools/chaos-values.yaml`.
New online model trials require separately configured model access.
New timings and new model responses may differ.
Use the archived evidence command above to reproduce the published numerical results exactly.

## Release materials

[Download all evidence, image backups and reports](https://github.com/Bdysj/SE-k8s-reuse-playbooks/releases/tag/v1.0.0).
Large files use GitHub Releases. The runnable result image uses GHCR.
The split Docker backup contains 11 application images, the seed carrier and four infrastructure images.
The live command imports it automatically when needed.
Redis, RabbitMQ and the curl probe use the immutable digests in the frozen manifest and reset code.
The seed contains disposable test accounts and generated test certificates.
Use it in the separate experiment environment.

Study-owned release code uses the MIT license.
Vendored code keeps its upstream license.
See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
