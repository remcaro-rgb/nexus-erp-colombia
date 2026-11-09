#!/usr/bin/env python3
"""dev_login — throwaway DEV login for the integrator's UI preview (edition 3.0.3-claude-cloud). Integrator tooling; DEV stack only.

  python3 tools/dev_login.py ensure      -> one line `DEV-LOGIN OK user=ui-preview file=deploy/dev/secrets/ui-preview.env origin=…`
                                             or `DEV-LOGIN FAILED: <reason>` (exit 1); never prints the password

Until P21's synthetic demo tenant exists there is no user who can sign in, so tools/ui_snapshot.py calls ensure() when it finds no login:
  1. Refuses unless the stack is the local DEV stack: ERP_PUBLIC_ORIGIN (deploy/dev/runtime.env) must be http on localhost / 127.0.0.1 / ::1,
     ERP_ENVIRONMENT (if set) must be dev, and the Keycloak bootstrap-admin password must be in deploy/dev/secrets/dev.env (gen-secrets.sh).
  2. Keycloak (realm erp-dev, through the same-origin proxy at <origin>/auth): creates or updates the user `ui-preview` — enabled, e-mail
     verified, no required actions, group erp-users, attribute tenant_id = a fixed synthetic UUID — and sets a NEW random password on every
     run (32 url-safe chars; the realm's password history forbids reuse). The password is written only to deploy/dev/secrets/ui-preview.env
     (gitignored, created 0600 under umask 077) as NEXUS_UI_PREVIEW_USER / NEXUS_UI_PREVIEW_PASSWORD.
  3. Checks Keycloak kept tenant_id (Keycloak 26 drops attributes its user profile does not declare; the realm template must declare it).
  4. ERP side: `manage.py dev_ui_principal` in a one-off `seed` container (DEV-only command, FIX-CI-W02) creates the synthetic tenant,
     company, internal user bound to the Keycloak subject, membership and a read-only role. Idempotent.
The admin token, the password and the dev.env values stay in this process; no output line contains them.
"""
import json, os, re, secrets, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request, uuid

ROOT = os.environ.get("NEXUS_ROOT") or subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip() or os.getcwd()
SECRETS_DIR = os.environ.get("ERP_SECRETS_DIR") or os.path.join(ROOT, "deploy", "dev", "secrets")
CRED_FILE = os.path.join(SECRETS_DIR, "ui-preview.env")
REALM = "erp-dev"
USERNAME = "ui-preview"
GROUP = "erp-users"
TENANT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "https://nexus-erp.invalid/dev/ui-preview-tenant"))
LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}


class LoginError(Exception):
    pass


def read_env(path):
    vals = {}
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8", errors="replace"):
            m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                vals[m.group(1)] = m.group(2).strip("'\"")
    return vals


def public_origin():
    rt = read_env(os.environ.get("NEXUS_RUNTIME_ENV") or os.path.join(ROOT, "deploy", "dev", "runtime.env"))
    origin = os.environ.get("ERP_PUBLIC_ORIGIN") or rt.get("ERP_PUBLIC_ORIGIN") or f"http://localhost:{rt.get('ERP_DEV_PORT', '8080')}"
    u = urllib.parse.urlsplit(origin)
    if u.scheme != "http" or (u.hostname or "") not in LOOPBACK:
        raise LoginError(f"ERP_PUBLIC_ORIGIN {origin!r} is not a loopback http origin; dev_login only runs against the local DEV stack")
    return origin.rstrip("/")


def admin_username():
    try:
        m = re.search(r"KC_BOOTSTRAP_ADMIN_USERNAME:\s*([A-Za-z0-9._-]+)", open(os.path.join(ROOT, "deploy", "dev", "compose.yaml")).read())
        return m.group(1) if m else "nexus-dev-admin"
    except OSError:
        return "nexus-dev-admin"


def http(method, url, token=None, data=None, form=None, timeout=20):
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = "Bearer " + token
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else None), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, None, dict(e.headers or {})
    except (urllib.error.URLError, OSError) as e:
        raise LoginError(f"{method} {urllib.parse.urlsplit(url).path} unreachable ({type(e).__name__})")


def write_secret_file(path, password):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    old = os.umask(0o077)
    try:
        fd, tmp = tempfile.mkstemp(prefix=".ui-preview.", dir=os.path.dirname(path))
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("# Nexus ERP DEV — throwaway UI-preview login created by tools/dev_login.py (synthetic tenant, DEV stack only).\n"
                    f"# Rotated on every run ({time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}). Gitignored. Never commit, print or paste.\n"
                    f"NEXUS_UI_PREVIEW_USER={USERNAME}\nNEXUS_UI_PREVIEW_PASSWORD={password}\n")
    finally:
        os.umask(old)
    return tmp


def erp_principal(subject):
    """Bind the Keycloak subject to an ERP internal user of the synthetic tenant (manage.py dev_ui_principal, DEV-only)."""
    override = os.environ.get("NEXUS_DEV_LOGIN_ERP_CMD")
    if override and not os.environ.get("NEXUS_REHEARSAL"):
        raise LoginError("NEXUS_DEV_LOGIN_ERP_CMD is only allowed in the local rehearsal")
    script = override or ('. scripts/ci/lib.sh && load_runtime_env && compose_profiles_env && '
                          'dc run --rm --no-deps seed python manage.py dev_ui_principal --subject "$1" --username "$2" --tenant-id "$3"')
    p = subprocess.run(["bash", "-c", script, "_", subject, USERNAME, TENANT_ID], cwd=ROOT, capture_output=True, text=True, timeout=600)
    out = p.stdout + p.stderr
    ok = re.search(r"(?m)^DEV-UI-PRINCIPAL OK\b[^\n]*", out)
    if p.returncode == 0 and ok:
        return ok.group(0)[:160]
    if re.search(r"Unknown command: '?dev_ui_principal", out):
        raise LoginError("backend command dev_ui_principal missing (FIX-CI-W02 not merged)")
    last = [l for l in out.strip().splitlines() if l.strip()][-1:] or ["no output"]
    raise LoginError(f"manage.py dev_ui_principal failed (exit {p.returncode}): {last[0][:140]}")


def ensure():
    """Returns {'user', 'password', 'origin', 'file', 'principal'}; raises LoginError with a printable reason."""
    if os.environ.get("ERP_ENVIRONMENT", "dev") != "dev":
        raise LoginError("ERP_ENVIRONMENT is not dev")
    origin = public_origin()
    dev = read_env(os.path.join(SECRETS_DIR, "dev.env"))
    admin_pw = dev.get("ERP_KEYCLOAK_ADMIN_PASSWORD")
    if not admin_pw:
        raise LoginError(f"{os.path.relpath(SECRETS_DIR, ROOT)}/dev.env has no Keycloak admin password (run make dev-up)")
    st, _, _ = http("GET", f"{origin}/auth/realms/{REALM}/.well-known/openid-configuration")
    if st != 200:
        raise LoginError(f"Keycloak realm {REALM} not reachable at {origin}/auth (HTTP {st})")
    st, tok, _ = http("POST", f"{origin}/auth/realms/master/protocol/openid-connect/token",
                      form={"client_id": "admin-cli", "grant_type": "password", "username": admin_username(), "password": admin_pw})
    if st != 200 or not tok or "access_token" not in tok:
        raise LoginError(f"Keycloak admin login refused (HTTP {st})")
    token = tok["access_token"]
    api = f"{origin}/auth/admin/realms/{REALM}"
    rep = {"username": USERNAME, "enabled": True, "emailVerified": True, "email": "ui-preview@example.invalid",
           "firstName": "Vista previa", "lastName": "UI (sintético)", "requiredActions": [], "attributes": {"tenant_id": [TENANT_ID]}}
    st, users, _ = http("GET", f"{api}/users?" + urllib.parse.urlencode({"username": USERNAME, "exact": "true"}), token)
    if st != 200:
        raise LoginError(f"Keycloak user lookup failed (HTTP {st})")
    if users:
        uid = users[0]["id"]
        st, _, _ = http("PUT", f"{api}/users/{uid}", token, rep)
        if st not in (200, 204):
            raise LoginError(f"Keycloak user update failed (HTTP {st})")
    else:
        st, _, hdr = http("POST", f"{api}/users", token, rep)
        if st != 201:
            raise LoginError(f"Keycloak user creation failed (HTTP {st})")
        loc = hdr.get("Location") or hdr.get("location") or ""
        uid = loc.rstrip("/").rsplit("/", 1)[-1]
        st, _, _ = http("PUT", f"{api}/users/{uid}", token, rep)  # clears any default required action Keycloak added on creation
        if st not in (200, 204):
            raise LoginError(f"Keycloak user update after creation failed (HTTP {st})")
    st, user, _ = http("GET", f"{api}/users/{uid}", token)
    if st != 200 or not user:
        raise LoginError(f"Keycloak user read-back failed (HTTP {st})")
    if (user.get("attributes") or {}).get("tenant_id") != [TENANT_ID]:
        raise LoginError("Keycloak dropped the tenant_id user attribute: the erp-dev realm user profile must declare it "
                         "(deploy/keycloak/realm-template.json, FIX-CI-W02 UI-2)")
    st, groups, _ = http("GET", f"{api}/groups?" + urllib.parse.urlencode({"search": GROUP, "exact": "true"}), token)
    gid = next((g["id"] for g in (groups or []) if g.get("name") == GROUP), None)
    if not gid:
        raise LoginError(f"group {GROUP} missing in realm {REALM}")
    st, _, _ = http("PUT", f"{api}/users/{uid}/groups/{gid}", token)
    if st not in (200, 204):
        raise LoginError(f"adding {USERNAME} to {GROUP} failed (HTTP {st})")
    password = secrets.token_urlsafe(24)
    tmp = write_secret_file(CRED_FILE, password)
    try:
        st, _, _ = http("PUT", f"{api}/users/{uid}/reset-password", token, {"type": "password", "value": password, "temporary": False})
        if st not in (200, 204):
            raise LoginError(f"setting the throwaway password failed (HTTP {st}; realm password policy?)")
        os.replace(tmp, CRED_FILE)
        os.chmod(CRED_FILE, 0o600)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    principal = erp_principal(uid)
    return {"user": USERNAME, "password": password, "origin": origin, "file": os.path.relpath(CRED_FILE, ROOT), "principal": principal}


def main():
    if sys.argv[1:] != ["ensure"]:
        print(__doc__.split("\n\n")[1])
        return 2
    try:
        r = ensure()
    except LoginError as e:
        print(f"DEV-LOGIN FAILED: {e}")
        return 1
    print(f"DEV-LOGIN OK user={r['user']} file={r['file']} origin={r['origin']} ({r['principal']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
