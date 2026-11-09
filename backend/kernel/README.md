# Kernel (P01) — how to use it

DEV-only edition. Everything here is imported by every module; read this before writing a service.
(The P01 prompt asks for `docs/kernel.md`; that path is outside P01's owned paths, so the
document lives here — see `contracts/change-requests/P01-2.md`.)

## Settings and runtime
* `erp.settings.dev` (runtime, default for `manage.py`), `erp.settings.test` (pytest), `erp.settings.prod` (refuses to boot:
  DEV-only edition; it still runs the production validations in `kernel.boot`).
* Env: `ERP_ENVIRONMENT` (`dev`|`test` only), `ERP_PUBLIC_ORIGIN` → `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS`,
  `DATABASE_URL` (default: unix-socket peer auth, role = OS user), `DJANGO_SECRET_KEY`, `ERP_CACHE_URL`, `ERP_BROKER_URL`,
  `ERP_INTEGRATION_<NAME>` (`DISABLED|FIXTURE_NOT_CERTIFIED|SANDBOX|LOCAL_CAPTURE`; anything else refuses to boot),
  `ERP_BRAND_NAME`, `ERP_BRAND_LOGO_PATH`, `ERP_GIT_SHA`, `ERP_REQUIRE_SAFE_DB_ROLE` (default true).
* Every new DB connection checks `pg_roles`: SUPERUSER or BYPASSRLS → `UnsafeDatabaseRole` (ADR-003).
* Anonymous endpoints (only these): `GET /api/health/live`, `GET /api/health/ready`, `GET /api/v1/system/environment`.
  `GET /api/v1/ui/context` requires authentication. DRF default permission is `IsAuthenticated`, no authentication classes
  are configured until P04 adds them → everything else is denied.
* `make db-setup` creates a LOGIN CREATEDB **non-superuser** role for the OS user (`kernel.devdb`); no password exists.

## Money and precision (`kernel.money`, `kernel.fields`)
```python
from decimal import Decimal
from kernel.money import Money, allocate, round_half_even, q_amount

total = Money.of("1190000.00", "COP")
parts = total.allocate([Decimal(1), Decimal(2)])   # largest remainder, ties → lower index, sum preserved
Money.of(1, "COP") + Money.of(1, "USD")            # MoneyError (mixed currencies)
Money(0.1, "COP")                                   # MoneyError (floats refused)
```
Model fields: `AmountField` (20,4), `QtyField` (20,6), `UnitCostField` (24,8), `FxRateField` (24,10), `PctField` (9,6),
`CurrencyField`. `make lint` runs `kernel.lint.no_float` (ERP001 `float(`, ERP002 `FloatField`, ERP003 float literal on a
money-named target; opt-out only with `# erp: allow-float(<reason>)`), and ruff bans `django.db.models.FloatField`.

## Ids, clock, dates
* `kernel.ids.new_id()` — UUIDv7, strictly increasing per process; `kernel.fields.uuid7_pk()` or inherit `TenantModel`.
* `kernel.clock.now()` (UTC, injectable; `with clock.frozen(dt): ...` in tests), `clock.business_date(tz)` (America/Bogota default).
* `kernel.dates.DateRange`, `is_effective`, `effective_q(on)` and `overlapping_q(range)` for `valid_from/valid_to` (both inclusive,
  `None` = open).

## Tenancy (`kernel.tenancy`)
```python
ctx = TenantContext(tenant_id, frozenset({company_id}), principal_id,
                    principal_type=PrincipalType.USER, channel=Channel.API)
with tenancy.scope(ctx):          # read scope: atomic + SET LOCAL app.* settings
    Invoice.objects.filter(...)
```
`bind(ctx)` sets `app.tenant_id`, `app.company_ids` (`{uuid,...}`), `app.principal_id`, `app.principal_type`,
`app.on_behalf_of`, `app.channel` with `set_config(..., true)` — they vanish at COMMIT/ROLLBACK, so pooled connections never
leak a binding (T-P01-10). AI/MCP principals must carry `on_behalf_of`. `TenancyMiddleware` activates `request.erp_context`
(set by P04 authentication) or `ERP_TENANT_CONTEXT_RESOLVER`; Celery tasks use `base=kernel.tasks.TenantTask` and are called
with `**TenantTask.context_kwargs(ctx)`. `kernel.db.rls_policy_sql(table)` is a reference policy for P04.

## Unit of work (`kernel.uow`) — every write
```python
from kernel.uow import run_in_unit_of_work

def create_invoice(uow, data):
    number = sequences.allocate(data.company_id, "FV", "SETP")   # gap-free, inside the UoW only
    inv = Invoice.objects.create(...)
    services.get(PostingService).post(uow, posting_request)       # same transaction (C1-1)
    uow.emit("sales.invoice.issued.v1", {"invoice_id": str(inv.id)}, company_id=data.company_id)
    uow.set_response({"id": str(inv.id)}, 201)                    # stored for Idempotency-Key replays
    return inv

result = run_in_unit_of_work(lambda u: create_invoice(u, data), ctx,
                             idempotency_key=request.headers["Idempotency-Key"], request=payload,
                             scope="sales.invoice.create")
# result is the function's return value, or a StoredResponse(body, status_code) on replay
```
* Retries SQLSTATE 40001/40P01 up to 3 times with jitter (T-P01-07). Nested `unit_of_work` joins the outer one.
* Idempotency: `kernel_idempotency` unique `(tenant_id, key)`; same key + same body → stored response; different body →
  `IdempotencyKeyReused` (422); concurrent same key → one execution (the second waits on the unique index, then replays).
* Events are validated (`<module>.<entity>.<verb>.v<N>`, no floats) and written to `kernel_outbox` before commit.
* `requests`/`httpx` raise `ExternalIOForbidden` while a UoW is active; use `uow.on_commit(fn)` or the outbox.
* `@transactional(scope=...)` decorator wraps `def svc(uow, ...)` as `svc(ctx, ..., idempotency_key=..., request=...)`.

## Outbox (`kernel.outbox`)
`manage.py outbox_relay [--once] [--batch-size N]` publishes PENDING rows (`FOR UPDATE SKIP LOCKED`) to Celery task
`kernel.dispatch_event` on queue `einvoice|notifications|reporting|ai|default` (`ERP_OUTBOX_ROUTES` by module prefix).
Failures back off exponentially; after `ERP_OUTBOX_MAX_ATTEMPTS` the row is DEAD with a `kernel_dead_letter` row.
Consumers: `outbox.register_consumer("p07.notify", "workflow.*", handler)`; delivery dedupes on `kernel_inbox(consumer,
event_id)` in the handler's transaction; handler exceptions become CONSUMER dead letters (`replay_dead_letter`).
`outbox.metrics()` returns counters + backlog.

## Sequences (`kernel.sequences`)
`Sequence` rows per `(tenant, company, sequence_code, prefix)` with `padding`, `range_from/to`, `valid_from/to`,
`resolution_number`, `technical_key` (DIAN data is external, entered by the tenant — never invented). `allocate()` requires a
UoW, locks the row, refuses inactive/out-of-validity (`sequence_not_valid`) and exhausted (`sequence_exhausted`) ranges,
and emits `kernel.sequence.threshold.v1` at 80 %/95 %. Rollbacks return the number → no gaps (T-P01-06, 50 threads).
`allocate_terminal_block` (ADR-015) always fails closed in this edition.

## Models (`kernel.models`, `kernel.db`)
`TenantModel`, `CompanyModel`, `AuditedModel` (`version`, `etag`, `check_if_match`, `save_versioned` → 412/428/409),
`ImmutableModel` (`posted`; app guard) + migration op `kernel.db.immutable_trigger(table, posted_column)` (DB trigger;
`posted_column=None` → append-only). `kernel.db.company_fk_sql(table)` adds the composite FK to `iam_company(tenant_id, id)`.

## Errors (`kernel.errors`)
`DomainError` subclasses carry `code`/`status`; `drf_exception_handler` renders RFC 9457 `application/problem+json`
(`type, title, status, detail, code, instance, trace_id, errors`). A module code on a kernel class:
`Conflict("…", code="period_closed")`. `kernel_error_codes()` lists the kernel registry (CCR P01-1 for `contracts/errors.yaml`).

## Flags, parameters, dimensions, cache, services
* `kernel.flags.register(code, kind="A")` (A features default off); `is_enabled(code, tenant, company)` = company override >
  tenant override > default. `pos_offline_fiscal_selling` is registered (off).
* `kernel.parameters`: `ParameterProvider.get(code, at_date, company)`; `enforce()` → `ParameterUnknown` (UNKNOWN/missing),
  `ParameterNotVerified` (production without legal-review stamp); DISPUTED/synthetic → `requires_warning` + `snapshot()`.
* `kernel.dimensions.slots()` — `cost_center_id`, `business_unit_id`, `aux_abierto_id`, `item_dimension_id` (company-scoped).
* `kernel.cache.ui_cache_key(ns, ctx, company_id=, period_id=, perm_hash=)`; `on_context_switch` / `on_logout` invalidate.
* `kernel.services.register(Interface, impl)` / `get(Interface)` (fails closed with `service_unavailable`).

## Interfaces (`kernel.interfaces`, v1.0.0-draft, frozen at G1)
`PostingService, StockService, Inventory, TaxService, PolicyService, AuditService, WorkflowService, SchedulerService,
FiscalGateway, FiscalDocuments, DocumentRenderer, Labels, ReportQueryService, NotificationService (= Notifications),
FileStore, ParameterProvider, PeriodService, Dimensions, ArOpenItems, PaymentStatus, PayrollPayables` — all
`@runtime_checkable` Protocols with docstring contracts. Test doubles: `kernel.testing.fakes.Fake*`.

## Demo tenant
`manage.py seed_demo [--file …]` reads `backend/kernel/fixtures/demo_tenant.yaml` (P21 data; must declare
`synthetic: true`, `tenant_id`, `seed_principal_id`, `company_ids`) and runs loaders registered with
`kernel.seed.register_loader(section, fn, order)` in one UoW as a service principal on channel `import`.

## Tests
`make ci` (ruff, ruff format, no-float, mypy strict kernel, makemigrations --check, pytest + coverage, contract, frontend);
`make test-ids IDS="T-P01-05"`. Shared pytest plugin: `-p kernel.testing.pytest_plugin` (fixtures `tenant_ctx`, `frozen_clock`;
Hypothesis without on-disk database).
