# ADR-0012: CI/CD on ephemeral Kubernetes, a self-destructing cloud drill, signed images and a static public demo (Phase 10)

- **Status:** Accepted
- **Date:** 2026-11-10

## Context

Phase 10 was "cloud and CI/CD". The obvious version, Centinela running permanently in a public cloud, does not fit the project's constraints:

- **Budget $0.** The full stack (k3s, Prometheus, Loki, Grafana, two PostgreSQL databases, the agent) needs about 8 GB of RAM: roughly $30–60 a month on AWS, and a GPU for the model costs over $300. The only AWS credit is shared with another project.
- **Safety.** A public deployment means a public approval console. Even with the controls of ADR-0008, the safest console is one the internet cannot reach.

What a cloud deployment proves to a reviewer is that the system **deploys from scratch, reproducibly, and works**. That can be proven on every pull request for free, and in the real cloud for cents, without anything staying on.

## Decision

### 1. End-to-end test on a fresh Kubernetes cluster, on every PR (`e2e.yml`, `scripts/e2e.sh`)

A GitHub-hosted runner (free for public repositories) creates a k3d cluster, builds both images, applies Terraform (namespaces, quotas, RBAC, secrets) and the Kustomize manifests: the same `./scripts/k8s.sh up` used on the laptop. Then the test closes the whole loop:

1. scale payments to 0;
2. an alert fires and an incident is opened;
3. the agent stores a diagnosis **with its evidence**;
4. a fix is pending: proposed by the agent or, if the small CI model does not propose it, through the **MCP server** (so that path is tested too);
5. a "human" approves with the CLI;
6. the executor scales payments back and verifies it; the incident resolves.

The runner has no GPU, so the agent uses `qwen3:1.7b` on CPU through Ollama on the runner. The e2e test checks the plumbing; how *good* the diagnosis is, is measured offline with the production model (`centinela eval run`, ADR-0011). Timings and the model's answer go to the job summary. On failure the job prints pod states, events and the logs of the collector, agent, executor and Ollama.

### 2. A self-destructing cloud drill on AWS (`infra/aws`, `scripts/cloud-drill.sh`)

`./scripts/cloud-drill.sh [branch]` creates **one EC2 instance** (`m7i-flex.large`, free-plan eligible, 2 vCPU / 8 GiB) with Terraform, in the default VPC. Its user data installs Docker, k3d, Terraform and Ollama, clones the public repository and runs **the same `scripts/e2e.sh`**: the platform is deployed on k3s in the cloud and has to repair payments end to end. Then the instance terminates and `terraform destroy` removes the rest.

- **Nothing to attack:** the security group has **no inbound rule** (not even SSH), there is **no key pair** and **no instance role** (it holds no AWS credentials), IMDSv2 is required and the disk is encrypted and deleted with the instance. Progress is read from the **serial console** (`ec2:GetConsoleOutput`), which needs no network path to the machine.
- **Least privilege for whoever runs it:** the IAM user behind the `centinela` CLI profile has only [`infra/aws/iam-policy.json`](../../infra/aws/iam-policy.json): EC2 in one region, describe and console output, security groups, and `RunInstances` **only for `m7i-flex.large`**. A leaked key could not start expensive instances, touch other services or read the other project's data.
- **Bounded cost:** the instance schedules its own power-off at boot and `shutdown` means **terminate**, so even if the laptop sleeps or the script is killed it costs at most 2 hours of compute. A normal run is 30–60 minutes, about **USD 0.30–0.50** of the account's free-plan credit, and the script always runs `terraform destroy` on exit. The free plan cannot bill beyond its credit; a USD 10 budget alert is already in place.
- **Why not permanent:** the same instance running all month would cost about USD 70 and use up a credit shared with another project.

### 3. Container supply chain (`images.yml`)

- **Pull requests:** build both images and scan them with **Trivy**; the job fails on a CRITICAL vulnerability that has a fix. HIGH and CRITICAL findings are listed in the job summary.
- **main and version tags:** publish to **GitHub Packages** (`ghcr.io/fabricioanchundia/centinela-agent`, `…/centinela-shop`) with an **SBOM** and **build provenance** attached (BuildKit attestations), and **sign** each image with **Sigstore cosign**, keyless: the signature is bound to this repository's workflow through GitHub's OIDC token, so there is no signing key to leak. The job summary prints the `cosign verify` command.

### 4. A static public demo (`pages.yml`, `centinela site`)

`centinela site` builds one HTML page from files in the repository: the incidents recorded by the chaos run (what broke, **exactly what the AI saw**, what it concluded, what reached the human, and what today's guards would do with the same answer), the evaluation table and the red-team results. GitHub Pages serves it.

- **No server, no JavaScript, no console, no credentials, no third-party requests.** A `Content-Security-Policy` of `default-src 'none'` (inline styles and same-site fonts only) means no script could run even if one were injected. The Geist fonts (SIL Open Font License) are served from the same site, so a visitor's browser contacts nobody else.
- **Every value from a log, an event or a model answer is HTML-escaped**, tested with a `<script>` planted in a recorded case.
- The page is rebuilt when the recorded cases or results change.

## Consequences

- Every PR proves, on a cluster that did not exist a minute before, that Centinela deploys and repairs an outage end to end. A broken manifest, RBAC rule, migration or alert stops the merge.
- Published images can be verified by anyone (signature, SBOM, provenance) before running them. Dependabot keeps base images and actions current; Trivy fails the build when a critical fix is available.
- The public demo cannot be attacked because there is nothing to execute. It shows real recorded behaviour, not a mock-up, but it is not live.
- The cloud drill shows the platform deploys on AWS with Terraform, with a cost and attack surface close to zero. **Not done, on purpose:** a permanent cloud deployment. That would mean a managed cluster, HTTPS and SSO in front of the console (ADR-0008 already sets `Secure` cookies behind HTTPS), and a monthly cost decided first.
- The e2e test adds about 20 minutes of runner time per PR (free on public repositories).
