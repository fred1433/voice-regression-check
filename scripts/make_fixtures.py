"""Regenerate the synthetic fixtures (needs the capture extra: Pipecat 1.15.0).

    uv run python scripts/make_fixtures.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from capture.synthetic_scenario import generate  # noqa: E402

if __name__ == "__main__":
    out = ROOT / "fixtures"
    generate(out / "baseline.jsonl", run_id="synthetic-baseline", config_id="baseline", delay_s=0.0)
    generate(out / "candidate.jsonl", run_id="synthetic-candidate", config_id="candidate-llm-first-chunk-plus-500ms", delay_s=0.5)
    print(f"wrote {out}/baseline.jsonl, {out}/candidate.jsonl and their manifests")
