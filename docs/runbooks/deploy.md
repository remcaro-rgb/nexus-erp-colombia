# Deploy (reference — no deployment happens in the DEV-only run)

1. Build from the accepted head: `ERP_IMAGE_TAG=rc-<sha12> scripts/ci/build-images.sh --sbom` (D-RC).
2. Scan: grype on every `erp-*` image, fail on critical; SBOM (CycloneDX) archived with the release.
3. Record image IDs/digests; the RC compose uses `erp-<service>:rc-<sha12>` and digest-pinned third-party images.
4. Staging: `docker compose -f docker-compose.yml -f docker-compose.prod.yml` with staging secrets; run the `migrate` job
   (expand phase only), start services, smoke test (`tests/e2e/dev_smoke.py` equivalent), then contract phase later.
5. Production: same images, blue/green or rolling (`api` replicas ≥ 2), health-gated. Blocked while EG gates are open.
