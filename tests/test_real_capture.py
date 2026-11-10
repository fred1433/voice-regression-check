"""The real local capture (capture/out/), read back."""

import json
from collections import defaultdict

from voice_check.analysis import single
from voice_check.parse import load_run

LOG = "capture/out/local-capture.jsonl"


def test_one_line_of_each_event_type_reads_back_without_losing_a_field():
    raw = [json.loads(l) for l in open(LOG)]
    first = {}
    for line in raw:
        first.setdefault(line["event_type"], line)
    assert set(first) == {"latency_breakdown", "metrics", "error"}
    run = load_run(LOG)
    assert run.input_errors == []
    kept = defaultdict(list)
    for t in run.turns.values():
        if t.breakdown:
            kept["latency_breakdown"].append(t.breakdown)
        kept["metrics"] += [m["payload"] for m in t.metrics]
        kept["error"] += [e["payload"] for e in t.errors]
    kept["metrics"] += [m["payload"] for m in run.startup_metrics]
    for et, line in first.items():
        assert line["payload"] in kept[et], et  # equal dicts: every field, every value


def test_cases_seen_in_the_real_capture():
    run = load_run(LOG)
    rep = single(run, min_n=300)
    s = rep["run"]
    # startup TTFB zeros, one per service per session, outside any turn
    assert s["zero_and_missing"]["initialization"] == 3 * s["counts"]["sessions_expected"]
    # Kokoro reports model "" in 1.15.0: labelled, not invented
    kokoro = [x for x in s["services"] if x["metric"] == "ttfb" and x["processor"] == "KokoroTTSService"]
    assert kokoro and all(x["model"] == "model not reported" for x in kokoro)
    # TTFA present, never merged into the TTFB count
    ttfa = next(x for x in s["services"] if x["metric"] == "ttfa" and x["processor"] == "KokoroTTSService")
    assert ttfa["n"] == kokoro[0]["n"]
    # "completed with no audio" errors in turns that still spoke: read, counted, not fatal
    assert s["counts"]["no_audio_errors_in_responded_turns"] >= 1
    assert s["inference_backend"] == "ollama"  # declared by the capture script
    assert s["total"]["p95_ms"] is None and s["total"]["p95_note"] == f"p95 not shown (N = {s['total']['n']})"
