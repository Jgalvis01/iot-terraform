import json
import sys
from pathlib import Path

folder = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/load/results")
print("| Escenario | RPS | P50 ms | P95 ms | P99 ms | Error % |")
print("|---|---:|---:|---:|---:|---:|")
for f in sorted(folder.glob("*.json")):
    m = json.loads(f.read_text())["metrics"]
    d = m["http_req_duration"]["values"]
    print(f"| {f.stem} | {m['http_reqs']['values']['rate']:.1f} | {d['med']:.1f} | {d['p(95)']:.1f} | "
          f"{d['p(99)']:.1f} | {m['http_req_failed']['values']['rate'] * 100:.2f} |")
