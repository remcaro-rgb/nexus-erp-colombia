# Visual suite (UI-CONTRACT §9)

Screens are captured with the `visual` project of `../playwright.config.ts`: deterministic seed, frozen clock
(`support/fixtures.ts`), reduced motion, local fonts, es-CO/COP, 1440×900. Baselines live next to the specs
(`*.spec.ts-snapshots/`) and are approved once by the integrator (D-022); regressions are never silenced by
regenerating them. Specs arrive with the shell and module screens (P08 at G1, P22d at G4).
