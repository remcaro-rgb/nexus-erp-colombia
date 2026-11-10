# Handoff FIX-IMG-W02 — critical zlib CVE in `erp-web` + local CI parity runs the image scan (v1)
Status: DONE · Attempt: 1 · Branch: `claude/new-session-u1ozj6` · Resolves CCR P08-7 (the integrator records it).

## Root cause
* The `web` stage base `docker.io/library/caddy:2.11.9-alpine@sha256:0bbb055b…` (Alpine 3.24.4) ships `zlib 1.3.2-r1`,
  CVE-2026-4738 (Critical, fixed in 1.4-r0). CI step `ci-parity.sh compose-startup --step image-scan` (grype
  `--fail-on critical --only-fixed`) fails as soon as a branch adds the frontend sources (P08), because only then is
  `erp-web` built.
* `ci-parity.sh` ran `image-scan` only when called by name, so `tools/parity_gate.py` (integrator gate, worker Step 5b)
  never scanned images: local parity ≠ CI.
* `active_profiles` enabled the `web` profile whenever an `erp-web:dev` image existed, even a stale one from another
  checkout (e.g. a trial merge) on a checkout without a frontend.

## Changes
* **IMG-1** `deploy/docker/frontend.Dockerfile` (web stage, as root, before `COPY`/`USER 10001:10001`):
  `apk add --no-cache --upgrade 'zlib>=1.4-r0'` with the optional BuildKit secret `proxy_ca` (same pattern as
  `backend.Dockerfile`; never stored in a layer). The minimum version makes the build fail rather than ship the vulnerable
  zlib. `CADDY_IMAGE` pin unchanged (no clean caddy tag exists, per the owner's measurement).
* **IMG-2** `scripts/ci/ci-parity.sh`: `step image-scan cs_sbom` is part of `compose-startup`'s default run (after
  `dr-drill`); `cs_sbom` scans only `erp-*` images labelled `org.opencontainers.image.revision=<HEAD short 12>` (set by
  `build-images.sh`), so stale images of other checkouts are never scanned or blamed. `.github/workflows/ci.yml` unchanged.
  **Addition beyond the prepared edit (decision, recorded here):** in cloud sessions behind the TLS-intercepting egress
  proxy, the grype container could not download its vulnerability DB (`x509: certificate signed by unknown authority`,
  `failed to load vulnerability db: database does not exist`), so the new default step would fail every cloud gate
  for environmental reasons. `cs_sbom` now mounts the proxy CA read-only into the grype container
  (`SSL_CERT_FILE`) — only when `HTTPS_PROXY`/`https_proxy` is set, with the same CA selection as `build-images.sh`
  (`NEXUS_BUILD_CA_FILE`, `SSL_CERT_FILE`, `/root/.ccr/ca-bundle.crt`). On GitHub CI (no proxy) nothing changes. Scan
  flags, `GRYPE_IMAGE` pin and image set are untouched; no ignore rules.
* **IMG-3** `scripts/ci/lib.sh` `active_profiles`: `web` only when `package.json`, `pnpm-lock.yaml` and
  `frontend/package.json` exist in this checkout.

## Verification (this session, Docker 29.9.2, pinned grype v0.121.2)
* shellcheck (`-S warning`, all P03 scripts): OK. Static T-P03-05 `dockerfiles_run_as_non_root`: 1 passed, exit 0.
* **A — branch parity** `bash scripts/ci/ci-parity.sh`: `CI-PARITY-OK 4/4`; `[ci-parity] step image-scan` ran inside
  compose-startup; grype on erp-api / erp-worker / erp-scheduler / erp-outbox-relay: no critical fixable finding (highest:
  High `zlib1g` deb CVE-2026-76844/85091 in the Debian backend base, not gating). First attempt failed only on the grype
  DB TLS issue above; fixed in `cs_sbom`, rerun green.
* **B — trial merge with P08** (`origin/claude/new-session-054ctn`, detached, clean merge, not committed/pushed):
  `ci-parity.sh compose-startup` → `CI-PARITY-OK 1/1`; `erp-web:dev` built through the proxy (revision label
  `bc1f140ffc6e`, user 10001:10001, 94 MB); `apk list -I zlib` → `zlib-1.4-r0 x86_64 {zlib} (Zlib) [installed]`; grype
  on `erp-web:dev`: only `golang.org/x/net` GO-2026-6860 High, `klauspost/compress` GO-2026-6865 High, `golang.org/x/net`
  GO-2026-6866 Medium (caddy binary) — 0 critical; CVE-2026-4738 absent from the log.
* **C — stale web image**: back on the fix branch with the trial's `erp-web:dev` present, `active_profiles` → `app` (no web).
* **GitHub Actions**: run 42744014191 on `72166f4` (latest code commit): policy, make ci, P03 tests, T-P03-01 compose
  (incl. image scan) all `success`. (On this branch alone CI builds no web image; trial B is the web proof.)
* Stack stopped with `make dev-down` (volumes kept).

## Residual risks / follow-ups
* The caddy binary's Go-module High/Medium findings (`golang.org/x/net`, `klauspost/compress`) remain (do not gate).
* The dev reverse proxy in `deploy/dev/compose.yaml` and `scripts/ci/tests/_support.py` uses the same caddy base; it is
  not an `erp-*` image and is not scanned (outside these paths).
* Drop the zlib line once upstream caddy alpine ships zlib ≥ 1.4 (no clean tag as of 2026-11-10).
* `build-images.sh --sbom`: syft logs non-fatal `failed to fetch latest version` TLS errors behind the proxy (update
  check only; SBOMs are written). Could get the same CA mount (P03 file outside these paths).
* `apk` run inside the final image tries to refresh its index at runtime (warning only); irrelevant to serving.
