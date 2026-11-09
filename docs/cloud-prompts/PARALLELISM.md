# Parallel sessions — how many, and why (edition 3.0.0-claude-cloud)

**This is a planning estimate, not a measurement.** Nothing has been run in Claude Code cloud sessions yet. Weights are derived from the size
of each original prompt and the number of mandatory tests/features it carries; real durations will differ.

## Inputs

* 2.2.0-dev plan: 33 tasks (24 prompts + 5 gates + RC + DEV, P22 in 4 milestones), 28 mandatory features, 154 mandatory tests.
* Re-split for the cloud: P09 → P09a/P09b/P09c, P10 → P10a/P10b, P11 → P11a/P11b, P13 → P13a/P13b, P16 → P16a/P16b, P17 → P17a/P17b/P17c
  (disjoint owned paths; every 2.2.0 test and feature is owned by exactly one cloud task — checked by `cloudctl validate-plan`).
* Why these: they carry the heaviest feature/test load (P09: 26 tests, 6 features; P17: 16 tests; P11: 15; P10/P16: 11; P13: largest prompt).
  Splitting keeps each session to a size that fits one cloud session's context and time comfortably, and P13b/P16b run in parallel with their siblings.
* Weight of a worker task = 0.25 + (original prompt KB ÷ 9) × share + 0.05 × tests + 0.15 × owned features (1.0 ≈ one ordinary session).
* Integrator cost per wave = 0.3 + 0.12 × merged tasks + 0.25 × gates closed (it merges and re-runs tests serially; it is the bottleneck).

## Waves (dependency layers of the plan; a wave starts after the integrator accepted everything it needs)

| Wave | Tasks (weight) | Width | Integrator closes |
|---|---|---|---|
| 01 | P01 (1.35), P02 (1.14), P03 (1.05) | 3 | G0 |
| 02 | P04 (1.93), P05 (1.91), P07 (1.59), P08 (1.35), P06 (1.34), P22a (0.48) | 6 | G1 |
| 03 | P15 (1.67), P09a (1.44), P13b (1.33), P11a (1.23), P13a (1.15), P14 (0.97), P22b (0.48) | 7 | — |
| 04 | P10a (1.35), P11b (1.35), P09c (1.34), P09b (1.14) | 4 | — |
| 05 | P16a (1.62), P12 (1.46), P10b (0.93), P16b (0.59) | 4 | G2 |
| 06 | P18 (1.34), P22c (1.08), P17a (0.96) | 3 | — |
| 07 | P19 (1.67), P17b (1.03), P17c (0.78) | 3 | — |
| 08 | P21 (1.51), P20 (1.41) | 2 | G3 |
| 09 | P23 (1.36), P22d (0.98) | 2 | G4, RC |
| 10 | DEV (1.3) | 1 | — |

## Estimated elapsed time by number of concurrent sessions (k)

Per wave: tasks packed onto k sessions longest-first (LPT), plus the serial integrator. Units: session-units.

| k | estimated elapsed | vs k=∞ |
|---|---|---|
| 1 | 52.31 | +120% |
| 2 | 32.96 | +38% |
| 3 | 27.62 | +16% |
| 4 | 25.27 | +6% |
| 5 | 24.25 | +2% |
| 6 | 23.8 | +0% |
| 7 | 23.8 | +0% |
| 8 | 23.8 | +0% |
| 9 | 23.8 | +0% |
| 10 | 23.8 | +0% |

* Lower bound (unlimited sessions): 23.80. Total work (all sessions + integrator): 52.31 session-units — the same for any k;
  more parallel sessions only spend your plan's usage faster, they do not reduce it.
* Widest waves: W02=6, W03=7.
* **Recommendation: 5 concurrent worker sessions** (the smallest k within 5% of the unlimited-session estimate), plus **1 integrator session**
  that runs alone between waves (it never overlaps workers in this model, so the peak is 5 sessions).
* Earlier advice for the old 5-machine plan was ~7 (6 workers + 1 integrator running concurrently). With the larger 2.2.0 scope, the splits and the
  wave model, the honest number is 5 workers at peak; most waves need fewer (median width 3).
* If you hit plan usage limits (cloud sessions share your normal Claude limits), lower k: the runbook order still works — start the next prompt of
  the wave when a session finishes. A lower k only lengthens the waves wider than k.
* Integration is serial: about 8.7 session-units of the elapsed estimate are integrator runs that no extra worker session can shorten.

Heaviest task per wave (the chain that sets the elapsed time): P01 → P04 → P15 → P10a → P16a → P18 → P19 → P21 → P23 → DEV.
