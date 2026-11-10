"""Run every case the page shows, through the real CLI, into reports/.

    uv run python scripts/build_reports.py

* synthetic-compare.json          the committed fixtures (FAIL, exit 1)
* synthetic-delay-removed.json    same scenario regenerated without the delay (PASS, exit 0)
* synthetic-slow-traces-removed.json  the 100 slow turns deleted from the log (INCOMPLETE, exit 2)
* local-capture.json              the real local run, summarized (no verdict)

Variant logs are rebuilt in build/ (not committed).
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
POLICY = "policies/published-example-420.yaml"


def cli(*args) -> int:
    r = subprocess.run([sys.executable, "-m", "voice_check.cli", *args], cwd=ROOT, capture_output=True, text=True)
    print(f"$ voice-check {' '.join(args)}\n  -> exit {r.returncode}")
    if r.returncode not in (0, 1, 2) or r.stderr.strip().startswith("Traceback"):
        print(r.stderr)
        raise SystemExit(1)
    return r.returncode


def main():
    from capture.synthetic_scenario import delayed_sessions, generate, session_id

    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    (ROOT / "reports").mkdir(exist_ok=True)

    generate(build / "candidate-no-delay.jsonl", run_id="synthetic-candidate-no-delay", config_id="candidate-without-delay", delay_s=0.0)

    slow = {session_id(s) for s in delayed_sessions()}
    kept = []
    for raw in (ROOT / "fixtures" / "candidate.jsonl").open():
        line = json.loads(raw)
        if line["session_id"] in slow and line["turn_id"] is not None:
            continue
        kept.append(raw)
    (build / "candidate-slow-removed.jsonl").write_text("".join(kept))
    shutil.copy(ROOT / "fixtures" / "candidate.manifest.json", build / "candidate-slow-removed.manifest.json")

    codes = {
        "synthetic-compare": cli("compare", "fixtures/baseline.jsonl", "fixtures/candidate.jsonl", "--policy", POLICY, "--report", "reports/synthetic-compare.json"),
        "synthetic-delay-removed": cli("compare", "fixtures/baseline.jsonl", "build/candidate-no-delay.jsonl", "--policy", POLICY, "--report", "reports/synthetic-delay-removed.json"),
        "synthetic-slow-traces-removed": cli("compare", "fixtures/baseline.jsonl", "build/candidate-slow-removed.jsonl", "--policy", POLICY, "--report", "reports/synthetic-slow-traces-removed.json"),
        "local-capture": cli("summarize", "capture/out/local-capture.jsonl", "--report", "reports/local-capture.json"),
    }
    assert codes == {"synthetic-compare": 1, "synthetic-delay-removed": 0, "synthetic-slow-traces-removed": 2, "local-capture": 0}, codes
    print(json.dumps(codes))


if __name__ == "__main__":
    main()
