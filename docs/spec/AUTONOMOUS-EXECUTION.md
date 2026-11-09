# AUTONOMOUS-EXECUTION — cloud edition (BINDING, 3.0.0-claude-cloud)

Sessions never ask the operator routine questions. They decide with DECISIONS-DEFAULTS.md and record the decision. The operator only pastes the
prompt files that `docs/run/STATUS.md` (from `docs/run/STATE.json`) names, one per new session, and pastes the integrator prompt after each wave.

## Named stops (the only reasons to stop and involve the operator)
| Stop line | Cause | Operator action |
|---|---|---|
| `BLOCKED-PRECONDITION <T>` | prompt pasted too early/twice, or task FAILED | paste what STATUS.md says |
| `BLOCKED-GIT <T>` / `BLOCKED-PUSH` | fetch/push refused after retries (GitHub App access removed, branch rule, outage) | restore access or remove the rule, then paste the RETRY / integrator prompt again |
| `BLOCKED-AUDIT` | a commit reached `main` without the integrator | inspect and revert it on GitHub, then paste the integrator prompt again |
| `BLOCKED <T>: <external input>` | officially unavailable external data or credentials | none needed for DEV (synthetic fixture path stays); provide the input only for production work (out of scope) |
| `STOP-OPERATOR` | a task failed 3 attempts | decide: accept a narrower scope via a change request, or retry after a fix |

Conflicts and failing tests are not stops: they return the task as REWORK with notes, and STATUS.md lists the RETRY prompt to paste. Completion is
not guaranteed; nothing is reported as working unless the integrator observed its tests pass. Never ask whether to continue.
