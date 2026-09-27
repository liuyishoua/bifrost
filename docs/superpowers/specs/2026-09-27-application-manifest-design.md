# Application discovery and lifecycle design

## Goal

An application author delivers a directory under `applications/` containing normal application code and one YAML file that states how to build and start it. Bifrost discovers the directory, builds it, shows it in the portal, and starts or stops it on an administrator's request. Authors do not edit Bifrost Python code or Caddy configuration and do not implement Bifrost-specific status endpoints by default.

Success is demonstrated by adding a small Go application directory and YAML file, refreshing the administration page, granting access, starting the application, visiting its normal `/`, `/api`, and `/static` paths, and stopping it without editing platform code.

## Current state and chosen approach

`apps.py`, both Caddyfiles, `portal.py`, and `service_control.py` name Ticket and Douyin statically. Their read-only adapters and the Douyin verification listener are established compatibility behavior.

Three approaches were considered:

1. **Directory manifest with platform-owned routing (chosen).** A directory scan creates validated application records. The platform generates gateway routes and controls the process. This matches the desired author workflow.
2. **Administrator form for commands and ports.** This duplicates the YAML file, introduces a second source of truth, and makes it harder to review a deployed application. A page is useful for viewing and controlling discovered applications, not for entering arbitrary commands.
3. **Keep static registry and routes.** This retains platform code changes for every application and does not meet the goal.

## Author contract

`applications/<id>/app.yaml` contains:

```yaml
schema: 1
id: weixin
name: 微信应用
description: 微信业务工作台
build: [go, build, -o, .bifrost/bin/weixin, .]
start: [.bifrost/bin/weixin, --port, "${PORT}"]
```

`build` is optional; `start` is required. Both are argument arrays executed directly, without a shell. `${PORT}` is replaced with a platform-assigned loopback port. A second optional `${DATA_DIR}` placeholder gives persistent application data a stable, Git-ignored directory. The author can otherwise write an ordinary HTTP application, including absolute `/api` and `/static` URLs. The process must listen on `127.0.0.1` and terminate on `SIGTERM`; applications with durable background work remain responsible for completing or preserving that work before exit.

The first version supports generic local executables and build tools, not a language-specific SDK. The platform does not inspect or modify application source. The deployment operator supplies the required build toolchain, such as Go.

## Discovery and validation

On portal startup and administration-page refresh, Bifrost scans immediate children of `applications/` for `app.yaml`. It reads YAML as data and validates a small schema: matching directory and ID, safe ID characters, bounded display text, nonempty argument arrays, and only documented placeholders. The `start` executable must resolve beneath the application directory; a build tool such as `go` may be found on the operator's `PATH`. It never invokes a shell. Invalid manifests appear as errors to administrators and are not available for grants, launch, or routing. One invalid directory does not prevent discovery of other valid applications.

Discovery never executes a build or starts a process. The administrator's **Start** action performs a build if configured, then launches the process. Build output and launch logs remain in the protected platform runtime directory; generated binaries and application data stay in ignored paths under the application directory. A bounded build timeout and clear error messages prevent a failed build from leaving the service in a misleading running state.

The registry is a coherent snapshot used by the portal, authorization, process controller, and route generator. Refresh installs valid records together and publishes invalid-record errors. A running application's manifest cannot be changed or removed through refresh; Bifrost retains its previous record until it is stopped, then permits replacement. Application IDs are stable grant keys.

## Process lifecycle

The platform assigns a stable unused internal port and persists the assignment under `.runtime/`. It substitutes that port into `start`, starts with the application directory as working directory, records process identity, and verifies that the expected process owns the listening port before reporting **Running**. A basic HTTP probe of `/` is informational; process and listener ownership determine generic readiness. A configured optional health path can provide stronger readiness when an application needs it.

Stop sends `SIGTERM` to the verified process and waits for exit and port release. If process ownership cannot be established, Bifrost refuses to signal an unrelated process. Background work is opaque to the platform. An application may optionally provide a status check that can block Stop while work is active. The existing Ticket and Douyin adapters keep their stricter activity checks. After a portal restart, process ownership is reconstructed from command, working directory, and listening port, preserving the existing conservative behavior.

## Routing and access

New applications receive a separate gateway origin so their own `/`, `/api`, and `/static` paths work unchanged. With the current IP-based deployment, Bifrost assigns each application an external HTTPS port from a configured range and generates a Caddy site for it. A future DNS deployment can map the same application registry to per-application hostnames without changing the author contract. The operator must open the chosen external port range in the firewall; this is a deployment concern, not an application-author task.

Both tracked Caddyfiles import a generated fragment from `.runtime/`. Portal startup writes the local and public fragments before Caddy starts, following the existing startup order. Refresh writes replacement fragments atomically, validates the active Caddyfile, then reloads Caddy using an operator-configured `BIFROST_CADDY_CONFIG` path. Generated routes run `forward_auth` before proxying, strip client-supplied identity and forwarding headers, and strip the portal session cookie before sending requests to the business process. The portal uses each application's registered origin when rendering its card, redirecting a user to login, validating a post-login destination, and checking the Origin of write requests. Authorization remains per request and fails closed when the portal is unavailable. A failed validation or reload restores the previous fragments and reports an administrator-visible error.

Ticket and Douyin continue using their existing `/ticket/` and `/douyin/` routes during migration. Their commands, data directories, health adapters, and Douyin verification listener are preserved. They are represented as platform-owned legacy records in the unified registry; new manifest applications require no additions to that list. A later migration can put their records into manifests after equivalent behavior is verified.

## Portal and administration

The administration page lists discovered applications, validation or build errors, current state, origin, and Start/Stop controls. Normal users see only granted applications. Grant forms populate from the registry rather than a module constant. Discovery adds an app without granting it to anyone. Removing an application does not silently reuse its ID or grants; an administrator explicitly removes stale grants after the process has stopped.

The existing account, CSRF, session, audit, and per-request grant checks continue to apply. Build, start, stop, route reload, and validation failures are audited. The page does not accept arbitrary command text; the reviewed YAML file is the source of truth.

## Verification

Tests cover schema rejection, directory discovery, stable port allocation, build and start failure, process ownership on stop, registry refresh while running, grants for discovered apps, login and write-origin checks for per-app origins, and generated gateway routes. A tiny fixture Go application proves the complete workflow using ordinary `/`, `/api`, and `/static` paths. Existing Ticket and Douyin tests must continue to pass. If Caddy is unavailable in the test environment, configuration generation is tested there and a manual Caddy reload test is documented for deployment.

## Scope boundary

This change covers trusted code placed on the server by the deployment operator. It does not provide source upload, package isolation, arbitrary public code execution, or per-user business data isolation. A web upload or app marketplace can be designed separately if needed.
