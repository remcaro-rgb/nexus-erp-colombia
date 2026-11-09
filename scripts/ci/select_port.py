#!/usr/bin/env python3
"""Select the DEV proxy port (D-002). Owner: P03.

Preferred port: $ERP_DEV_PORT (default 8080). If it is busy, the first free port of 8081..8090 on
127.0.0.1 is chosen. A port already published by our own compose project counts as free for us
(re-running dev-up must not move the stack). Never kills or inspects foreign processes beyond a
bind test. Output (stdout, one line): the selected port. All busy -> exit 3 and a message on stderr
listing every busy port (BLOCKED_DEV_RUNTIME).
"""
from __future__ import annotations

import errno
import json
import os
import socket
import sys

FALLBACK = list(range(8081, 8091))


def is_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError as exc:  # EADDRINUSE / EACCES -> busy
            if exc.errno in (errno.EADDRINUSE, errno.EACCES, errno.EADDRNOTAVAIL):
                return False
            raise
    return True


def candidates(preferred: int) -> list[int]:
    order = [preferred] + [p for p in [8080] + FALLBACK if p != preferred]
    return order


def select(preferred: int, owned: set[int], free_check=is_free) -> tuple[int | None, list[int]]:
    busy: list[int] = []
    for port in candidates(preferred):
        if port in owned or free_check(port):
            return port, busy
        busy.append(port)
    return None, busy


def main(argv: list[str]) -> int:
    raw = os.environ.get("ERP_DEV_PORT", "8080")
    try:
        preferred = int(raw)
    except ValueError:
        sys.stderr.write(f"ERP_DEV_PORT must be an integer, got {raw!r}\n")
        return 2
    if not 1024 <= preferred <= 65535:
        sys.stderr.write("ERP_DEV_PORT must be between 1024 and 65535\n")
        return 2
    owned: set[int] = set()
    if len(argv) > 1 and argv[1]:
        owned = {int(p) for p in argv[1].split(",") if p.strip().isdigit()}
    port, busy = select(preferred, owned)
    if port is None:
        sys.stderr.write(
            "BLOCKED_DEV_RUNTIME: all candidate ports are busy on 127.0.0.1: "
            + ", ".join(str(p) for p in busy)
            + ". Free one of them (this script never stops other processes) or set ERP_DEV_PORT.\n"
        )
        return 3
    if port != preferred:
        sys.stderr.write(json.dumps({"event": "dev_port_select", "preferred": preferred,
                                     "selected": port, "busy": busy}) + "\n")
    sys.stdout.write(f"{port}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
