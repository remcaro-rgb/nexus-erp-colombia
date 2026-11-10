# Frozen UI spec snapshot (owner P08)

Byte-identical copies of `docs/spec/ui/tokens.json` and `docs/spec/ui/module-registry.json` (frozen at G0).
The frontend build reads these copies through the `@spec/*` alias and the `virtual:nexus-tokens.css` plugin so that
`deploy/docker/frontend.Dockerfile` can build without `docs/` in its context (its `.dockerignore` excludes `docs`).

Never hand-edit. After the integrator changes `docs/spec/ui/*`, run `pnpm --dir frontend run sync-spec`.
`src/lib/spec/__tests__/spec-snapshot.unit.ts` fails `make ci` on any drift.
