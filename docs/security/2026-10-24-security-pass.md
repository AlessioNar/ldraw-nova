# Security pass, 2026-10-24

Scope: the self-hosted Open Brick Designer (FastAPI backend, React frontend, viewer pages, toolkit, Docker image).
Threat model: a local app with no login, bound to 127.0.0.1:8765. Realistic attackers are (1) a malicious web page the
owner visits, (2) a prompt-injected agent whose code runs in the sandbox, (3) a compromised dependency at rebuild time.
Branch `security-pass` from master e7f6a613. Method: pip-audit and npm audit, source review, targeted tests.
No paid API calls; the running container was only inspected read-only.

## 1. LiteLLM verdict

The app uses the LiteLLM Python SDK only (`litellm.acompletion` in `inference.py` and `routers/llm_models.py`, plus model
listing helpers). It does not run the LiteLLM proxy, virtual keys, the semantic cache or the admin UI (no proxy config, no
`proxy` import, no cache setup in `app/web/backend`). Pinned `litellm==1.102.1` (`app/requirements.txt:5`); the running
image reports 1.102.1.

| Item | Advisory checked (OSV) | Affected | 1.102.1 | Component used here? | Verdict |
|---|---|---|---|---|---|
| MAL-2026-2144 (credential-stealing .pth) | https://osv.dev/MAL-2026-2144 | 1.82.7, 1.82.8, 1.107.0 | not listed | n/a | Not affected. Never installed. |
| CVE-2026-89032 (semantic cache tenant isolation) | https://osv.dev/CVE-2026-89032 | < 1.101.0-rc.1, proxy semantic cache, needs a virtual key | fixed (1.102.1 > 1.101.0-rc.1) | No | Not affected. |
| CVE-2026-114612 / GHSA-7hp6-4w63-5g45 (proxy admin escalation, one key for secrets and session tokens) | https://osv.dev/CVE-2026-114612 | 1.87.0 up to < 1.102.2 on the 1.102 line (also < 1.100.4, 1.101.3, 1.103.1, 1.104.0-rc.2) | inside the range by version number | No: proxy server, internal users, MCP stdio endpoint | Version is in range, exploit surface is absent. Not exploitable here. Moving to 1.102.2 is a free patch bump once vetted (R2). |
| CVE-2026-35029 / GHSA-53mr-6c8q-9789 (`/config/update` missing admin check, proxy) | https://osv.dev/CVE-2026-35029 | < 1.83.0 | fixed | No | Not affected. |
| CVE-2026-59823 / GHSA-hx8v-g79f-8w5f (`user_config` nested api_base SSRF, proxy) | https://osv.dev/CVE-2026-59823 | < 1.83.9 | fixed | No | Not affected. |

Summary: no LiteLLM advisory applies. The remaining LiteLLM risk is supply chain (a bad release arriving on a rebuild),
addressed in section 3. Note the MAL record's own dates are inconsistent (published 2026-03-24, details mention 2026-10-21);
the affected-version list is what matters here.

## 2. Dependency audit

Throwaway container `obd-security-check` from `ldraw-nova-app`, pip-audit installed with uv inside it.

- `app/requirements.txt` resolved fresh: no known vulnerabilities.
- Installed system site-packages (68 pins): `setuptools 68.1.2` (PYSEC-2025-49, PYSEC-2026-1918, PYSEC-2026-3447,
  GHSA-xmj3-9gf7-h52w) and `wheel 0.42.0` (CVE-2026-24049). Both are Ubuntu apt packages used for installs only, not
  imported by the app. Low.
- Toolkit (`uv.lock`, venv at `/opt/ldraw-nova/.venv`): `urllib3 2.7.0` (PYSEC-2026-4175, -4176, -4177, fixed in 2.8.0) and
  `certifi 2026.7.22` (PYSEC-2026-4320, fixed in 2026.10.19). `requests` is used by pyldraw3. Medium-low. Fix by
  `uv lock --upgrade-package urllib3 --upgrade-package certifi`; not done here because it changes the toolkit lock and
  needs the toolkit test suite.
- jev-rerank (`/opt/uv/tools/jev-rerank`, 11 packages): no known vulnerabilities.
- `npm audit --omit=dev`: 0 vulnerabilities in `app/web/frontend` and `app/web/viewer-deps`. Full audit: viewer-deps 0;
  frontend 2 high, both dev-server only (vite 8.3.0-8.3.2 dev-server issues, source-map-js DoS). The image only ships the
  built static files. Fix with `npm audit fix` in `app/web/frontend`.

Pinned vs floating (`app/Dockerfile`):
- Pinned with sha256: bun, mpd2glb, ldraw-player, uv, jev-rerank, the packed reference models. LeoCAD pinned by tag
  (resolved through the GitHub API at build, no checksum). LDraw viewer source pinned by commit; three.js GLB loaders by
  tag r139 (tag, not hash).
- Floating: `ubuntu:24.04` and `node:24-slim` (tags, no digest), apt packages (no versions), the LDraw parts library
  (`library.ldraw.org/library/updates/complete.zip`, always latest, no checksum), the LeoCAD AppImage (no checksum).
- Python: `requirements.txt` pinned four top-level packages, but their ~60 transitive packages were resolved at build time.
  No runtime code path installs or upgrades packages (no `pip install`, `-U` or `uv pip` in backend, scripts or
  entrypoint).

## 3. Supply-chain hardening (done)

- New `app/requirements.lock`: the full closure of `requirements.txt` (66 packages, `==` pinned, 2,295 sha256 hashes,
  litellm 1.102.1 included). `app/Dockerfile` now installs with
  `pip install --require-hashes --no-deps -r requirements.lock`. A rebuild can no longer fetch a newer litellm or a
  re-uploaded file under the same version. The lock was generated from the versions installed in the current image and
  verified by downloading everything with `--require-hashes` in a throwaway container. The regeneration command is in the
  file header. Not yet verified by a full `docker build` (heavy; run it once before merging).
- Hashes were taken from PyPI on 2026-10-24. 1.102.1 predates the incident and the image already contains it, but the
  hash list was not compared with a pre-incident record; cross-check litellm's hashes against another source if desired.
- Not done: digest-pinning base images, checksums for the LeoCAD AppImage and the LDraw library (R4).

## 4. Findings, ranked

### High

H1. Agent code can call the app's own unauthenticated API (key exfiltration path). Sandboxed code shares the container's
network namespace (`sandbox.py` only drops the uid and scrubs env) and the API has no login. The Origin check
(`main.py:90`) lets through requests with no Origin header, such as `curl`. Prompt-injected agent code can
`PUT /api/llm-models/{id}` to point `api_base` at a server it controls while the entry's `api_key` is
`os.environ/OPENROUTER_API_KEY` (or a saved key), then `POST /api/llm-models/{id}/test`: the app sends the real key to that
host. It can also delete chats and models. This defeats the "/config is root-only" protection. Not fixed: needs a design
decision (R1). I did not run this attack; it follows from the code paths above.

H2. DNS rebinding (Host header was never validated). The same-origin check compared Origin with Host only. With a
rebinding domain both name the attacker, so a web page the owner visits could read and write every route: save keys, change
`api_base`, start agent turns that run code, delete data. Fixed (section 5).

### Medium

M1. Arbitrary environment variable names could be saved (`environment_config.py`, `PUT /api/environment`) and were applied to
the root server's `os.environ`, so `LD_PRELOAD`, `PATH`, `PYTHONPATH` and similar would affect every later subprocess.
Fixed.

M2. Agent-writable files served from the app origin: agent code owns `data/generated`, and `/files/...` served any file
there with a guessed content type, so an `.html` or `.svg` planted by a prompt-injected agent would run with full API
access when opened. Fixed with `Content-Security-Policy: sandbox` and `nosniff` (`routers/files.py`). Chat artifacts
already had both.

M3. The root server followed agent-planted symlinks: `/api/models/zip` (`routers/models_gallery.py:92`) archived symlink
targets (a link to `/config/environment.json` would be zipped), and `gallery.collection` read titles through symlinks.
Fixed (symlinks skipped).

M4. The OpenRouter key is handed to agent code as `TYPESAFE_API_KEY` for Jev search (`toolkit.py:30-36`,
`tools.py:117-124`). It is redacted from tool output (exact match, also when split across pipe reads, `sandbox.py:123`) and
no other key is in the sandbox env, but code can send it over the network or write it to a file (outputs under
`data/output` are downloadable). Redaction does not catch encoded forms (base64, reversed). Recommend a dedicated,
spend-capped key for Jev (R3).

M5. Dev compose (`docker-compose.dev.yml:48-50`) mounts the repo read-write at `/app` and `./scripts` at `/opt/scripts`, and
runs uvicorn `--reload` as root. On Docker Desktop for Windows those files are likely writable by the agent user, so agent
code could edit backend Python that root then executes (sandbox escape to root, dev only). Production compose mounts no
code. Recommend `:ro` code mounts in the dev file (R5).

### Low / informational

L1. The container runs as root and the compose file has no `cap_drop`, `no-new-privileges` or read-only root
(`app/Dockerfile:271-273`, `docker-compose.yml`). Mitigated by the privilege drop for agent code.
L2. `LDRAW_NOVA_WEB_BIND=0.0.0.0` exposes a no-login app that can spend API credit and run code. Documented in the compose
file. With the new Host check, LAN IP access still works; LAN names need `LDRAW_NOVA_ALLOWED_HOSTS`.
L3. The agent can read other chats' work folders and write `data/generated` (by design, shared collection).
L4. CSV export injection: BOM CSVs and `ldraw_tools/external.py:158` write part names and descriptions unescaped; a value
starting with `=`, `+`, `-` or `@` could run as a formula in a spreadsheet. Names come mostly from the LDraw library;
imported models are the untrusted source.
L5. setuptools/wheel apt versions; partly floating base images and downloads (section 2).

### Checked and found sound

- Cross-origin POST/PUT/DELETE: `Sec-Fetch-Site: cross-site` and a mismatched `Origin` get 403; state changes are never GET
  (`/api/llm-providers/{p}/test` is POST). `Origin: null` is refused.
- API key handling: `/api/llm-models*` masks secrets (`llm_config.mask_secrets`), `/api/environment` is write-only, the
  export masks keys, `/api/llm-providers/keys` returns booleans, the validation error handler strips input values
  (`main.py`), `provider_errors.py` never echoes provider text or keys. No logger writes request bodies, headers or params;
  LiteLLM verbose logging is not enabled. Provider keys are not in the sandbox env.
- Path handling: `safe_join` is used by `/files`, `/ldraw*`, `/reference-models`, chat artifacts, the SPA fallback, agent
  file tools, attachments and `model_from_url`. Delete and edit routes accept only a bare file name and refuse symlinks. Chat
  ids are validated by regex. `data/output` is never served by `/files`; artifacts block dot-paths.
- Uploads: documents are decoded with `validate=True`, capped (5 MB each, 12 MB total, 4 files), names without folders,
  saved in a per-upload directory. CSV inventories are capped (25 MB) and parsed in the sandbox. The app never extracts
  zip files (it only creates them), so there is no zip-slip surface. Request size is capped by middleware
  (Content-Length based; a chunked body without Content-Length is not capped by it, a low-severity gap).

### Prompt injection (untrusted documents, imported models, part names)

Existing mitigations: tool policy by mode and permission (plan and read-only modes expose read tools only,
`tool_policy.py`), delegation only under full permissions, workers cannot run shell or publish, a stray-write guard for
workers, write roots, file tools limited to the chat's folders, output redaction, documents saved as files rather than
inlined as instructions. Residual risk: in full-permission agent mode, injected text can make the agent run shell or Python
with network access (H1, M4). Proportionate steps: keep the default permission level low for chats with attached documents,
ask for confirmation of shell commands in chats with attached documents or imported models (UX change), and fix H1.

## 5. Fixes made (tests in `app/web/backend/tests/test_security.py`)

| Fix | Code | Test |
|---|---|---|
| Host header allowlist (localhost, IP literals, host.docker.internal, `LDRAW_NOVA_ALLOWED_HOSTS`), HTTP 421 otherwise, on every route | `main.py` `host_allowed`, `reject_unknown_host` | `test_unknown_host_is_refused_even_when_origin_matches`, `test_local_hosts_are_served`, `test_extra_hosts_can_be_allowed` |
| Forbidden environment names (`PATH`, `LD_*`, `PYTHON*`, `BASH_ENV`, `UV_*`, `PIP_*`, ...) | `environment_config.py` `_forbidden` | `test_environment_refuses_names_that_change_program_loading`, `test_environment_still_accepts_provider_keys` |
| Sandbox CSP and nosniff on `/files` | `routers/files.py` | `test_served_files_cannot_run_scripts_in_the_app_origin` |
| Skip symlinks in the zip and the model collection | `routers/models_gallery.py`, `gallery.py` | `test_collection_and_zip_ignore_planted_symlinks` |
| Hash-locked Python install | `app/requirements.lock`, `app/Dockerfile` | hashes verified by a `--require-hashes` download in a throwaway container |

Test runs (one container at a time, worktree mounted read-only): `test_security.py` 23 passed; with `test_api`,
`test_gallery`, `test_builder_settings`, `test_inventories` 108 passed, 13 skipped; `test_environment` and
`test_model_delete` passed. `conftest.py` sets `LDRAW_NOVA_ALLOWED_HOSTS=testserver` for Starlette's TestClient.

Behaviour change: opening the app by a hostname other than `localhost` (for example `mypc.local`) now returns 421 until the
name is listed in `LDRAW_NOVA_ALLOWED_HOSTS`. Saving `PATH`, `LD_*`, `PYTHON*`, `UV_*`, `PIP_*` in Settings now errors.

## 6. Remaining work and recommendations (owner decisions)

R1 (H1, highest value). Stop sandboxed code from using the owner's API. Options: (a) reject API requests whose peer address
is loopback when the server runs as root in the container (the browser arrives via Docker's gateway address, agent `curl`
from 127.0.0.1); simple, but unverified on every Docker network mode, so test it on your setup first; (b) run agent code in
its own network namespace or container with an allowlist for the Jev/OpenRouter hosts, which also blocks exfiltration but
breaks agent downloads; (c) a per-process token the agent cannot read (needs a login or key in the UI). Also refuse
`api_base` changes on entries that reference `os.environ/` keys unless the key is re-entered, so a changed endpoint never
inherits a saved key.
R2. Bump `litellm` to 1.102.2 (patch on the same line; fixes CVE-2026-114612, which does not apply to the SDK) only after
checking its hashes and release notes, then regenerate `requirements.lock`.
R3. Use a separate, spend-capped OpenRouter key for Jev, or route Jev through the backend instead of the agent env.
R4. Pin base images by digest, checksum the LeoCAD AppImage and the LDraw `complete.zip`, add Dependabot or Renovate for
`requirements.txt`, `uv.lock` and the two package-lock files, and run pip-audit and npm audit in CI.
R5. Dev compose: mount code `:ro` (or accept that dev mode is not a sandbox). Production compose: `cap_drop: [ALL]` (re-add
only what Xvfb/LeoCAD need, test it), `security_opt: [no-new-privileges:true]`, `pids_limit`, memory limits. Consider
running uvicorn as a non-root user.
R6. Bump `urllib3` and `certifi` in the toolkit `uv.lock`; run `npm audit fix` in `app/web/frontend`.
R7. CSV export: escape formula-leading cells. Cap chunked request bodies.
R8. Run one full `docker build` of this branch before merging, since the Dockerfile install step changed.
