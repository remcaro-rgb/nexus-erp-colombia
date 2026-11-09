import io
import uuid
from pathlib import Path

import pytest
import yaml
from django.core.management import CommandError, call_command

from kernel import outbox, seed
from kernel.models import OutboxMessage


def _write(tmp_path: Path, data) -> Path:
    path = tmp_path / "demo.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.mark.django_db(transaction=True)
def test_seed_demo_runs_registered_loaders_in_one_uow(tmp_path):
    company = str(uuid.uuid4())
    path = _write(
        tmp_path,
        {
            "synthetic": True,
            "tenant_id": str(uuid.uuid4()),
            "seed_principal_id": str(uuid.uuid4()),
            "company_ids": [company],
            "parties": [{"code": "SYN-1"}, {"code": "SYN-2"}],
            "items": [],
        },
    )
    seen = []

    def load_parties(uow, rows):
        seen.append((uow.ctx.principal_type.value, uow.ctx.channel.value))
        uow.emit("onboarding.seed.loaded.v1", {"section": "parties", "rows": len(rows)})
        return len(rows)

    seed.register_loader("parties", load_parties, order=10)
    try:
        out = io.StringIO()
        call_command("seed_demo", file=str(path), stdout=out)
        text = out.getvalue()
        assert "parties: 2 rows" in text and "items: no loader registered" in text
        assert seen == [("service", "import")]
        assert OutboxMessage.objects.filter(event_type="onboarding.seed.loaded.v1").count() == 1
    finally:
        seed.unregister_loader("parties")


def test_seed_demo_rejects_missing_or_unsafe_files(tmp_path):
    with pytest.raises(CommandError, match="not found"):
        call_command("seed_demo", file=str(tmp_path / "missing.yaml"))
    not_synthetic = _write(tmp_path, {"tenant_id": str(uuid.uuid4()), "seed_principal_id": str(uuid.uuid4())})
    with pytest.raises(CommandError, match="synthetic"):
        call_command("seed_demo", file=str(not_synthetic))
    (tmp_path / "list.yaml").write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(CommandError, match="mapping"):
        call_command("seed_demo", file=str(tmp_path / "list.yaml"))
    lacking = _write(tmp_path, {"synthetic": True})
    with pytest.raises(CommandError, match="tenant_id"):
        call_command("seed_demo", file=str(lacking))


@pytest.mark.django_db(transaction=True)
def test_outbox_relay_command(ctx, monkeypatch):
    from kernel import uow

    with uow.unit_of_work(ctx) as u:
        u.emit("kernel.test.created.v1", {})
    sent = []

    class Pub:
        def publish(self, queue, envelope):
            sent.append(envelope["event_id"])

    monkeypatch.setattr(outbox, "CeleryPublisher", Pub)
    out = io.StringIO()
    call_command("outbox_relay", "--once", stdout=out)
    assert "published=1" in out.getvalue() and len(sent) == 1
    sleeps = []
    monkeypatch.setattr("kernel.management.commands.outbox_relay.time.sleep", lambda s: sleeps.append(s))
    call_command("outbox_relay", "--max-batches", "2", stdout=io.StringIO())
    assert sleeps == [1]
