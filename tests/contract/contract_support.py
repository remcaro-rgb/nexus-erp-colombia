"""Helpers for the contract tests (P02): paths and loaders. Pure files; needs PyYAML and jsonschema."""
from __future__ import annotations

import json
import os
import sys

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONTRACTS = os.path.join(ROOT, "contracts")
TOOLS = os.path.join(CONTRACTS, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

METHODS = ("get", "post", "put", "patch", "delete")


def load_yaml(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return yaml.load(fh, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))


def load_json(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return json.load(fh)


def resolve(doc, node):
    """Resolve a local $ref (recursively)."""
    seen = 0
    while isinstance(node, dict) and "$ref" in node and node["$ref"].startswith("#/"):
        cur = doc
        for part in node["$ref"][2:].split("/"):
            cur = cur[part]
        node = cur
        seen += 1
        assert seen < 50, "ref loop"
    return node


def param_names(doc, op):
    out = {}
    for p in op.get("parameters", []):
        p = resolve(doc, p)
        out[p["name"]] = p
    return out


def iter_operations(doc):
    for path, item in doc["paths"].items():
        for m in METHODS:
            if m in item:
                yield m, path, item[m]
