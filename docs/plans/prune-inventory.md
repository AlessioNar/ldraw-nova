# Prune inventory: optional features

Read-only inventory so the owner can decide what to cut. Nothing was removed.
Out of scope by owner decision (KEEP): the 3D player (ldraw-player WASM) and
the GLB export (bun + mpd2glb).

## How the numbers were obtained (and what is not verified)

- Source/test lines: `wc -l` on whole files; for features that live inside shared
  files, the line count is my count of the specific functions/branches/ranges
  (marked `~`). Treat `~` as +-15%.
- Image MB: no Docker build was run. Sizes come from downloading the exact
  artifacts the Dockerfile pulls and measuring them unpacked:
  - Codex: `npm pack @openai/codex@0.157.1-linux-x64` (the Dockerfile pins this),
    unpacked `vendor/x86_64-unknown-linux-musl/` = **374 MB** (`bin/codex` 285 MB,
    `bin/codex-code-mode-host` 74 MB, resources 26 MB, rest 5 MB). The Dockerfile
    COPYs that whole directory (line 282). Download size: 150 MB tgz.
  - Claude SDK: `pip download claude-agent-sdk` gave **0.2.168** (0.2.160, the pinned
    version, is listed on the index but I did not download it; I assume a similar size).
    Wheel 114 MB, unpacked 257 MB, of which `_bundled/claude` is 268 MB (the native
    Claude Code binary the app uses for login, status and inference).
  - Extra Python packages: a `pip install --dry-run` resolve (Python 3.12, same as
    Ubuntu 24.04's python3) of requirements.txt with and without the SDK: 66 vs 76
    packages. The SDK alone adds: `mcp`, `mcp-types`, `sse-starlette`,
    `python-multipart`, `PyJWT`, `cryptography`, `cffi`, `pycparser`,
    `opentelemetry-api`. I did not size these (cryptography is the only large one,
    single-digit MB).
- Image layer sizes are uncompressed on-disk estimates; the pushed (compressed) size
  will be smaller. Layers in Docker stack, so removing a layer saves its full size.
- Tests were not run. Importers/callers were found with grep over `app/`, repo root
  scripts and docs; dynamic imports (`from claude_agent import run_claude` inside
  `turn.py`) were included.
- Loss ratings (1 = nobody notices, 5 = core workflow broken) are my judgement.
  Score = (source lines + test lines + MB) / loss.

## Ranked summary

| Rank | Candidate | Src lines | Test lines | Image MB | Py/npm deps alone | User loss (1-5) | Score | Removal risk |
|---|---|---|---|---|---|---|---|---|
| 1 | **B1. Codex login runtime** (keep ChatGPT login via device code) | ~130 | ~100 | **~374** | npm `@openai/codex` (build stage only), apt `socat` | 1 | ~600 | Low |
| 2 | **A. Claude subscription path** (Agent SDK + Claude CLI login) | ~235 | ~140 | **~268** | `claude-agent-sdk` + 9 transitive packages | 3 | ~215 | Medium |
| 3 | **B2. Rest of ChatGPT subscription** (after B1: device login + `chatgpt/` transport) | ~260 | ~250 | 0 | none (litellm ships the transport) | 3 | ~170 | Medium |
| 4 | **D. Provider extras** (model_discovery + test-refresh, per-provider tests) | ~112 (+0 JSON) | ~126-400 | 0 | none | 3 | ~85 | Medium-high |
| 5 | **E. Gallery** (page, `/api/gallery*`, `models-gallery/`) | ~60 | ~90 | ~2 | none | 2 | ~75 | Low |
| 6 | **F. My Models page** (collection view, delete, zip) | ~150 unique | ~100 | 0 | none | 3 | ~80 | Low-medium |

Bundle "all browser login" = A + B1 + B2: ~690 src, ~420 test, ~642 MB, ~10 fewer
Python packages, whole Settings "Browser login" section and port 1455.
(Rank 4 and 6 are close; D is ranked above E and F only because of the test volume.)

Top recommendation: **B1 now** (cut the Codex runtime, ~374 MB, nearly zero user
loss because the device-code flow does not need the binary), then decide A (268 MB)
as a product call about Claude Pro/Max users.

## Shared structure you need to know first

- "Browser login" is two independent providers sharing `browser_auth.py`:
  - Anthropic: spawns the SDK's bundled `claude` binary (`auth login --claudeai`,
    `auth status`, `auth logout`); inference then goes through `claude_agent.py`.
  - OpenAI/ChatGPT: `login_worker.py` runs `codex app-server` (browser OAuth, needs
    callback port 1455) or LiteLLM's own device-code authenticator (no Codex binary);
    inference goes through LiteLLM's built-in `chatgpt/` provider (no extra package).
- The `claude` binary is used by login, status AND inference, so candidates (a) and
  the Claude half of (b) in the brief cannot be separated: removing the SDK without
  removing Claude login strands `browser_auth.claude_*`. They are one candidate, A.
- The Codex binary is used ONLY by `browser_login()` in `login_worker.py`
  (`["codex", "app-server", ...]`). The device flow (`DeviceAuthenticator`) and
  inference (`inference.params_for` -> `litellm.acompletion`) never call it.
  This is why B1 is separable and nearly free.
- API-key use of Anthropic/OpenAI/OpenRouter etc. is untouched by A and B: it goes
  through LiteLLM and `providers.py`.
- docs/README: the root README, `app/README.md` and `docs/` contain NO mention of
  Claude Agent SDK, browser login, ChatGPT subscription, Codex, port 1455 or the
  gallery (grep-verified; the only README mentions are jev-rerank/TYPESAFE).
  Nothing to update in docs for A, B, D, E, F beyond the code comments listed below.

---

## A. Claude subscription path (Claude Agent SDK + Claude CLI browser login)

| Item | Detail |
|---|---|
| Source (whole files) | `app/web/backend/claude_agent.py` 113 |
| Source (partial) | `browser_auth.py` ~75 of 230 (`claude_login_url`, `claude_binary`, `claude_env`, Claude branch of `connected`, `_login` else-branch, `submit_code`, Claude branch of `disconnect`); `routers/llm_models.py` ~14 (`POST /api/auth/{p}/code`, Claude branch in `llm_models_test` lines 133-136); `turn.py` 4 (lines 39-43); `agent.py` ~2 (provider pick lines 56-58); `llm_config.py` 1 (`"anthropic"` in browser tuple); frontend ~20 (`ProviderLogin.tsx` code form, `Settings.tsx` one `<ProviderLogin provider="anthropic">`, `AgentForm.tsx` `"anthropic"` in the browser-login conditions, `api.ts` `loginCode`); `requirements.txt` 1 |
| Source total | ~235 |
| Tests | `tests/test_chat_features.py` lines 176-311 (~136: Claude URL parsing, code submission, cancellation, adapter gate) plus `import browser_auth` parts; ~140 |
| Dockerfile | lines 276-279: comment + `RUN python3 -c "... claude_agent_sdk ... _bundled/claude --version"`; the `pip install -r requirements.txt` layer (line 276) shrinks by the SDK |
| Image MB | ~268 (bundled `claude` binary) + ~10-15 for the SDK and the 9 extra packages (unmeasured) = **~270-285** |
| Deps alone | Python: `claude-agent-sdk==0.2.160` (pins `mcp<3`), plus `mcp`, `mcp-types`, `sse-starlette`, `python-multipart`, `PyJWT`, `cryptography`, `cffi`, `pycparser`, `opentelemetry-api`. npm: none |
| Docs/README | none |
| User loses | Using a Claude Pro/Max subscription via "Browser login" in Settings. Anyone with an `ANTHROPIC_API_KEY` is unaffected (and keeps prompt caching, effort, pricing display). Existing agents saved with `auth_mode: "browser"` + `anthropic/` model would fail at start; they need a migration or deletion (not handled today) |
| Callers/importers | `claude_agent` imported only in `turn.py:40` (lazy). `browser_auth.claude_binary/claude_env/auth_dir("anthropic")` used by `claude_agent.py`. `claude_agent_sdk` imported only by `claude_agent.py`, `browser_auth.claude_binary`, and one test. `agent.py` re-exports are not affected |
| Risk | **Medium.** Edits the shared `browser_auth.py`, router, `turn.py`, frontend forms; stored `auth_mode` entries need handling; no runtime check done here |
| Files that would change | delete `app/web/backend/claude_agent.py`; edit `app/web/backend/browser_auth.py`, `turn.py`, `agent.py`, `llm_config.py`, `routers/llm_models.py`, `app/requirements.txt`, `app/Dockerfile` (lines 276-279), `tests/test_chat_features.py`, `frontend/src/components/ProviderLogin.tsx`, `AgentForm.tsx`, `pages/Settings.tsx`, `api.ts`. No compose/entrypoint change |

## B1. Codex login runtime (browser OAuth for ChatGPT)

| Item | Detail |
|---|---|
| Source (partial) | `login_worker.py`: `import_credentials` + `browser_login` ~92 of 126; `entrypoint.sh` 3 (lines 34-36, the socat relay + comment); `docker-compose.yml` 2 (port `127.0.0.1:1455:1456` and its comment, lines 23-24); Dockerfile ~11 (stage `openai-login` lines 26-31, `COPY ... /opt/codex/` + `ln -s` + `codex --version` lines 281-283, `socat` in apt list lines 184 and 196); frontend ~25 (browser-vs-device toggle in `ProviderLogin.tsx`; device becomes the only flow); `browser_auth.py` ~5 (flow switch) |
| Source total | ~130 |
| Tests | `tests/test_login_worker.py` ~100 of 134 (browser flow, credential import, worker/parent handling, flow switching); ~100 |
| Dockerfile | new-layer COPY of `/opt/codex/` (line 282) plus the throwaway `openai-login` build stage (npm pack, 150 MB download, not in final image) |
| Image MB | **~374** (whole `vendor/x86_64-unknown-linux-musl/` dir is COPYed; could be cut to ~285 MB by copying only `bin/codex` if kept, but B1 removes it). `socat` apt package is ~0.5 MB (unmeasured) |
| Deps alone | npm `@openai/codex` (build stage only); apt `socat` |
| Docs/README | none; code comments in compose/Dockerfile/entrypoint mention the relay |
| User loses | The one-click "sign in with ChatGPT in the browser" flow. ChatGPT subscription login stays available through the device-code flow (user must enable device-code sign-in in ChatGPT security settings first, per `ProviderLogin.tsx` text). Also port 1455 no longer has to be free on the host |
| Callers/importers | `codex` is invoked only at `login_worker.py:46` (`subprocess.Popen(["codex", "app-server", ...])`) and by the Dockerfile smoke test. `login_worker.py` is spawned by `browser_auth._login` (line 140). Nothing else references port 1455/1456 (grep: entrypoint, compose, a login_worker error string) |
| Risk | **Low.** Self-contained subprocess; inference path untouched. Unverified: device flow works end to end in this image without Codex (code reading says yes: `DeviceAuthenticator` uses only LiteLLM) |
| Files that would change | `app/Dockerfile` (lines 26-31, 184, 196, 281-283), `app/entrypoint.sh` (lines 34-36), `app/docker-compose.yml` (lines 23-24), `app/web/backend/login_worker.py`, `browser_auth.py`, `tests/test_login_worker.py`, `frontend/src/components/ProviderLogin.tsx`. Not touched: requirements.txt, docker-compose.dev.yml (no port there) |

## B2. Remainder of ChatGPT subscription (device login + `chatgpt/` transport)

Only meaningful after, or together with, B1.

| Item | Detail |
|---|---|
| Source | `login_worker.py` rest (~34) ; `browser_auth.py` OpenAI parts ~50 (`openai_authenticator`, `openai_ready`, branches); `inference.py` browser branch lines 38-54 (~17, incl. `litellm.register_model` workaround); `llm_config.py` ~4 (`chatgpt/` forced to browser, tuple); `providers.py` 1 (chatgpt row); `model_catalog.py` 1 (`chatgpt/` canonicalization); `routers/llm_models.py` ~30 (status, login, disconnect endpoints, shared with A); frontend ~40 (ProviderLogin openai branch, `agentProvider`/`providerName` chatgpt mapping in `modelChoices.ts`, AgentForm); `main.py` 2 (`browser_auth.shutdown`) |
| Source total | ~260 (if A is also removed, the router/`browser_auth.py` files go entirely) |
| Tests | `tests/test_chatgpt_transport.py` 103 (whole), `tests/test_chat_features.py` lines 42-66 and 156-175 (~45), rest of `test_login_worker.py` (~34) = ~180-250 |
| Image MB | 0 beyond B1 (the transport is inside the already-required `litellm`) |
| Deps alone | none |
| Docs/README | none |
| User loses | ChatGPT Plus/Pro subscription as a model backend. OpenAI API keys unaffected |
| Callers | `inference.params_for` (called from `turn.py`, `routers/llm_models.py` test route); `browser_auth.openai_ready`; `llm_config.create`; frontend model pickers |
| Risk | **Medium.** Touches `inference.params_for`, config validation, UI labels; stored `auth_mode: "browser"` OpenAI entries need handling |
| Files | `app/web/backend/login_worker.py` (delete), `browser_auth.py`, `inference.py`, `llm_config.py`, `providers.py`, `model_catalog.py`, `main.py`, `routers/llm_models.py`, `tests/test_chatgpt_transport.py` (delete), `tests/test_login_worker.py` (delete), `tests/test_chat_features.py`, frontend `ProviderLogin.tsx`, `modelChoices.ts`, `AgentForm.tsx`, `Settings.tsx`, `ModelPicker.tsx`, `api.ts` |

## D. Provider-specific code beyond the registry

| Item | Detail |
|---|---|
| Source | `model_discovery.py` 112; `model_catalog_data.json` 684 lines / 18 KB (25 presets: 13 OpenRouter, 6 OpenAI, 6 Anthropic); `providers.py` 113 is the registry itself and is small, and non-registry providers (azure, vertex, bedrock, gemini, mistral, ollama) cost ~10 table rows and add no dependency (all via LiteLLM) |
| Tests | `test_model_discovery.py` 126, `test_openrouter.py` 200, `test_providers.py` 54, `test_prompt_cache.py` 74 (only partly provider-specific) |
| Image MB | ~0.02 (the JSON). Nothing to save |
| Deps alone | none (`httpx` already required by LiteLLM) |
| Docs/README | none |
| User loses | If `model_discovery.py` goes: the "Test" button no longer refreshes context window / prices / effort levels from OpenRouter or Anthropic; the app falls back to the bundled snapshot and LiteLLM's catalogue. If the JSON catalogue goes: the preset picker disappears AND `model_catalog.builder_supported()` has only LiteLLM's metadata to decide which models may be used; new models LiteLLM does not know are rejected |
| Callers | `model_discovery` imported only by `routers/llm_models.py:18` (one call at line 126). `model_catalog` imported by `agent.py`, `turn.py`, `inference.py`, `llm_config.py`, `claude_agent.py`, `model_discovery.py`, router (core) |
| Risk | **Medium-high for the catalogue (core gating), low-medium for `model_discovery.py` alone.** The catalogue is not a size win, so only `model_discovery.py` (+ its 126 test lines) is a sensible cut |
| Files | `app/web/backend/model_discovery.py` (delete), `routers/llm_models.py` (lines 18, 126-129 and `tested` closure), `providers.py` (`discovery` field and `catalogue()` flag), `tests/test_model_discovery.py` (delete), `frontend/src/api.ts` line 112 (`discovery` type). OpenRouter-specific tests are not removable without dropping OpenRouter reasoning-stream support (`inference.preserve_openrouter_reasoning_chunks`) |

## E. Gallery

| Item | Detail |
|---|---|
| Source | `routers/models_gallery.py` ~12 (`/api/gallery`, `/api/gallery/zip`, "/demo/" alias); `routers/files.py` ~8 (`/gallery-files`, `/demo`); `routers/common.py` ~15 (`is_gallery` and branches); `routers/spa.py` 1; `settings.py` ~4 (`GALLERY_MODELS_DIR`); frontend ~14 (`Models.tsx` isGallery branches, `App.tsx` routes + landing redirect `/` -> `/gallery`, `Sidebar.tsx` link, `api.ts`); `vite.config.ts` proxy list; `docker-compose.dev.yml` 1 mount (line 42); Dockerfile 2 (lines 314-316) |
| Source total | ~60 |
| Tests | `test_api.py` lines 132-176 and 192-215 (~70); `test_glb.py` gallery/demo parametrization (~15); `conftest.py` 4; `test_model_delete.py` 2 asserts; `test_gallery.py` 22 stays (tests `info_heading_of`, shared with My Models); ~90 |
| Data | `app/models-gallery/`: 6 models, 24 files, 1.8 MB |
| Image MB | ~2 |
| Deps alone | none |
| Docs/README | comments only: `app/docker-compose.yml` line 41 and `app/leocad_render.py` line 13 give `/opt/models-gallery/copper-bean.mpd` as the sample command |
| User loses | The landing page and the six bundled sample models (the first thing a new user sees; the "try a model without an API key" path). `gallery.py` (154 lines) is NOT removable: despite the name it is the snapshot/BOM queue for My Models |
| Callers | `gallery.py` is imported by `routers/common.py`, `routers/models_gallery.py`, `routers/chats.py`, `tools.py` (all My Models); `settings.GALLERY_MODELS_DIR` used by `common.py`, `models_gallery.py`, `files.py` |
| Risk | **Low**; mostly deletions plus changing the default route to `/new` |
| Files | `app/models-gallery/` (delete), `app/Dockerfile` (314-316), `app/docker-compose.dev.yml` (42), `app/docker-compose.yml` (comment 41), `app/leocad_render.py` (docstring), `routers/models_gallery.py`, `files.py`, `common.py`, `spa.py`, `settings.py`, `frontend/src/App.tsx`, `Sidebar.tsx`, `pages/Models.tsx`, `api.ts`, `vite.config.ts`, tests listed above |

## F. My Models page (collection browser)

| Item | Detail |
|---|---|
| Source | `routers/models_gallery.py` ~100 unique (list, delete, `model_from_url`, zip); `Models.tsx` 96 (shared with Gallery); `ModelCard.tsx` 167 (also used elsewhere? not verified, so excluded); `gallery.py` queue is still needed by chats (`routers/chats.py:73`, `tools.py:189`). Unique ~150 |
| Tests | `test_model_delete.py` 76, share of `test_api.py` ~25; ~100 |
| Image MB | 0 |
| User loses | Browsing/deleting/zipping generated models outside the chat; models are still shown inside each chat and on disk under `./data/generated`. No dependency saving |
| Callers | `model_from_url` is re-exported from `main.py` and used by `glb.py`-adjacent routes and tests, so deleting it is NOT clean |
| Risk | **Low-medium** (tangled with GLB export and chat cards, which are KEEP) |
| Verdict | Poor value; listed so the owner knows it was assessed |

## Other things looked at (not recommended)

| Item | Why not |
|---|---|
| `jev-rerank` layer + uv-managed Python 3.14 (`Dockerfile` 211-252) | jev-rerank is the headline discovery tool in the README and `ldraw_tools/discovery.py`; Python 3.14 is also needed by `uv sync` of the toolkit (lines 288, 296). Size not measured |
| `examples/` baked into the image (76 MB, `Dockerfile:292`) and `data/` (443 MB; `models-annotated` 388 MB, `ldraw-info.db` 26 MB, `offLibShadow` 19 MB, `Dockerfile` `COPY --from=nova data/`) | Referenced by toolkit tests and the agent system prompt (`prompts/system.md:17`), and discovery reads `models-annotated`. Largest weights in the repo, but not optional UI features: owner's call. Not measured as image layers |
| `environment_config.py` / `EnvironmentSettings.tsx` / `routers/environment.py` (~300 lines + 216 test) | Holds `TYPESAFE_API_KEY` used by `tools.py`; core |
| `attachments.py` (103) + `test_documents.py` (86) and `poppler-utils` | Document upload to chat; `poppler-utils` is also advertised to agents; moderate loss, small saving |
| `scripts/ldraw-render-steps.sh` (175), `app/example.py` (40), `docker-compose.dev.yml` (49) | Small, referenced by the toolkit docs / dev workflow |
| `usage.py` (49) + `UsageBadge.tsx` (17) | Tiny; core to cost display |

## Suggested order

1. B1 (cut Codex runtime): ~374 MB, ~230 lines incl. tests, device-code login remains.
2. A (cut Claude SDK + Claude CLI): ~270 MB, ~375 lines incl. tests; only after confirming
   nobody relies on Claude subscription login. Migrate or drop stored `auth_mode: "browser"` entries.
3. B2 only if you also want to drop ChatGPT subscription entirely; combined with A it
   deletes `browser_auth.py`, `login_worker.py`, the `/api/auth/*` routes and the Settings
   "Browser login" section outright (about 690 source lines, 642 MB).
4. D (`model_discovery.py` only), E, F: small wins; skip unless the line count is the goal.
