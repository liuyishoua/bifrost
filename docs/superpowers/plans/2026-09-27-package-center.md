# Application Package Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let an administrator choose an application and target in Bifrost, reuse a matching existing artifact, build a missing artifact, and download the result.

**Architecture:** An optional trusted `package.yaml` in each application declares target-specific build commands and output paths. A packaging controller validates recipes, tracks one build per app/target, caches successful output by source digest, and exposes status/log/download through admin-only portal routes. Go Weixin demonstrates real Windows and macOS builds. Android/iOS targets are visible, but without source/build tools/signing they report a concrete unavailable reason instead of fabricating packages.

**Tech Stack:** Python standard library, Flask, Go toolchain, unittest.

**Spec:** Approved in conversation: webpage packaging for each application and target, reuse existing package or build one if missing.

## Global Constraints

- Packaging does not stop or mutate a running business service or its data.
- Only administrator sessions may build or download packages.
- Build commands run only from trusted application manifests, never from request fields.
- The requested artifact must exist, be regular, and stay inside the application build directory.
- A successful cache hit requires unchanged source and recipe.

## Review Focus

- Path traversal in package manifests and download routes must not expose arbitrary files.
- Two simultaneous requests for one target must not start two builds.
- A failed build must not leave a stale downloadable artifact.
- User accounts must not build or download packages.
- Missing mobile build recipes must be reported honestly.

---

### Task 1: Packaging controller

**Files:** Create `app_packaging.py`; create `tests/test_app_packaging.py`.

- [ ] Write tests for target validation, cache hit, changed source rebuild, failure, traversal, and concurrent build.
- [ ] Run failing tests.
- [ ] Implement recipe parsing, background build, result metadata and log, and guarded artifact resolution.
- [ ] Run tests.

### Task 2: Portal UI and routes

**Files:** Modify `portal.py`, `static/portal.css`; modify `tests/test_portal.py`.

- [ ] Write failing admin permission and build/download route tests.
- [ ] Run failing tests.
- [ ] Add package center navigation/page, build POST, status and download GET routes, and audit records.
- [ ] Run tests.

### Task 3: Real sample build and docs

**Files:** Create `applications/weixin/package.yaml`, `applications/weixin/package_build.py`; modify `applications/weixin/main.go`, `README.md`, `INTEGRATION.md`.

- [ ] Add real macOS and Windows Go builds with a desktop entry that opens the local app.
- [ ] Build macOS artifact and Windows executable, inspect binary types, and launch the macOS sample.
- [ ] Document package recipes, target environment, and why mobile recipes are not present for server-only source.
- [ ] Run all platform tests and check diff.
