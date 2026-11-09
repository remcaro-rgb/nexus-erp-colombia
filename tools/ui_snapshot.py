#!/usr/bin/env python3
"""ui_snapshot — UI progress screenshots for the integrator (edition 3.0.3-claude-cloud). Integrator-only; workers never run or edit it.

  python3 tools/ui_snapshot.py --wave N [--base-url URL] [--commit]

* Captures, with Playwright + the system Chrome ($CHROME_PATH; never downloads a browser), at 1440x900:
  the login screen and home dashboard, the main page of every module of docs/spec/ui/module-registry.json whose owner task is ACCEPTED
  in docs/run/STATE.json and whose route really renders (others are listed as "not yet built", never faked), in light AND dark theme,
  plus the dashboard with the sidebar and the AI panel collapsed.
* Synthetic seeded data only. The demo login is read from the gitignored secrets (deploy/dev/secrets/) or NEXUS_UI_USER/NEXUS_UI_PASSWORD;
  when there is none (before P21's demo tenant) tools/dev_login.py bootstraps a throwaway DEV user `ui-preview` with a fresh random password
  stored only in deploy/dev/secrets/ui-preview.env (0600). The password is passed to the browser script on stdin and never printed.
* Base URL: ERP_PUBLIC_ORIGIN of deploy/dev/runtime.env (the origin Keycloak redirects back to; loopback only), else 127.0.0.1:8080-8090. Password/token/secret fields are masked in every image. No devtools.
* Writes docs/ui-progress/WNN/*.png (+ INDEX.md) and regenerates docs/ui-progress/README.md (waves newest first). PNGs are recompressed;
  if the wave exceeds 8 MB the shots are retaken at a lower device scale (0.75, then 0.5), then the largest are dropped and listed.
* Never blocks integration: always exits 0. Last line: `UI-SNAPSHOT: OK …` or `UI-SNAPSHOT: FAILED <reason>`.
* --commit: commits docs/ui-progress on the integration branch nexus-integrate-wNN with the integrator trailer (audit-main accepts it).
"""
import argparse, base64, datetime as dt, glob, json, os, re, shutil, struct, subprocess, sys, tempfile, urllib.request, zlib

CAP_BYTES = 8 * 1024 * 1024
PW_VERSION = "1.65.0"   # playwright-core, pinned; browser = system Chrome
ROOT = os.environ.get("NEXUS_ROOT") or subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip() or os.getcwd()
OUTDIR = os.path.join(ROOT, "docs", "ui-progress")
INTEG_TRAILER = "Nexus-Integrator:"
STUB_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC")


def sh(*a, **k):
    return subprocess.run(list(a), cwd=ROOT, capture_output=True, text=True, **k)


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ------------------------------------------------------------------ PNG recompression (stdlib only)
def optimize_png(path):
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return
    pos, keep, idat = 8, [], b""
    while pos < len(data):
        ln, = struct.unpack(">I", data[pos:pos + 4]); typ = data[pos + 4:pos + 8]; body = data[pos + 8:pos + 8 + ln]; pos += 12 + ln
        if typ == b"IDAT":
            idat += body
        elif typ in (b"IHDR", b"PLTE", b"tRNS"):
            keep.append((typ, body))
    try:
        raw = zlib.decompress(idat)
    except zlib.error:
        return
    chunk = lambda t, b: struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xffffffff)
    out = b"\x89PNG\r\n\x1a\n" + b"".join(chunk(t, b) for t, b in keep) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    if len(out) < len(data):
        open(path, "wb").write(out)


# ------------------------------------------------------------------ inputs
def pages_for_wave():
    reg = json.load(open(os.path.join(ROOT, "docs/spec/ui/module-registry.json"), encoding="utf-8"))
    plan = json.load(open(os.path.join(ROOT, "docs/run/plan-cloud.json"), encoding="utf-8"))
    st = json.load(open(os.path.join(ROOT, "docs/run/STATE.json"), encoding="utf-8"))
    accepted = {t for t, s in st["tasks"].items() if s["status"] == "ACCEPTED"}
    by_src = {}
    for tid, t in plan["tasks"].items():
        for s in t.get("source", []):
            by_src.setdefault(s, []).append(tid)
    pages, skipped = [], []
    for m in reg["modules"]:
        parts = by_src.get(m["owner_task"], [m["owner_task"]])
        done = [p for p in parts if p in accepted]
        if done:
            pages.append({"id": m["id"], "label": m["label"], "route": m["route"], "owner": m["owner_task"], "parts_accepted": done})
        else:
            skipped.append({"id": m["id"], "label": m["label"], "route": m["route"], "status": f"not yet built (owner {m['owner_task']} not accepted)"})
    for p in reg.get("portals", []):
        skipped.append({"id": p["id"], "label": p["label"], "route": p["route"], "status": "not captured: portal pages need an external portal account"})
    return pages, skipped


def find_login():
    if os.environ.get("NEXUS_UI_USER") and os.environ.get("NEXUS_UI_PASSWORD"):
        return {"user": os.environ["NEXUS_UI_USER"], "password": os.environ["NEXUS_UI_PASSWORD"]}, "environment"
    cands = []
    for f in sorted(glob.glob(os.path.join(ROOT, "deploy/dev/secrets/*"))):
        base = os.path.basename(f)
        if base in (".gitkeep", "README.md", "ui-preview.env") or base.startswith(".") or not os.path.isfile(f):  # ui-preview: dev_login re-syncs it
            continue
        try:
            txt = open(f, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        try:
            def walk(o):
                if isinstance(o, dict):
                    u = next((o[k] for k in ("username", "user", "login", "email") if isinstance(o.get(k), str)), None)
                    p = next((o[k] for k in ("password", "pass", "secret") if isinstance(o.get(k), str)), None)
                    if u and p:
                        cands.append((json.dumps(o).lower(), u, p, base))
                    for v in o.values():
                        walk(v)
                elif isinstance(o, list):
                    for v in o:
                        walk(v)
            walk(json.loads(txt))
            continue
        except ValueError:
            pass
        kv = dict(re.findall(r"(?m)^\s*([A-Za-z0-9_]+)\s*[=:]\s*['\"]?([^'\"\n]+?)['\"]?\s*$", txt))
        for k, v in kv.items():
            m = re.match(r"(.*?)(?:_)?(USER|USERNAME|LOGIN|EMAIL)$", k, re.I)
            if m:
                for pk in (m.group(1) + "_PASSWORD", m.group(1) + "PASSWORD", m.group(1) + "_PASS"):
                    if pk in kv:
                        cands.append((k.lower(), v, kv[pk], base))
    if not cands:
        return None, "no demo login found in deploy/dev/secrets/ (gitignored)"
    cands.sort(key=lambda c: (0 if "admin" in c[0] else 1 if "contador" in c[0] or "account" in c[0] else 2))
    return {"user": cands[0][1], "password": cands[0][2]}, f"deploy/dev/secrets/{cands[0][3]}"


def dev_login_module():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import dev_login  # noqa: E402  (3.0.3 integrator tooling, same directory)
    return dev_login


def find_base_url(given):
    urls = [given] if given else []
    if not given:
        try:
            urls.append(dev_login_module().public_origin())  # the OIDC redirect origin: cookies and redirect_uri must match it
        except Exception:
            pass
        urls += [f"http://127.0.0.1:{p}" for p in [int(os.environ.get("ERP_DEV_PORT", 8080))] + list(range(8080, 8091))]
    for u in dict.fromkeys(urls):
        try:
            urllib.request.urlopen(u + "/", timeout=5)
            return u
        except urllib.error.HTTPError:
            return u
        except Exception:
            continue
    return None


def ensure_playwright():
    """Return a directory from which `playwright-core` (or `playwright`/`@playwright/test`) resolves. Never downloads a browser."""
    for mod in ("playwright-core", "playwright", "@playwright/test"):
        for base in (ROOT, os.path.join(ROOT, "frontend")):
            if os.path.isdir(os.path.join(base, "node_modules", mod)):
                return base, mod
    cache = os.path.join(tempfile.gettempdir(), f"nexus-ui-snapshot-pw-{PW_VERSION}")
    if not os.path.isdir(os.path.join(cache, "node_modules", "playwright-core")):
        os.makedirs(cache, exist_ok=True)
        env = dict(os.environ, PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD="1")
        p = subprocess.run(["npm", "install", "--prefix", cache, "--no-save", "--no-audit", "--no-fund", f"playwright-core@{PW_VERSION}"],
                           capture_output=True, text=True, env=env, timeout=300)
        if p.returncode != 0:
            raise RuntimeError("npm install playwright-core failed: " + (p.stderr or p.stdout)[-300:])
    return cache, "playwright-core"


# ------------------------------------------------------------------ outputs
def write_index(wave, wdir, meta):
    L = [f"# UI progress — after wave {wave:02d}", "",
         f"Integrated commit: `{meta.get('commit', '')[:12]}` · captured {meta.get('captured_at')} (UTC) · viewport 1440×900 · device scale {meta.get('dsf', '-')} · "
         f"{meta.get('size_mb', 0):.2f} MB · browser: system Chrome via Playwright {PW_VERSION}", "",
         f"**Status: {meta['status']}**" + (f" — {meta['reason']}" if meta.get("reason") else ""), "",
         "Synthetic seeded data only (DEV). Password, token and secret fields are masked. Pages not yet built are listed, never faked."
         " Contract notes are automatic measurements against docs/spec/UI-CONTRACT.md §3 (header 64 px, sidebar 256/80 px, AI panel 320 px).", ""]
    if meta.get("login_src"):
        L += [f"Signed in as: {meta['login_src']}", ""]
    if meta.get("stub"):
        L += ["> **REHEARSAL PLACEHOLDER** — these images are 1×1 stubs written by the local git rehearsal, not real screenshots.", ""]
    shots = meta.get("shots", [])
    if shots:
        L += ["| Page | Route | Theme / variant | Status | Contract notes |", "|---|---|---|---|---|"]
        for s in shots:
            L.append(f"| {s['label']} | `{s['route']}` | {s['variant']} | {s['status']} | {s.get('notes', '')} |".replace("\n", " "))
        L.append("")
    if meta.get("skipped"):
        L += ["## Not captured", "", "| Page | Route | Why |", "|---|---|---|"] + [f"| {s['label']} | `{s['route']}` | {s['status']} |" for s in meta["skipped"]] + [""]
    if shots:
        L += ["## Gallery", ""]
        for s in shots:
            if s.get("file"):
                L += [f"### {s['label']} — {s['variant']}", "", f"![{s['label']} {s['variant']}]({s['file']})", ""]
    open(os.path.join(wdir, "INDEX.md"), "w", encoding="utf-8").write("\n".join(L))


def write_readme():
    waves = sorted((d for d in os.listdir(OUTDIR) if re.fullmatch(r"W\d\d", d) and os.path.isfile(os.path.join(OUTDIR, d, "INDEX.md"))), reverse=True)
    L = ["# UI progress (newest first)", "", "Screenshots taken by the integrator after each wave (from wave 02 on), with the system Chrome on the integrated `main`.",
         "Open a wave to see every page in light and dark theme. Synthetic DEV data only.", ""]
    if not waves:
        L.append("No screenshots yet: the first set appears after wave 02 is integrated.")
    for w in waves:
        idx = open(os.path.join(OUTDIR, w, "INDEX.md"), encoding="utf-8").read()
        status = (re.search(r"\*\*Status: (.*?)\*\*", idx) or [None, "?"])[1]
        thumb = next((f for f in ("inicio-light.png", "home-light.png") if os.path.isfile(os.path.join(OUTDIR, w, f))), None)
        L.append(f"## [{w}]({w}/INDEX.md) — {status}")
        L.append("")
        if thumb:
            L += [f'<a href="{w}/INDEX.md"><img src="{w}/{thumb}" width="480" alt="{w} home, light theme"></a>', ""]
    open(os.path.join(OUTDIR, "README.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")


def commit(wave):
    br = sh("git", "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if br != f"nexus-integrate-w{wave:02d}":
        return f"not committed (current branch {br}, expected nexus-integrate-w{wave:02d})"
    sh("git", "add", "-A", "docs/ui-progress")
    if sh("git", "diff", "--cached", "--quiet").returncode == 0:
        return "nothing to commit"
    p = sh("git", "commit", "-q", "-m", f"ui preview after wave {wave:02d}", "-m", f"{INTEG_TRAILER} wave {wave:02d} ui preview")
    return "committed" if p.returncode == 0 else "commit failed: " + (p.stderr or p.stdout)[-200:]


# ------------------------------------------------------------------ main
def capture(wave, wdir, base, login, pages, dsf):
    mod_dir, mod = ensure_playwright()
    job = {"base": base, "outdir": wdir, "dsf": dsf, "chrome": os.environ.get("CHROME_PATH") or "/usr/bin/google-chrome",
           "login": login, "pages": pages, "module_dir": mod_dir, "module": mod}
    p = subprocess.run(["node", os.path.join(ROOT, "tools", "ui_snapshot.cjs")], input=json.dumps(job), capture_output=True, text=True, timeout=1500,
                       env=dict(os.environ, PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD="1"))
    out = p.stdout.strip().splitlines()
    res = json.loads(out[-1]) if out and out[-1].startswith("{") else None
    secret = login["password"] if login else None
    scrub = lambda t: t.replace(secret, "***") if (secret and isinstance(t, str)) else t
    if res is None:
        raise RuntimeError("browser script failed: " + scrub((p.stderr or p.stdout)[-400:]))
    res["error"] = scrub(res.get("error"))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wave", type=int, required=True)
    ap.add_argument("--base-url")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    wdir = os.path.join(OUTDIR, f"W{a.wave:02d}")
    if os.path.isdir(wdir):
        shutil.rmtree(wdir)
    os.makedirs(wdir)
    meta = {"status": "FAILED", "captured_at": now(), "commit": sh("git", "rev-parse", "HEAD").stdout.strip(), "shots": [], "skipped": []}
    try:
        pages, skipped = pages_for_wave()
        meta["skipped"] = skipped
        if os.environ.get("NEXUS_UI_STUB"):
            if not os.environ.get("NEXUS_REHEARSAL"):
                raise RuntimeError("NEXUS_UI_STUB is only allowed in the local rehearsal")
            meta["stub"] = True
            for pg in [{"id": "login", "label": "Login", "route": "/"}] + pages:
                for th in ("light", "dark"):
                    f = f"{pg['id']}-{th}.png"
                    open(os.path.join(wdir, f), "wb").write(STUB_PNG)
                    meta["shots"].append({"label": pg["label"], "route": pg["route"], "variant": th, "status": "placeholder", "file": f})
            meta.update(status="OK (rehearsal placeholder)", dsf=1, size_mb=0.0)
        else:
            base = find_base_url(a.base_url)
            if not base:
                raise RuntimeError("dev stack not reachable on 127.0.0.1:8080-8090 (run `make dev-up && make dev-seed`)")
            login, src = find_login()
            if login:
                meta["login_src"] = f"demo login from {src}"
            else:
                try:
                    r = dev_login_module().ensure()
                    login = {"user": r["user"], "password": r["password"]}
                    meta["login_src"] = (f"throwaway DEV user `{r['user']}` of a synthetic tenant, bootstrapped by tools/dev_login.py "
                                         f"(password only in {r['file']}, 0600, rotated each run)")
                except Exception as e:
                    meta["reason_login"] = f"{src}; dev login bootstrap: {str(e)[:200]}"
            for dsf in (1, 0.75, 0.5):
                for f in glob.glob(os.path.join(wdir, "*.png")):
                    os.remove(f)
                res = capture(a.wave, wdir, base, login, pages, dsf)
                for f in glob.glob(os.path.join(wdir, "*.png")):
                    optimize_png(f)
                total = sum(os.path.getsize(f) for f in glob.glob(os.path.join(wdir, "*.png")))
                if total <= CAP_BYTES:
                    break
            shots = res["shots"]
            if total > CAP_BYTES:
                for s in sorted([s for s in shots if s.get("file")], key=lambda s: -os.path.getsize(os.path.join(wdir, s["file"]))):
                    if total <= CAP_BYTES:
                        break
                    total -= os.path.getsize(os.path.join(wdir, s["file"])); os.remove(os.path.join(wdir, s["file"]))
                    s.update(file=None, status="omitted (8 MB size cap)")
            meta["shots"] = shots
            meta["skipped"] += res.get("not_built", [])
            ok = [s for s in shots if s.get("file")]
            meta.update(dsf=dsf, size_mb=total / 1048576, status="OK" if ok and not res.get("error") else "FAILED",
                        reason="; ".join(x for x in [res.get("error"), meta.get("reason_login") and ("login: " + meta["reason_login"])] if x))
    except Exception as e:  # never block integration
        meta["reason"] = str(e)[:400]
    write_index(a.wave, wdir, meta)
    write_readme()
    c = commit(a.wave) if a.commit else "not committed (--commit not given)"
    n_ok = len([s for s in meta["shots"] if s.get("file")])
    if meta["status"].startswith("OK"):
        print(f"UI-SNAPSHOT: OK {n_ok} images, {len(meta['skipped'])} pages not captured, {meta.get('size_mb', 0):.2f} MB, {c}")
    else:
        print(f"UI-SNAPSHOT: FAILED {meta.get('reason', 'unknown')} ({n_ok} images, {c})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"UI-SNAPSHOT: FAILED {str(e)[:300]}")
        sys.exit(0)
