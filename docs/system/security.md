# Security (short version)

The full report is [docs/security/2026-10-24-security-pass.md](../security/2026-10-24-security-pass.md). Its follow-ups were merged as "security hardening" (commit `bcfef7c4`).

## Threat model

- The app is local, with **no login**, on `127.0.0.1:8765`.
- Three attackers are considered:
  1. a malicious web page the owner visits;
  2. a prompt-injected agent whose code runs in the sandbox;
  3. a compromised dependency at rebuild.

## What protects what

| Protection | Against | Code |
|---|---|---|
| **Sandbox user `agent`** with a scrubbed environment, CPU/file/output limits and a process-group kill | Agent code reading keys or damaging the app | `app/web/backend/sandbox.py` |
| `/config` is root-only (0700) | Agent code reading LLM keys | `app/Dockerfile`, `settings.py:33` |
| **Firewall**: `agent` cannot reach the app's own port, `host.docker.internal` or the gateway | Agent code calling the unauthenticated API (H1) | `app/entrypoint.sh:13-66` |
| Endpoint-change guard on LLM entries | Redirecting a saved key to an attacker host (H1) | `llm_config._guard_endpoint_change` |
| **Host header allowlist** (421 otherwise): `localhost`, `host.docker.internal`, IP literals, `LDRAW_NOVA_ALLOWED_HOSTS` | DNS rebinding (H2) | `app/web/backend/main.py` |
| Cross-site API requests rejected (403) | Malicious pages | `main.py` `same_origin_api` |
| Forbidden env var names (`PATH`, `LD_*`, `PYTHON*`, ...) | Code injection through Settings (M1) | `environment_config.py:28-34` |
| `Content-Security-Policy: sandbox` and `nosniff` on agent-written files | Script running from the app origin (M2) | files router |
| Separate, spend-capped `JEV_OPENROUTER_API_KEY` | The main key leaking to agent code (M4) | `toolkit.jev_environment` |
| **Hash-locked deps** (`app/requirements.lock`, `--require-hashes`), digest-pinned base images, sha256-checked downloads | Supply chain | `app/Dockerfile` |
| Compose hardening: `cap_drop: ALL` plus 8 caps, `no-new-privileges`, 4 GB memory, 512 pids | Container escape blast radius | `app/docker-compose.yml:38-62` |

## Residual risks to know

- **Agent code has internet access.** A prompt-injected agent with full permissions can send out whatever it can read. That includes **the Jev key**, which is why it should be a separate key with a low cap.
- **The firewall depends on a capability.** If the container starts without NET_ADMIN, Settings shows "Sandbox isolation: off", and the H1 path is open again.
- **No login.** Binding to `0.0.0.0` (`LDRAW_NOVA_WEB_BIND`) exposes everything to the LAN.
- **Not done yet:** Dependabot/Renovate and audits in CI.
