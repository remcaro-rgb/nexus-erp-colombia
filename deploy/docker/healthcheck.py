"""Container healthcheck helper (stdlib only). Owner: P03.

  python /opt/nexus/healthcheck.py http <url>   -> 0 when the URL answers 2xx
  python /opt/nexus/healthcheck.py self <port> <path>
                                                -> 0 when this container's own address answers 2xx on <port>/<path>
                                                   (servers bind the container address, never 0.0.0.0; Host: localhost
                                                   so the request passes ALLOWED_HOSTS)
  python /opt/nexus/healthcheck.py process      -> 0 when PID 1 is alive and not a zombie
"""
import socket
import sys
import urllib.request


def http(url: str, host: str | None = None) -> int:
    req = urllib.request.Request(url, headers={"Host": host} if host else {})  # noqa: S310 - fixed in-container URL
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:  # noqa: S310 - fixed in-container URL
            return 0 if 200 <= resp.status < 300 else 1
    except OSError:
        return 1


def process() -> int:
    try:
        with open("/proc/1/stat", encoding="ascii") as fh:
            state = fh.read().rsplit(")", 1)[1].split()[0]
    except OSError:
        return 1
    return 1 if state in ("Z", "X") else 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "process"
    if mode == "http" and len(sys.argv) > 2:
        sys.exit(http(sys.argv[2]))
    if mode == "self" and len(sys.argv) > 3:
        addr = socket.gethostbyname(socket.gethostname())
        sys.exit(http(f"http://{addr}:{int(sys.argv[2])}/{sys.argv[3].lstrip('/')}", host="localhost"))
    sys.exit(process())
