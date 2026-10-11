# Symphony

Private AI for small businesses. A Jetson box in the back office runs **Soloist**, a small local model that answers quick and private jobs without anything leaving the building. Bigger jobs, documents and the web go to **Orchestra**, an agent running in the business's own dedicated AWS account. Orchestra uses models that keep nothing (zero data retention) and are never trained on the business's data.

Built for Palmetto Hacks, Fall 2026.

```
People ──► Symphony app (on the Jetson) ──► OpenCode ──► Soloist (local model) or Orchestra (private cloud)
             sign-in: Cognito + SSO           routing: the on-device router decides per job
```

## What's here

| Folder | What it is | Start with |
| --- | --- | --- |
| [`sebastian/`](sebastian/) | The Symphony app (Next.js) on the Jetson: Today, Work, Team, Privacy and spending, and a Coding agent tab. Every job is an OpenCode session that runs on Soloist or Orchestra. | [`sebastian/README.md`](sebastian/README.md) |
| [`everything/`](everything/) | The on-prem edge router: Qwen3.5-2B with a trained difficulty head chooses local or private cloud for each prompt, plus local model runtimes and Jetson tests. | [`everything/README.md`](everything/README.md) |
| [`router_heads/`](router_heads/) | Training and evaluation for the difficulty head (PyTorch and ONNX exports, evaluation report). | [`router_heads/out/eval_report.md`](router_heads/out/eval_report.md) |
| [`cloud/`](cloud/) | The AWS platform: a Control Tower landing zone, one account per business, the Orchestra agent (AgentCore Harness on Bedrock with document search, Code Interpreter, Browser and per-user memory), and the Jetson endpoint (mTLS + Cognito, streaming). All Terraform, tested offline. | [`cloud/PLAN.md`](cloud/PLAN.md), [`cloud/demo/README.md`](cloud/demo/README.md) |
| [`cloud/schemas/`](cloud/schemas/) | The API contract between the Jetson and the cloud. | [`cloud/schemas/README.md`](cloud/schemas/README.md) |
| [`docs/superpowers/`](docs/superpowers/) | Design specs and implementation plans for each phase. | [`specs/`](docs/superpowers/specs/) |

## Run the demo

The live demo runs against the deployed `demo` tenant at `https://api.demo.symphony-edge.link`. The step-by-step script and expected output are in [`cloud/demo/README.md`](cloud/demo/README.md).

## Checks

| Part | Command | CI workflow |
| --- | --- | --- |
| Cloud Terraform (offline, no AWS credentials) | `cloud/terraform/scripts/check.sh` | `terraform-offline` |
| Cloud services (Node and Python) | `npm test` in `cloud/services/ingress`; `python -m unittest discover -s tests` in `cloud/services/agent` | `services-offline` |
| App | `npm run typecheck && npm run lint && npm run check:logic && npm run check:auth && npm run build` in `sebastian/` | `app` |
| Edge router | `PYTHONPATH=. pytest -m "not integration"` in `everything/` (extras `dev,strands,signatures`) | `edge-router` |

## Security model, in one paragraph

Each business gets its own AWS account under a Symphony-managed AWS Organization, with KMS-encrypted data, zero data retention on Bedrock enforced by an SCP, and audit logs that record who did what but never the prompts or answers. The Jetson proves its identity with a per-device certificate (mTLS), and people sign in through Cognito (SSO where the business has it). The data-tier policy is enforced twice, on the device and in the cloud: anything marked `local-only` never leaves the box.
