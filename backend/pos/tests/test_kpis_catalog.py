"""KPI provider ``/api/v1/kpis/pos`` (registry dashboard) and the catalog snapshot (ETag delta read-sync)."""

from __future__ import annotations

import pytest

from pos.testing.support import PosEnv, open_counter
from pos.tests.helpers import checkout_params
from sales.testing.support import ApiClient

pytestmark = pytest.mark.django_db


def test_pos_kpis_zero_denominator_and_values(pos_env: PosEnv, api: ApiClient) -> None:
    env = pos_env
    api.as_("supervisor")
    env.world.fakes.policy.grant(env.world.ctx("supervisor").principal_id, "pos.kpi.read")
    r = api.client.get("/api/v1/kpis/pos", {"period": "2026-11"})
    assert r.status_code == 200, r.content
    by_id = {k["id"]: k for k in r.json()["kpis"]}
    assert (
        by_id["ticket_promedio"]["value"] is None
        and by_id["ticket_promedio"]["empty_reason"] == "Sin base para calcular"
    )
    assert by_id["tiquetes"]["value"] == "0"
    session = open_counter(env)
    for qty in ("1", "3"):
        p = checkout_params(
            session, [{"item_id": str(env.items["soda"].id), "quantity": qty}], [{"type": "CASH", "amount": "20000"}]
        )
        api.as_("cashier")
        assert api.post("tickets:checkout", {"parameters": p}, key=p["client_uuid"]).status_code == 200
    api.as_("supervisor")
    by_id = {k["id"]: k for k in api.client.get("/api/v1/kpis/pos", {"period": "2026-11"}).json()["kpis"]}
    assert by_id["ventas_pos"]["value"] == "10000.00" and by_id["tiquetes"]["value"] == "2"
    assert by_id["ticket_promedio"]["value"] == "5000.00" and by_id["turnos_abiertos"]["value"] == "1"
    assert by_id["diferencias_arqueo"]["value"] == "0.00"
    panel = api.client.get("/api/v1/kpis/pos/panels/ventas_por_hora", {"period": "2026-11"}).json()
    assert panel["state"] == "ok" and panel["points"][0]["label"] == "10:00"  # 15:00 UTC = 10:00 Bogotá
    # the cashier has no pos.kpi.read: money KPIs and the panel are absent; counts of their own tickets only
    api.as_("cashier2")
    ids = [k["id"] for k in api.client.get("/api/v1/kpis/pos").json()["kpis"]]
    assert "ventas_pos" not in ids and "diferencias_arqueo" not in ids and "tiquetes" in ids
    assert api.client.get("/api/v1/kpis/pos/panels/ventas_por_hora").json()["state"] == "not_available"


def test_catalog_snapshot_etag_and_presentations(pos_env: PosEnv, api: ApiClient) -> None:
    env = pos_env
    open_counter(env)
    api.as_("cashier")
    r = api.get("catalog-snapshot", config_id=str(env.config.id))
    assert r.status_code == 200, r.content
    rows = {x["code"]: x for x in r.json()["rows"]}
    assert rows["GAS-350"]["price"] == "2500.000000" and rows["GAS-350"]["tax_code"] == "IVA19"
    assert {p["code"]: p["factor"] for p in rows["GAS-350"]["presentations"]} == {
        "CJ12": "12.000000",
        "SIX": "6.000000",
    }
    again = api.client.get(
        "/api/v1/pos/catalog-snapshot", {"config_id": str(env.config.id)}, HTTP_IF_NONE_MATCH=r["ETag"]
    )
    assert again.status_code == 304
