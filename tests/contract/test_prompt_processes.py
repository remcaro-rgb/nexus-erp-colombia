"""T-P02-02 — every process listed in the "API" section of module prompts P04–P21 appears as an operation of the frozen contract
(checklist generated from docs/prompt-pack/prompts). Also: every module of shared/02 with an API has a fragment."""
from __future__ import annotations

import glob
import os
import re

import pytest

from contract_support import ROOT

WILD = r"[^/:]+"
PROMPTS = sorted(glob.glob(os.path.join(ROOT, "docs", "prompt-pack", "prompts", "P*.md")))
WITH_API = [p for p in PROMPTS if re.match(r"P(0[4-9]|1\d|2[01])-", os.path.basename(p))]


def api_section(path):
    txt = open(path, encoding="utf-8").read()
    m = re.search(r"^## API[^\n]*\n(.*?)(?=^## )", txt, re.S | re.M)
    return " ".join(m.group(1).split()) if m else ""


def tok_rx(t):
    out, i = "", 0
    while i < len(t):
        ch = t[i]
        if ch in "{<":
            j = t.index("}" if ch == "{" else ">", i)
            out += WILD
            i = j + 1
        elif ch == "*":
            out += r"[^:]+"
            i += 1
        else:
            out += re.escape(ch)
            i += 1
    return out


def checklist(text):
    """[(token, regexes)] — base switches on absolute tokens; tokens in parentheses or bare `:verb` attach to the previous resource."""
    items, base, anchor = [], None, None
    paren = [(m.start(), m.end()) for m in re.finditer(r"\([^)]*\)", text)]
    for m in re.finditer(r"`([^`]+)`", text):
        raw = m.group(1).strip()
        alts = [raw]
        if "|:" in raw:  # `/break-glass:open|:close`, `/plans/{id}:confirm|:cancel`
            head, rest = raw.split(":", 1)
            alts = [head + ":" + a.lstrip(":") for a in rest.split("|")]
        inside = any(a <= m.start() < b for a, b in paren)
        for t in alts:
            if t.startswith("/api/"):
                segs = t.split("/")
                base = "/".join(segs[:4])
                items.append((t, ["^" + tok_rx(t) + "$"]))
                anchor = t
            elif t.startswith("/pos/"):  # P12 shorthand `/pos/tickets:checkout`
                items.append((t, ["^/api/v1" + tok_rx(t) + "$"]))
            elif (inside or t.startswith(":")) and anchor is not None:
                body = tok_rx(anchor)
                items.append((t, ["^" + body + "(?:/[^:]*)?" + tok_rx(t) + "$"]))
            elif t.startswith("/") and base:
                full = base + t
                items.append((t, ["^" + tok_rx(full) + "$", "^" + tok_rx(full) + "/" + WILD + "$"]))
                if not inside:
                    anchor = full
    return items


@pytest.mark.parametrize("prompt", WITH_API, ids=[os.path.basename(p)[:3] for p in WITH_API])
def test_T_P02_02_every_prompt_process_is_an_operation(prompt, bundle):
    text = api_section(prompt)
    name = os.path.basename(prompt)
    if name.startswith(("P08", "P20")):
        assert text == "", "P08 (UI shell) and P20 (MCP over the public API) declare no REST API of their own"
        return
    items = checklist(text)
    assert len(items) >= 5, name
    paths = list(bundle["paths"])
    missing = []
    for tok, regs in items:
        if not any(re.match(r, p) for r in regs for p in paths):
            missing.append(tok)
    assert missing == [], f"{name}: {missing}"


def test_T_P02_02_checklist_totals():
    total = sum(len(checklist(api_section(p))) for p in WITH_API)
    assert total >= 300


def test_T_P02_02_pos_checkout_is_the_only_finalization_path(bundle):
    ops = bundle["paths"]["/api/v1/pos/tickets:checkout"]["post"]
    assert ops["x-permission"] == "pos.ticket.post"
    assert "pos_offline_finalization_forbidden" in ops["x-error-codes"]
    assert not any(p.startswith("/api/v1/pos/tickets/{id}:post") for p in bundle["paths"])
