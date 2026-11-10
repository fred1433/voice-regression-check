"""The committed fixtures, and the scenario that produced them (Pipecat 1.15.0)."""

import json
import subprocess
import sys

import pytest

from voice_check.analysis import compare, load_policy
from voice_check.parse import load_run

POLICY = "policies/published-example-420.yaml"


@pytest.fixture(scope="module")
def report():
    return compare(load_run("fixtures/baseline.jsonl"), load_run("fixtures/candidate.jsonl"), load_policy(POLICY))


def test_table_of_the_brief(report):
    b, c = report["runs"]["baseline"]["total"], report["runs"]["candidate"]["total"]
    assert (b["median_ms"], b["mean_ms_measured_only"], b["p95_ms"], b["over_threshold"]) == (400.0, 400.0, 420.0, 0)
    assert (c["median_ms"], c["mean_ms_measured_only"], c["p95_ms"], c["over_threshold"]) == (400.0, 450.0, 900.0, 100)
    assert b["n"] == c["n"] == 1000
    assert report["evaluation"]["baseline_passed"] is True
    assert report["verdict"] == "FAIL" and report["exit_code"] == 1
    assert report["label"] == "FAIL: configured latency threshold exceeded"


def test_counts_sessions_and_turns_apart(report):
    c = report["runs"]["candidate"]["counts"]
    assert (c["sessions_expected"], c["turns_expected"], c["turns_measured"]) == (200, 1000, 1000)
    assert c["greetings_kept_apart"] == 200
    f = report["change"]["facts"]
    assert (f["affected_turns"], f["affected_sessions"]) == (100, 20)
    assert f["every_turn_of_those_sessions_affected"] is True
    assert f["affected_by_turn_position"] == {f"u{k}": 20 for k in range(1, 6)}


def test_attribution_finds_the_injected_contribution(report):
    ch = report["change"]
    assert ch["primary_contribution"] == "llm_inference"
    assert ch["changed_measures"][0]["share_of_total_delta"] == 1.0
    assert ch["associated_metrics"] == [{"metric": "ttfb", "processor": "ScriptedLLM#0"}]
    assert ch["known_cause"]["matches_diagnosis"] is True
    assert all(r["delta_ms"] == 500.0 for r in ch["affected_turns"])
    # 20 turns per value: 400 -> 900 ms among them
    assert sum(1 for r in ch["affected_turns"] if r["baseline_total_ms"] == 400.0 and r["candidate_total_ms"] == 900.0) == 20


def test_dependent_timestamps_moved_with_the_delay(report):
    turn = report["change"]["affected_turns"][0]
    base = {s["key"]: s for s in turn["trace"]["baseline"]}
    cand = {s["key"]: s for s in turn["trace"]["candidate"]}
    assert cand["llm_inference"]["duration_ms"] - base["llm_inference"]["duration_ms"] == 500.0
    assert cand["speech_synthesis"]["start_ms"] - base["speech_synthesis"]["start_ms"] == 500.0
    assert cand["output_transport"]["start_ms"] - base["output_transport"]["start_ms"] == 500.0
    assert cand["speech_synthesis"]["duration_ms"] == base["speech_synthesis"]["duration_ms"]


def test_removing_the_delay_gives_pass_again(tmp_path):
    from capture.synthetic_scenario import generate

    generate(tmp_path / "nodelay.jsonl", run_id="nodelay", config_id="candidate-without-delay", delay_s=0.0)
    rep = compare(load_run("fixtures/baseline.jsonl"), load_run(tmp_path / "nodelay.jsonl"), load_policy(POLICY))
    assert rep["verdict"] == "PASS" and rep["exit_code"] == 0
    assert rep["label"] == "Latency policy passed"
    assert rep["evaluation"]["value_ms"] == 420.0


def test_regenerated_candidate_matches_the_committed_fixture(tmp_path):
    from capture.synthetic_scenario import generate

    generate(tmp_path / "candidate.jsonl", run_id="synthetic-candidate", config_id="candidate-llm-first-chunk-plus-500ms", delay_s=0.5)
    assert (tmp_path / "candidate.jsonl").read_text() == open("fixtures/candidate.jsonl").read()


def test_removing_the_100_slow_traces_is_incomplete(tmp_path):
    slow = {f"s{s + 1:03d}" for s in range(200) if s % 10 == 3}
    kept = [l for l in open("fixtures/candidate.jsonl") if not (json.loads(l)["session_id"] in slow and json.loads(l)["turn_id"])]
    trimmed = tmp_path / "candidate.jsonl"
    trimmed.write_text("".join(kept))
    (tmp_path / "candidate.manifest.json").write_text(open("fixtures/candidate.manifest.json").read())
    rep = compare(load_run("fixtures/baseline.jsonl"), load_run(trimmed), load_policy(POLICY))
    c = rep["runs"]["candidate"]
    assert c["counts"]["turns_measured"] == 900 and c["counts"]["missing_from_log"] == 100
    assert c["total"]["p95_ms"] == 420.0  # looks fine, but 100 expected turns are gone
    assert rep["verdict"] == "INCOMPLETE" and rep["exit_code"] == 2
    assert rep["reasons"][0]["code"] == "coverage"


def test_cli_exit_codes_and_report_drive_the_terminal(tmp_path):
    out = tmp_path / "report.json"
    cmd = [sys.executable, "-m", "voice_check.cli", "compare", "fixtures/baseline.jsonl", "fixtures/candidate.jsonl",
           "--policy", POLICY, "--report", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 1
    shown = subprocess.run([sys.executable, "-m", "voice_check.cli", "show", str(out)], capture_output=True, text=True)
    assert shown.returncode == 1
    assert r.stdout.startswith(shown.stdout.rstrip("\n"))
    no_policy = subprocess.run(cmd[:6], capture_output=True, text=True)
    assert no_policy.returncode == 2 and "no default threshold" in no_policy.stderr
    missing_arg = subprocess.run([sys.executable, "-m", "voice_check.cli", "compare", "fixtures/baseline.jsonl"], capture_output=True, text=True)
    assert missing_arg.returncode == 2
    same = subprocess.run(cmd[:5] + ["fixtures/baseline.jsonl", "--policy", POLICY], capture_output=True, text=True)
    assert same.returncode == 0


def test_known_cause_is_not_confirmed_when_it_does_not_match(tmp_path):
    """A declared cause that points at other sessions or another stage must not be 'matched'."""
    import shutil

    for name, edit in (
        ("other-sessions", lambda m: m["injected_change"].update(sessions=["s001", "s002"])),
        ("other-stage", lambda m: m["injected_change"].update(contribution_key="speech_synthesis")),
    ):
        d = tmp_path / name
        d.mkdir()
        shutil.copy("fixtures/candidate.jsonl", d / "candidate.jsonl")
        m = json.load(open("fixtures/candidate.manifest.json"))
        edit(m)
        (d / "candidate.manifest.json").write_text(json.dumps(m))
        rep = compare(load_run("fixtures/baseline.jsonl"), load_run(d / "candidate.jsonl"), load_policy(POLICY))
        assert rep["change"]["known_cause"]["matches_diagnosis"] is False, name
        assert rep["change"]["primary_contribution"] == "llm_inference"  # read from the logs, not from the manifest


def test_no_known_cause_outside_synthetic_runs(tmp_path):
    import shutil

    shutil.copy("fixtures/candidate.jsonl", tmp_path / "candidate.jsonl")
    m = json.load(open("fixtures/candidate.manifest.json"))
    m["provenance"] = "local-capture"
    (tmp_path / "candidate.manifest.json").write_text(json.dumps(m))
    rep = compare(load_run("fixtures/baseline.jsonl"), load_run(tmp_path / "candidate.jsonl"), load_policy(POLICY))
    # the log still says synthetic, so the manifest is rejected as inconsistent: no verdict, no cause
    assert rep["verdict"] == "INCOMPLETE" and rep["change"] is None
