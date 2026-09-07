"""Read-only league transport benchmark. No browser emulation or field-CWV claims.

Run: .venv/bin/python backend/tests/perf/league_benchmark.py --runs 5
This measures actual response bytes/TTFB/end-to-end transport. The calculated
200 KiB/s transfer floor excludes latency and CPU; curl --limit-rate can verify
that budget separately. Never provide credentials on the command line.
"""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import time
from urllib.request import Request, urlopen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--league", type=int, default=1)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.runs <= 20:
        parser.error("--runs must be between 1 and 20")
    output = {}
    for name, suffix in {
        "roster": "/roster",
        "waivers_first_10": "/waivers/page",
        "waivers_next_10": "/waivers/page?offset=10",
        "waivers_WR": "/waivers/page?role=WR",
    }.items():
        samples = []
        for _ in range(args.runs):
            request = Request(
                f"{args.base_url}/api/v1/leagues/{args.league}{suffix}",
                headers={"Accept-Encoding": "gzip"},
            )
            started = time.perf_counter()
            with urlopen(request, timeout=30) as response:
                ttfb = (time.perf_counter() - started) * 1000
                raw = response.read()
                total = (time.perf_counter() - started) * 1000
                encoding = response.headers.get("Content-Encoding", "identity")
                decoded = gzip.decompress(raw) if encoding == "gzip" else raw
                body = json.loads(decoded)
                samples.append(
                    {
                        "ttfb_ms": round(ttfb, 2),
                        "total_ms": round(total, 2),
                        "wire_bytes": len(raw),
                        "decoded_bytes": len(decoded),
                        "encoding": encoding,
                        "rows": len(body) if isinstance(body, list) else len(body["items"]),
                    }
                )
        output[name] = {
            "samples": samples,
            "median_total_ms": statistics.median(s["total_ms"] for s in samples),
            "transfer_floor_at_200_KiB_s_ms": round(samples[0]["wire_bytes"] / 204800 * 1000, 1),
        }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
