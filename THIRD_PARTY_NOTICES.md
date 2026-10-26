# Third-party source notices

| Source | Frozen commit | License | Included material |
|---|---|---|---|
| [Robot Shop](https://github.com/instana/robot-shop) | `55292e2199f2fb00a165b1f7d3045fe7f8922038` | Apache-2.0 | Application source and build context under `sources/robot-shop/` |
| [KubePlaybook](https://github.com/K8sPlayBook/KubePlaybook) | `57136d02afa68c2ee5e6df4dc5479d455b98047d` | MIT | Candidate source under `sources/kubeplaybook/` |
| [MicroRemed](https://github.com/LLM4AIOps/MicroRemed) | `f579d32279188ea9e5843877d3311d8d7b4ea2c3` | No declared license in the recorded checkout | Commit reference only. Upstream source is excluded. |

Upstream license texts remain beside the included source.
Preserve their copyright and attribution notices.

`tools/inject_fault.py` is the study-owned fault adapter.
Its header records the upstream fault specification and the independent implementation.
Architecture and runtime adaptations are recorded in `sources/sources.lock.yaml` and `arch-eval/`.
Frozen experiment sources are retained unchanged for identity checks.

The root MIT license applies to study-owned code and release tooling.
It does not replace third-party licenses or grant rights to excluded source.
Redis, RabbitMQ, MySQL, k3s and other container dependencies retain their own licenses.
Container contents and notices need review before image publication.
