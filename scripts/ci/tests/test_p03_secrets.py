"""Dev secrets generation (D-P03, ADR-012) and secret scanning. Owner: P03."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _support import CI, REPO, load

secret_scan = load("secret_scan")
GEN = REPO / "deploy" / "dev" / "gen-secrets.sh"


def parse(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v
    return out


class GenSecretsTest(unittest.TestCase):
    def test_T_P03_SEC_gen_secrets_mode_0600_idempotent_and_never_prints_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdir = Path(tmp) / "secrets"
            env = dict(os.environ, ERP_SECRETS_DIR=str(sdir))
            first = subprocess.run(["bash", str(GEN)], capture_output=True, text=True, env=env, check=True)
            dev, cred = sdir / "dev.env", sdir / "dev-credentials.env"
            for f in (dev, cred):
                self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600, f)
            self.assertEqual(stat.S_IMODE(sdir.stat().st_mode), 0o700)
            values = parse(dev) | parse(cred)
            self.assertIn("ERP_SMOKE_CLIENT_SECRET", values)
            self.assertIn("ERP_DEMO_PASSWORD_VENDEDOR_DEMO", values)
            self.assertEqual(len(set(values.values())), len(values), "values must be unique")
            for key, value in values.items():
                self.assertGreaterEqual(len(value), 24, key)
                self.assertNotIn(value, first.stdout + first.stderr)
            second = subprocess.run(["bash", str(GEN)], capture_output=True, text=True, env=env, check=True)
            self.assertEqual(parse(dev) | parse(cred), values, "existing values must never be overwritten")
            self.assertNotIn("created", second.stdout)

    def test_FIX_D_W01_1_field_encryption_key_file_0600_never_overwritten(self):
        import base64

        with tempfile.TemporaryDirectory() as tmp:
            sdir = Path(tmp) / "secrets"
            env = dict(os.environ, ERP_SECRETS_DIR=str(sdir))
            first = subprocess.run(["bash", str(GEN)], capture_output=True, text=True, env=env, check=True)
            key_file = sdir / "field-encryption.key"
            self.assertEqual(stat.S_IMODE(key_file.stat().st_mode), 0o600)
            key = key_file.read_text(encoding="utf-8").strip()
            self.assertEqual(len(base64.urlsafe_b64decode(key)), 32)
            self.assertNotIn(key, first.stdout + first.stderr)
            subprocess.run(["bash", str(GEN)], capture_output=True, text=True, env=env, check=True)
            self.assertEqual(key_file.read_text(encoding="utf-8").strip(), key, "the key must never be overwritten")
        with tempfile.TemporaryDirectory() as tmp:  # a key from an older dev.env is kept (data stays readable)
            sdir = Path(tmp) / "secrets"
            sdir.mkdir(mode=0o700)
            legacy = base64.urlsafe_b64encode(os.urandom(32)).decode()
            (sdir / "dev.env").write_text("ERP_FIELD_ENCRYPTION_" + "KEY=" + legacy + "\n", encoding="utf-8")
            env = dict(os.environ, ERP_SECRETS_DIR=str(sdir))
            subprocess.run(["bash", str(GEN)], capture_output=True, text=True, env=env, check=True)
            self.assertEqual((sdir / "field-encryption.key").read_text(encoding="utf-8").strip(), legacy)

    def test_T_P03_SEC_secrets_dir_is_gitignored(self):
        proc = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", "deploy/dev/secrets/dev.env"])
        self.assertEqual(proc.returncode, 0)


class SecretScanTest(unittest.TestCase):
    def test_T_P03_SEC_scan_flags_tokens_and_tracked_secret_files_without_printing_values(self):
        token = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
        with tempfile.TemporaryDirectory(dir=REPO / "scripts" / "ci" / "tests") as tmp:
            leak = Path(tmp) / "leak.txt"
            leak.write_text(f"token = '{token}'\npassword = 'example-not-a-secret'\n", encoding="utf-8")
            rel = str(leak.relative_to(REPO))
            hits = secret_scan.scan([rel, "deploy/dev/secrets/dev.env"])
        self.assertTrue(any(h.endswith(":1: github-token") for h in hits), hits)
        self.assertFalse(any(":2:" in h for h in hits), hits)
        self.assertTrue(any("inside deploy/dev/secrets" in h for h in hits), hits)
        self.assertFalse(any(token in h for h in hits))

    def test_T_P03_SEC_repository_has_no_tracked_secrets(self):
        proc = subprocess.run([sys.executable, str(CI / "secret_scan.py")], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
