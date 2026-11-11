"""Helpers that drive a whole import/export through ``dataio.service`` (the same path the API uses)."""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from dataio import exports, formats, service
from dataio.models import ImportJob
from dataio.service import run as run_in_unit_of_work
from kernel.tenancy import TenantContext


def csv_bytes(rows: Iterable[Mapping[str, Any]], headers: list[str] | None = None, delimiter: str = ",") -> bytes:
    rows = list(rows)
    headers = headers or list(dict.fromkeys(k for r in rows for k in r))
    out = io.StringIO()
    w = csv.writer(out, delimiter=delimiter, lineterminator="\n")
    w.writerow(headers)
    for r in rows:
        w.writerow(["" if r.get(h) is None else r.get(h) for h in headers])
    return out.getvalue().encode("utf-8")


def start(
    ctx: TenantContext, company: uuid.UUID, entity: str, data: bytes, filename: str = "datos.csv", **kw: Any
) -> ImportJob:
    def fn(u: Any) -> ImportJob:
        job = service.create_job(u, company, entity, **kw)
        return service.upload(u, job, filename, data)

    job: ImportJob = run_in_unit_of_work(fn, ctx)
    return job


def validate(ctx: TenantContext, company: uuid.UUID, entity: str, data: bytes, **kw: Any) -> ImportJob:
    job = start(ctx, company, entity, data, **kw)
    return service.dry_run(ctx, company, job.id)


def commit(
    ctx: TenantContext, company: uuid.UUID, entity: str, data: bytes, filename: str = "datos.csv", **kw: Any
) -> ImportJob:
    job = start(ctx, company, entity, data, filename, **kw)
    return service.commit(ctx, company, job.id)


def export(ctx: TenantContext, company: uuid.UUID, entity: str, fmt: str = "csv") -> bytes:
    def fn(u: Any) -> bytes:
        job = exports.start_export(u, exports.create_export(u, company, entity, fmt))
        return bytes(job.content or b"")

    out: bytes = run_in_unit_of_work(fn, ctx)
    return out


def table(data: bytes, filename: str = "x.csv") -> list[dict[str, str]]:
    t = formats.parse(filename, data)[0]
    return [dict(zip(t.headers, r, strict=False)) for r in t.rows]
