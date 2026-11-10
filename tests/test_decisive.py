"""Decisive tests: counting, duplicates, greetings, overlaps, unknown input."""

import json
import shutil

from builders import Log, uniform

from voice_check.analysis import compare, load_policy, summarize
from voice_check.parse import load_run

POLICY = "policies/published-example-420.yaml"


def _cmp(a, b, policy=POLICY):
    return compare(load_run(a), load_run(b), load_policy(policy))


def test_duplicate_session_import_is_not_doubled(tmp_path):
    log = Log()
    for k in range(5):
        log.turn("s1", f"u{k + 1}", 0.4)
    p = log.write(tmp_path / "a.jsonl")
    once = summarize(load_run(p), min_n=1)
    p.write_text(p.read_text() * 2)  # the same session imported twice
    twice = summarize(load_run(p), min_n=1)
    assert twice["counts"]["turns_measured"] == once["counts"]["turns_measured"] == 5
    assert twice["total"]["n"] == 5
    assert twice["counts"]["duplicate_lines_ignored"] == 5
    assert twice["input_error_count"] == 0


def test_conflicting_duplicate_is_an_input_error(tmp_path):
    log = Log()
    log.turn("s1", "u1", 0.4)
    clash = dict(log.lines[0], event_id="different", payload=dict(log.lines[0]["payload"], total_secs=0.5))
    p = log.write(tmp_path / "a.jsonl", extra_lines=[json.dumps(clash)])
    assert load_run(p).input_errors


def test_greetings_and_user_turns_are_not_mixed(tmp_path):
    log = Log()
    log.add("s1", None, None, "latency_breakdown", "LatencyBreakdown",
            {**__import__("builders").breakdown(2.5), "measured_from": "client_connected", "user_turn_start_time": None})
    log.turn("s1", "u1", 0.4)
    s = summarize(load_run(log.write(tmp_path / "a.jsonl")), min_n=1)
    assert s["total"]["n"] == 1 and s["total"]["max_ms"] == 400.0
    assert s["greetings"]["n"] == 1 and s["greetings"]["median_ms"] == 2500.0


def test_overlapping_contributions_are_not_added_twice(tmp_path):
    log = Log()
    # two function_handler spans overlap: [100.0, 100.2] and [100.1, 100.3]
    log.turn("s1", "u1", 0.3, parts=[("function_handler", 0.2, 100.0), ("function_handler", 0.2, 100.1)])
    s = summarize(load_run(log.write(tmp_path / "a.jsonl")), min_n=1)
    fh = next(c for c in s["contributions"] if c["key"] == "function_handler")
    assert fh["median_ms"] == 300.0  # union, not 400
    assert s["total"]["median_ms"] == 300.0  # the measured total, not a sum


def test_tool_time_and_user_turn_secs_are_not_added_to_the_total(tmp_path):
    log = Log()
    log.turn("s1", "u1", 0.4, user_turn_secs=0.25, function_calls=[("lookup", 0.3)],
             ttfb=[("STT#0", None, 0.05), ("LLM#0", None, 0.12), ("TTS#0", None, 0.1)])
    s = summarize(load_run(log.write(tmp_path / "a.jsonl")), min_n=1)
    assert s["total"]["median_ms"] == 400.0


def test_unknown_framework_version_never_passes(tmp_path):
    a = uniform(tmp_path / "a.jsonl", [0.4] * 300)
    b = uniform(tmp_path / "b.jsonl", [0.4] * 300, framework_version="1.16.0", run_id="r2", config_id="c2")
    rep = _cmp(a, b)
    assert rep["verdict"] == "INCOMPLETE" and rep["exit_code"] == 2
    assert rep["reasons"][0]["code"] == "input_error"


def test_unknown_structure_never_passes(tmp_path):
    a = uniform(tmp_path / "a.jsonl", [0.4] * 300)
    log = Log(run_id="r2", config_id="c2")
    for i in range(300):
        line = log.turn(f"s{i // 5:04d}", f"u{i % 5 + 1}", 0.4)
    line["payload"]["new_field_from_a_future_pipecat"] = 1
    b = log.write(tmp_path / "b.jsonl")
    rep = _cmp(a, b)
    assert rep["verdict"] == "INCOMPLETE" and rep["reasons"][0]["code"] == "input_error"


def test_bare_pipecat_dump_without_envelope_never_passes(tmp_path):
    a = uniform(tmp_path / "a.jsonl", [0.4] * 300)
    b = uniform(tmp_path / "b.jsonl", [0.4] * 300, run_id="r2", config_id="c2")
    with b.open("a") as fh:
        fh.write(json.dumps({"ttfb": [], "contributions": [], "total_secs": 0.4}) + "\n")
    rep = _cmp(a, b)
    assert rep["verdict"] == "INCOMPLETE" and rep["reasons"][0]["code"] == "input_error"


def test_missing_manifest_never_passes(tmp_path):
    a = uniform(tmp_path / "a.jsonl", [0.4] * 300)
    b = uniform(tmp_path / "b.jsonl", [0.4] * 300, run_id="r2", config_id="c2")
    (tmp_path / "b.manifest.json").unlink()
    rep = _cmp(a, b)
    assert rep["verdict"] == "INCOMPLETE" and rep["reasons"][0]["code"] == "input_error"


def test_different_scenarios_are_not_comparable(tmp_path):
    a = uniform(tmp_path / "a.jsonl", [0.4] * 300)
    b = uniform(tmp_path / "b.jsonl", [0.4] * 300, run_id="r2", config_id="c2", scenario_id="other")
    rep = _cmp(a, b)
    assert rep["verdict"] == "INCOMPLETE" and rep["reasons"][0]["code"] == "not_comparable"


def test_p95_exactly_420_passes_and_420_001_fails(tmp_path):
    a = uniform(tmp_path / "a.jsonl", [0.42] * 300)
    b = uniform(tmp_path / "b.jsonl", [0.42] * 300, run_id="r2", config_id="c2")
    assert _cmp(a, b)["verdict"] == "PASS"
    c = uniform(tmp_path / "c.jsonl", [0.420001] * 300, run_id="r3", config_id="c3")
    rep = _cmp(a, c)
    assert rep["verdict"] == "FAIL" and rep["evaluation"]["value_ms"] == 420.001


def test_float_noise_does_not_flip_the_verdict(tmp_path):
    a = uniform(tmp_path / "a.jsonl", [0.42] * 300)
    b = uniform(tmp_path / "b.jsonl", [0.1 + 0.2 + 0.12] * 300, run_id="r2", config_id="c2")
    assert 0.1 + 0.2 + 0.12 != 0.42  # 0.42000000000000004
    assert _cmp(a, b)["verdict"] == "PASS"


def test_too_few_turns_is_incomplete_and_shows_no_p95(tmp_path):
    a = uniform(tmp_path / "a.jsonl", [0.4] * 10)
    b = uniform(tmp_path / "b.jsonl", [0.4] * 10, run_id="r2", config_id="c2")
    rep = _cmp(a, b)
    assert rep["verdict"] == "INCOMPLETE" and rep["reasons"][0]["code"] == "too_few_turns"
    assert rep["runs"]["candidate"]["total"]["p95_ms"] is None


def test_failed_turns_can_fail_the_policy(tmp_path):
    # 300 measured turns (the policy's floor) and 30 silent ones
    a = uniform(tmp_path / "a.jsonl", [0.4] * 330)
    log = Log(run_id="r2", config_id="c2")
    for i in range(330):
        s, t = f"s{i // 5:04d}", f"u{i % 5 + 1}"
        if i < 30:  # 9% silent turns
            log.add(s, t, "no_audio", "error", "ErrorFrame",
                    {"error": f"TTS context {i} completed with no audio", "fatal": False, "category": None, "processor": "TTS#0"})
        else:
            log.turn(s, t, 0.4)
    rep = _cmp(a, log.write(tmp_path / "b.jsonl"))
    assert rep["verdict"] == "FAIL" and rep["evaluation"]["value_is_failed_turn"] is True
    assert rep["runs"]["candidate"]["total"]["n"] == 330


def test_published_breakdown_never_changes_the_verdict(tmp_path):
    import yaml

    pol = yaml.safe_load(open(POLICY))
    for item in pol["published_breakdown"]:
        item["streaming_target_ms"] = 1
    alt = tmp_path / "alt.yaml"
    alt.write_text(yaml.safe_dump(pol))
    a = uniform(tmp_path / "a.jsonl", [0.4] * 300)
    b = uniform(tmp_path / "b.jsonl", [0.41] * 300, run_id="r2", config_id="c2")
    r1, r2 = _cmp(a, b), _cmp(a, b, alt)
    assert r1["verdict"] == r2["verdict"] == "PASS"
    assert r1["evaluation"] == r2["evaluation"]
