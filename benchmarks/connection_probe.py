"""Read-only HTTPS timing, adapted from the historical connection-decomposition tool.

No endpoint is selected automatically. The optional CLI makes GET requests only.
The original DNS/connect/TLS/first-byte/body boundaries are preserved; names now
make TLS context construction and request construction explicit. See README.md.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import ssl
import statistics
import time
from urllib.parse import urlsplit


def measure_cold(url: str, timeout: float = 10.0, max_bytes: int = 4_194_304) -> dict:
    """Measure a fresh IPv4 TCP/TLS/HTTP1.1 GET, including failures.

    TLS timing includes context creation. First-byte timing starts before request
    construction/send. Body timing covers reception after the first response byte.
    These are local monotonic intervals, not server processing or fill latency.
    """
    target = urlsplit(url)
    if target.scheme != "https" or not target.hostname or target.username or target.password:
        raise ValueError("provide an HTTPS URL without embedded credentials")
    if any(ord(c) < 33 or ord(c) > 126 for c in url) or target.fragment:
        raise ValueError("URL must be ASCII, without whitespace or a fragment")
    if not math.isfinite(timeout) or timeout <= 0 or max_bytes < 1:
        raise ValueError("timeout and maximum response size must be positive")
    host, port = target.hostname, target.port or 443
    path = (target.path or "/") + (("?" + target.query) if target.query else "")
    authority = host if port == 443 else f"{host}:{port}"
    out = {key: None for key in (
        "dns_ms", "tcp_ms", "tls_setup_handshake_ms", "request_first_byte_ms",
        "remaining_receive_ms", "total_ms", "status", "error")}
    out["bytes_in"] = 0
    sock = None
    ssock = None
    t0 = time.perf_counter()
    try:
        addrs = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        t1 = time.perf_counter()
        out["dns_ms"] = (t1 - t0) * 1000
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(addrs[0][4])
        t2 = time.perf_counter()
        out["tcp_ms"] = (t2 - t1) * 1000
        ctx = ssl.create_default_context()
        ssock = ctx.wrap_socket(sock, server_hostname=host)
        t3 = time.perf_counter()
        out["tls_setup_handshake_ms"] = (t3 - t2) * 1000
        request = (f"GET {path} HTTP/1.1\r\nHost: {authority}\r\n"
                   "User-Agent: weather-research-probe/1.0\r\n"
                   "Accept: */*\r\nConnection: close\r\n\r\n").encode("ascii")
        ssock.sendall(request)
        first = ssock.recv(1)
        t4 = time.perf_counter()
        out["request_first_byte_ms"] = (t4 - t3) * 1000
        if not first:
            raise ValueError("connection closed without an HTTP response")
        response = bytearray(first)
        while True:
            chunk = ssock.recv(65536)
            if not chunk:
                break
            response.extend(chunk)
            if len(response) > max_bytes:
                raise ValueError("response exceeded maximum size")
        t5 = time.perf_counter()
        out["remaining_receive_ms"] = (t5 - t4) * 1000
        out["bytes_in"] = len(response)
        status_line = response.split(b"\r\n", 1)[0].split()
        if len(status_line) < 2 or not status_line[0].startswith(b"HTTP/"):
            raise ValueError("invalid HTTP status line")
        out["status"] = int(status_line[1])
        out["total_ms"] = (t5 - t0) * 1000
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["total_ms"] = (time.perf_counter() - t0) * 1000
    finally:
        try:
            if ssock is not None:
                ssock.close()
            elif sock is not None:
                sock.close()
        except OSError:
            pass
    return out


async def measure_warm_burst(client, path: str, n: int) -> list[dict]:
    """Reuse a supplied async HTTP client; caller owns setup and warmup.

    This preserves the historical sequential warm-burst structure. Supplying a
    new client each time would change the experiment. No client is created here.
    """
    if n < 1:
        raise ValueError("n must be positive")
    results = []
    for _ in range(n):
        start = time.perf_counter()
        try:
            response = await client.get(path, timeout=10.0)
            results.append({"ms": (time.perf_counter() - start) * 1000,
                            "status": response.status_code, "bytes": len(response.content),
                            "error": None})
        except Exception as exc:
            results.append({"ms": (time.perf_counter() - start) * 1000,
                            "status": None, "error": type(exc).__name__})
    return results


def summarize(values: list[float]) -> dict:
    """Report n/median/mean/range. Do not label a tiny sample's maximum p99."""
    if any(not math.isfinite(x) or x < 0 for x in values):
        raise ValueError("durations must be finite and nonnegative")
    if not values:
        return {"n": 0}
    return {"n": len(values), "median_ms": statistics.median(values),
            "mean_ms": statistics.mean(values), "min_ms": min(values), "max_ms": max(values)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="explicit HTTPS URL to measure with GET")
    parser.add_argument("--count", type=int, default=1, choices=range(1, 21), metavar="1..20")
    args = parser.parse_args()
    records = []
    for index in range(args.count):
        if index:
            time.sleep(1)
        records.append(measure_cold(args.url))
    successful = [r["total_ms"] for r in records if r["error"] is None and 200 <= r["status"] < 300]
    print(json.dumps({"records": records, "http_2xx": summarize(successful),
                      "attempts": len(records)}, indent=2))
