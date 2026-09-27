# Application Manifest Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A trusted Go application becomes manageable in Bifrost by adding only its code and `applications/<id>/app.yaml`.

**Architecture:** A registry scans and validates manifests, combines them with unchanged legacy records, and persists assigned ports. The portal, controller, and generated Caddy fragments all consume one registry snapshot. New apps receive their own gateway port, so they use normal root-relative URLs.

**Tech Stack:** Python 3, Flask, PyYAML, psutil, Caddy, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-27-application-manifest-design.md`

## Global Constraints

- Scan `applications/<id>/app.yaml` at portal startup and administrator-page refresh; discovery never builds or starts an application.
- Accept `schema: 1`, matching `id`, `name`, optional `description`, optional `build` argv, and required `start` argv; substitute only `${PORT}` and `${DATA_DIR}`.
- Execute argv directly with no shell. Keep generated binaries and data under the app directory in ignored paths.
- Preserve Ticket and Douyin commands, adapters, existing routes, and Douyin verification behavior.
- New applications require no Bifrost status endpoints. Preserve conservative process ownership checks; an optional activity check can prevent Stop.
- The operator places trusted code and build tools on the server. Never expose business listeners outside loopback.
- Preserve existing uncommitted changes to `portal.py` and `static/`; inspect the diff before editing and stage only task files.

## Review Focus

- A malformed manifest beside a valid one must not hide the valid application; Task 1 tests this.
- Replacing or deleting a running manifest must not change the process record used to stop it; Task 2 tests this.
- An assigned port occupied by another process must not be used to start an application; Task 2 tests allocation and collision handling.
- A crafted `X-Forwarded-Uri` or `Origin` from an unauthorized app origin must fail authorization; Task 4 tests this.
- Failed Caddy validation or reload must keep the previous usable routes; Task 3 tests rollback.

---

### Task 1: Manifest registry and app model

**Files:** Create `app_registry.py`; modify `apps.py`, `requirements.txt`, `.gitignore`; create `tests/test_app_registry.py`.

**Interfaces:** Produce `AppRegistry(applications_dir: Path, runtime: Path, public_origin: str = "http://127.0.0.1:8080")`, `refresh() -> None`, `get(app_id: str) -> App | None`, `all() -> tuple[App, ...]`, `pin(app_id: str) -> None`, `unpin(app_id: str) -> None`, and `errors: dict[str, str]`. Extend `App` with `kind`, `build`, `external_port`, and `origin` without changing legacy field meanings. Compute manifest origins from the public origin's scheme and host plus assigned external port. Keep `LEGACY_APPS` in `apps.py`; no new app ID is added there. Persist pinned records in `runtime/apps-registry.json` so a portal restart can still identify and stop a running app whose manifest changed.

- [ ] **Step 1: Write failing tests** for a valid `weixin/app.yaml`, invalid ID/argv/placeholder/path, duplicate legacy ID, and a valid directory beside an invalid one. Assert the valid app is returned, errors are visible, and scan itself never executes commands.
- [ ] **Step 2: Run** `.venv/bin/python -m unittest tests.test_app_registry -v`; expect failures because `AppRegistry` does not exist.
- [ ] **Step 3: Implement** the registry and `App` extension. Use `yaml.safe_load`; restrict startup executable to the application directory and require argv arrays of strings. Add `PyYAML>=6,<7`; ignore `applications/**/.bifrost/` and `applications/**/.runtime/`.
- [ ] **Step 4: Run** `.venv/bin/python -m unittest tests.test_app_registry tests.test_contract -v`; expect pass.
- [ ] **Step 5: Commit** only Task 1 files.

### Task 2: Stable ports, build, and process lifecycle

**Files:** Modify `app_registry.py`, `service_control.py`; modify `tests/test_app_registry.py`, `tests/test_service_control.py`.

**Interfaces:** `AppRegistry` persists an ID-to-internal/external-port map in `runtime/apps-ports.json`; `Controller(runtime: Path, registry: AppRegistry)` uses registry lookup instead of module `APPS`. `Controller.status(app_id)` and `operate(app_id, action)` retain return values. `Controller` invokes an optional build before starting, with timeout, captured log, and argv substitution. Legacy apps continue using their adapters; manifest apps use listener ownership as default readiness and no default activity endpoint.

- [ ] **Step 1: Write failing tests** for stable allocation across registry recreation, occupied port avoidance, build invocation only on Start, build failure, correct `${PORT}` substitution, refusing unknown-owner Stop, and retaining a running record across manifest removal or edit.
- [ ] **Step 2: Run** the two focused test modules; expect failures at new assertions.
- [ ] **Step 3: Implement** port persistence and controller changes. Bind-test candidate loopback ports; verify the process owns the listener before reporting Running. On Stop send `SIGTERM`, wait for exit and port release, and preserve legacy activity checks.
- [ ] **Step 4: Run** `.venv/bin/python -m unittest tests.test_app_registry tests.test_service_control -v`; expect pass.
- [ ] **Step 5: Commit** only Task 2 files.

### Task 3: Generated gateway routes

**Files:** Create `gateway_routes.py`, `tests/test_gateway_routes.py`; modify `Caddyfile`, `Caddyfile.public`, `.gitignore`.

**Interfaces:** `render_fragments(apps: tuple[App, ...], public_origin: str) -> tuple[str, str]` returns local/public Caddy text for manifest apps. `GatewayRoutes(runtime: Path, public_origin: str, config_path: Path | None).sync(apps) -> None` atomically writes `.runtime/apps.local.caddy` and `.runtime/apps.public.caddy`, validates the configured active Caddyfile, reloads Caddy when running, and restores prior fragments on failure.

- [ ] **Step 1: Write failing tests** that assert one app produces a site on its assigned external port with `forward_auth`, header cleanup, cookie removal, and loopback proxy; assert Ticket/Douyin generate no new sites and failed validation/reload restores old fragments.
- [ ] **Step 2: Run** `.venv/bin/python -m unittest tests.test_gateway_routes -v`; expect failures.
- [ ] **Step 3: Implement** renderer and sync. Add fragment imports at top level in both tracked Caddyfiles; ensure empty fragments exist before Caddy startup. Keep current fixed sites untouched.
- [ ] **Step 4: Run** `.venv/bin/python -m unittest tests.test_gateway_routes -v`; expect pass. If `caddy` exists, run `caddy validate --config Caddyfile` and `caddy validate --config Caddyfile.public` with required public env values.
- [ ] **Step 5: Commit** only Task 3 files.

### Task 4: Portal discovery, grants, and authorization

**Files:** Modify `portal.py`; modify `tests/test_portal.py`.

**Interfaces:** `create_app(runtime=None, registry=None)` creates/accepts `AppRegistry`; `app.extensions["app_registry"]` exposes it to tests. All app lists, grant validation, service actions, and `/internal/auth/<app_id>` use registry records. Login `next` accepts only `/`, legacy app paths, and exact registered manifest origins. For manifest apps, `/internal/auth` expects original paths on that app origin and the app-specific Origin on writes.

- [ ] **Step 1: Write failing tests** for discovery in the admin page, no automatic grant, grant/revoke and app card origin, administrator Start/Stop dispatch, login redirect back to an exact app origin, and rejection of mismatched forwarded host/URI/Origin.
- [ ] **Step 2: Run** `.venv/bin/python -m unittest tests.test_portal -v`; expect failures.
- [ ] **Step 3: Implement** portal registry use, refresh on admin GET, and gateway sync. Preserve the existing unrelated `portal.py` worktree changes. Keep CSRF and audit behavior; add audit records for discovery/build/route failures.
- [ ] **Step 4: Run** `.venv/bin/python -m unittest tests.test_portal -v`; expect pass.
- [ ] **Step 5: Commit** only Task 4 changes, staging `portal.py` selectively if it still contains the user's unrelated edits.

### Task 5: End-to-end example and operator instructions

**Files:** Create `examples/weixin-go/app.yaml`, `examples/weixin-go/go.mod`, `examples/weixin-go/main.go`; modify `README.md`, `applications/README.md`, `INTEGRATION.md`, `integrations/README.md`; create `tests/test_manifest_example.py`.

**Interfaces:** The example uses `--port ${PORT}` and serves `/`, `/api/hello`, and `/static/app.css` without Bifrost-specific code. Documentation gives the minimal author contract, build tool prerequisite, start/stop flow, gateway reload setting, and external port range/firewall steps.

- [ ] **Step 1: Write a failing test** that copies `examples/weixin-go/` into a temporary `applications/weixin/` directory, loads its manifest, builds the Go program when `go` is present, starts it via `Controller`, requests all three paths directly, and stops it. Skip only the Go integration part if Go is unavailable.
- [ ] **Step 2: Run** `.venv/bin/python -m unittest tests.test_manifest_example -v`; expect failure while example files are absent.
- [ ] **Step 3: Add** the example and update the four docs to describe manifest-driven apps while retaining the legacy deployment instructions. State that application code is trusted and ordinary absolute web paths work because new apps have independent origins.
- [ ] **Step 4: Run** `.venv/bin/python -m unittest discover -s tests -v`; expect all tests to pass. Run `git diff --check` and inspect that `portal.py` and `static/` user changes remain intact.
- [ ] **Step 5: Commit** only Task 5 files.
