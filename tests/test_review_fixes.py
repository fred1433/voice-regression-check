"""Cases found by review on 10 Nov 2026, each written before its fix.

Every test here failed on commit 8a63772 and passes after the fix.
"""

import json
import os
import stat
import subprocess
import sys

import pytest
from builders import Log, breakdown, uniform

from voice_check.analysis import compare, load_policy, single, summarize
from voice_check.parse import load_run
from voice_check.render_text import render

POLICY = "policies/published-example-420.yaml"


def _cmp(a, b, policy=POLICY):
    return compare(load_run(a), load_run(b), load_policy(policy))


def _cli(*args):
    return subprocess.run([sys.executable, "-m", "voice_check.cli", *args], capture_output=True, text=True)


# -- manifest -----------------------------------------------------------------


def _repeat_manifest(path, times):
    mp = path.with_name(path.name.removesuffix(".jsonl") + ".manifest.json")
    m = json.loads(mp.read_text())
    m["expected_turns"] = m["expected_turns"] * times
    mp.write_text(json.dumps(m))


def test_duplicated_manifest_turns_cannot_fake_the_sample_size(tmp_path):
    # 10 real turns, the manifest lists them 30 times: N must not become 300
    a = uniform(tmp_path / "a.jsonl", [0.4] * 10)
    b = uniform(tmp_path / "b.jsonl", [0.4] * 10, run_id="r2", config_id="c2")
    _repeat_manifest(a, 30)
    _repeat_manifest(b, 30)
    rep = _cmp(a, b)
    assert rep["verdict"] == "INCOMPLETE" and rep["exit_code"] == 2
    assert rep["reasons"][0]["code"] == "input_error"
    assert "listed twice" in rep["reasons"][0]["detail"]
    assert rep["runs"]["candidate"]["counts"]["turns_expected"] == 10
    assert rep["runs"]["candidate"]["total"]["n"] == 10


@pytest.mark.parametrize("manifest", ["[]", "{}", '"x"', "null"])
def test_manifest_of_the_wrong_shape_is_an_input_error(tmp_path, manifest):
    a = uniform(tmp_path / "a.jsonl", [0.4] * 300)
    b = uniform(tmp_path / "b.jsonl", [0.4] * 300, run_id="r2", config_id="c2")
    (tmp_path / "b.manifest.json").write_text(manifest)
    rep = _cmp(a, b)
    assert rep["verdict"] == "INCOMPLETE" and rep["reasons"][0]["code"] == "input_error"
    r = _cli("compare", str(a), str(b), "--policy", POLICY)
    assert r.returncode == 2 and "Traceback" not in r.stderr


@pytest.mark.parametrize(
    "entry",
    [
        "not-an-object",
        {"session_id": "s0000"},
        {"session_id": 7, "turns": ["u1"]},
        {"session_id": "s0000", "turns": "u1"},
        {"session_id": "s0000", "turns": [1]},
    ],
)
def test_manifest_entries_are_validated(tmp_path, entry):
    a = uniform(tmp_path / "a.jsonl", [0.4] * 5)
    mp = tmp_path / "a.manifest.json"
    m = json.loads(mp.read_text())
    m["expected_turns"].append(entry)
    mp.write_text(json.dumps(m))
    run = load_run(a)
    assert run.input_errors and run.input_errors[0].startswith("manifest: expected_turns[1]")


# -- interruptions before the first audio (observer) ----------------------------


def _observer_status(tmp_path, frames):
    """Push frames through the real observer inside one turn; return the turn's status."""
    import asyncio

    from pipecat.observers.base_observer import FramePushed
    from pipecat.processors.frame_processor import FrameDirection

    from capture.pipecat_jsonl_observer import EnvelopeWriter, PipecatJsonlObserver

    class Src:
        name = "P#0"

    out = tmp_path / "obs.jsonl"
    w = EnvelopeWriter(out, run_id="r", config_id="c", scenario_id="s", provenance="synthetic", clock=lambda: 0.0)
    obs = PipecatJsonlObserver(w)
    from pipecat.frames.frames import MetricsFrame
    from pipecat.metrics.metrics import ProcessingMetricsData

    async def go():
        w.begin_session("s1")
        w.begin_turn("u1")
        for f in frames:
            await obs.on_push_frame(FramePushed(source=Src(), destination=Src(), frame=f,
                                                direction=FrameDirection.DOWNSTREAM, timestamp=0))
        # one event so the turn has a line to carry its status
        await obs.on_push_frame(FramePushed(source=Src(), destination=Src(),
                                            frame=MetricsFrame(data=[ProcessingMetricsData(processor="P#0", value=0.01)]),
                                            direction=FrameDirection.DOWNSTREAM, timestamp=0))
        w.end_turn()
        w.close()

    asyncio.run(go())
    return {json.loads(l)["turn_status"] for l in out.read_text().splitlines()}


def test_interruption_before_first_audio_is_kept(tmp_path):
    from pipecat.frames.frames import InterruptionFrame, LLMFullResponseStartFrame

    # the LLM had started its answer; the user cut in before any audio played
    assert _observer_status(tmp_path, [LLMFullResponseStartFrame(), InterruptionFrame()]) == {"interrupted_before_response"}


def test_interruption_with_no_response_engaged_is_ignored(tmp_path):
    from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame, InterruptionFrame

    assert _observer_status(tmp_path, [InterruptionFrame()]) == {"no_response"}
    # a reply that already finished playing is not interrupted by a later frame
    assert _observer_status(tmp_path, [BotStartedSpeakingFrame(), BotStoppedSpeakingFrame(), InterruptionFrame()]) == {"no_response"}


# -- attribution ------------------------------------------------------------------


def _two_runs(tmp_path, base_parts, cand_parts, *, base_total, cand_total, owners=None, n=300, cand_ttfb=()):
    def make(name, parts, total, ttfb, **meta):
        log = Log(**meta)
        for i in range(n):
            line = log.turn(f"s{i // 5:04d}", f"u{i % 5 + 1}", total, parts=parts, ttfb=ttfb)
            for c in line["payload"]["contributions"]:
                if owners and c["key"] in owners:
                    c["owner"] = owners[c["key"]]
        return log.write(tmp_path / f"{name}.jsonl")

    return make("a", base_parts, base_total, ()), make("b", cand_parts, cand_total, cand_ttfb, run_id="r2", config_id="c2")


def test_no_ttfb_is_invented_for_an_owner_without_one(tmp_path):
    owners = {"endpointing_wait": "config: VAD stop_secs"}
    a, b = _two_runs(tmp_path, [("endpointing_wait", 0.2), ("llm_inference", 0.2)],
                     [("endpointing_wait", 0.7), ("llm_inference", 0.2)], base_total=0.4, cand_total=0.9, owners=owners)
    ch = _cmp(a, b)["change"]
    assert ch["primary_contribution"] == "endpointing_wait"
    assert ch["associated_metrics"] == []


def test_associated_ttfb_is_listed_when_it_exists(tmp_path):
    a, b = _two_runs(tmp_path, [("llm_inference", 0.4)], [("llm_inference", 0.9)], base_total=0.4, cand_total=0.9,
                     cand_ttfb=[("OpenAILLMService#0", "m", 0.8)])
    ch = _cmp(a, b)["change"]
    assert ch["associated_metrics"] == [{"metric": "ttfb", "processor": "OpenAILLMService#0"}]


def test_a_stage_present_on_one_side_only_counts_in_the_delta(tmp_path):
    a, b = _two_runs(tmp_path, [("llm_inference", 0.4)], [("llm_inference", 0.4), ("function_handler", 0.5)],
                     base_total=0.4, cand_total=0.9)
    ch = _cmp(a, b)["change"]
    row = ch["affected_turns"][0]
    assert row["contribution_delta_ms"] == {"function_handler": 500.0, "llm_inference": 0.0}
    assert ch["primary_contribution"] == "function_handler"


# -- sample size and failed turns ----------------------------------------------


def _with_failures(tmp_path, n, failed):
    log = Log(run_id="r2", config_id="c2")
    for i in range(n):
        s, t = f"s{i // 5:04d}", f"u{i % 5 + 1}"
        if i < failed:
            log.add(s, t, "no_audio", "error", "ErrorFrame",
                    {"error": f"TTS context {i} completed with no audio", "fatal": False, "category": None, "processor": "TTS#0"})
        else:
            log.turn(s, t, 0.4)
    return log.write(tmp_path / "b.jsonl")


def test_min_measured_turns_counts_measured_turns_only(tmp_path):
    a = uniform(tmp_path / "a.jsonl", [0.4] * 300)
    b = _with_failures(tmp_path, 300, 30)  # 270 measured, 30 failed
    rep = _cmp(a, b)
    assert rep["verdict"] == "INCOMPLETE" and rep["reasons"][0]["code"] == "too_few_turns"
    assert "270 measured" in rep["reasons"][0]["detail"]


def test_a_p95_on_a_failed_turn_is_said_not_left_null(tmp_path):
    a = uniform(tmp_path / "a.jsonl", [0.4] * 330)
    b = _with_failures(tmp_path, 330, 30)  # 300 measured, 30 failed: rank 314 is a failed turn
    rep = _cmp(a, b)
    t = rep["runs"]["candidate"]["total"]
    assert rep["verdict"] == "FAIL"
    assert t["p95_ms"] is None and t["p95_note"] == "p95 falls on a failed turn"
    assert rep["evaluation"]["value_ms"] is None and rep["evaluation"]["value_note"] == "p95 falls on a failed turn"
    assert "None" not in render(rep)


# -- input validation ---------------------------------------------------------------


def test_negative_total_is_an_input_error(tmp_path):
    a = uniform(tmp_path / "a.jsonl", [0.4] * 300)
    b = uniform(tmp_path / "b.jsonl", [-0.1] * 300, run_id="r2", config_id="c2")
    rep = _cmp(a, b)
    assert rep["verdict"] == "INCOMPLETE" and rep["reasons"][0]["code"] == "input_error"
    assert "total_secs" in rep["reasons"][0]["detail"]


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1"])
def test_non_finite_or_negative_metric_is_an_input_error(tmp_path, bad):
    log = Log()
    log.turn("s1", "u1", 0.4)
    p = log.write(tmp_path / "a.jsonl")
    line = log.lines[0]
    raw = json.dumps({**line, "event_type": "metrics", "native_type": "TTFBMetricsData", "seq": 9, "event_id": "m1",
                      "payload": {"processor": "P#0", "model": None, "value": 12345}}).replace("12345", bad)
    p.write_text(p.read_text() + raw + "\n")
    assert any("value" in e for e in load_run(p).input_errors)


def test_same_event_id_with_other_content_is_a_conflict(tmp_path):
    log = Log()
    for k in range(5):
        log.turn("s1", f"u{k + 1}", 0.4)
    p = log.write(tmp_path / "a.jsonl")
    clash = dict(log.lines[0], payload=dict(log.lines[0]["payload"], total_secs=0.9))  # same event_id
    p.write_text(p.read_text() + json.dumps(clash) + "\n")
    run = load_run(p)
    assert any("different content" in e for e in run.input_errors)
    assert run.duplicates_ignored == 0


def test_a_log_that_is_not_utf8_is_an_input_error(tmp_path):
    a = uniform(tmp_path / "a.jsonl", [0.4] * 300)
    b = uniform(tmp_path / "b.jsonl", [0.4] * 300, run_id="r2", config_id="c2")
    b.write_bytes(b.read_bytes() + b"\xff\xfe\x00 not utf-8\n")
    assert load_run(b).input_errors
    r = _cli("compare", str(a), str(b), "--policy", POLICY)
    assert r.returncode == 2 and "Traceback" not in r.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_unwritable_report_exits_2_not_1(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        r = _cli("compare", "fixtures/baseline.jsonl", "fixtures/baseline.jsonl", "--policy", POLICY,
                 "--report", str(ro / "sub" / "report.json"))
    finally:
        ro.chmod(stat.S_IRWXU)
    assert r.returncode == 2 and "INCOMPLETE" in r.stderr and "Traceback" not in r.stderr


@pytest.mark.parametrize("content", ["{not json", '{"command": "compare"}', "[]"])
def test_show_on_a_broken_report_exits_2(tmp_path, content):
    p = tmp_path / "report.json"
    p.write_text(content)
    r = _cli("show", str(p))
    assert r.returncode == 2 and "Traceback" not in r.stderr


# -- TTFA, TTFAT and traces -------------------------------------------------------


def test_ttfa_and_ttfat_count_once_per_processor_and_turn(tmp_path):
    log = Log()
    for _ in range(3):  # three sentences in one turn
        log.metric("s1", "u1", "responded", "TTFAMetricsData", processor="TTS#0", ttfa=0.15, ttfb=0.1, leading_silence=0.05)
        log.metric("s1", "u1", "responded", "TTFATMetricsData", processor="LLM#0", ttfat=0.3, ttfb=0.1, thinking_time=0.2)
    log.turn("s1", "u1", 0.5)
    s = summarize(load_run(log.write(tmp_path / "a.jsonl")), min_n=1)
    assert next(x for x in s["services"] if x["metric"] == "ttfa")["n"] == 1
    assert next(x for x in s["services"] if x["metric"] == "ttfat")["n"] == 1


def test_trace_keeps_a_start_time_of_zero(tmp_path):
    from voice_check.analysis import _trace

    b = breakdown(0.3, parts=[("transcription", 0.1, 0.1), ("llm_inference", 0.2, 0.2)], t0=0.0)
    assert b["user_turn_start_time"] == 0.0
    assert [x["start_ms"] for x in _trace(b)] == [100.0, 200.0]


# -- median and terminal output ------------------------------------------------------


def test_median_of_an_even_count_is_the_mean_of_the_two_middle_values(tmp_path):
    log = Log()
    for i, t in enumerate((0.1, 0.2, 0.3, 0.4)):
        log.turn("s1", f"u{i + 1}", t)
    s = summarize(load_run(log.write(tmp_path / "a.jsonl")), min_n=1)
    assert s["total"]["median_ms"] == 250.0


def test_terminal_shows_reasonable_precision_and_no_none(tmp_path):
    rep = single(load_run(tmp_path / "missing.jsonl"), min_n=300)
    out = render(rep)
    assert "None" not in out and "log not found" in out
    r = _cli("summarize", "capture/out/local-capture.jsonl")
    assert r.returncode == 0
    assert "7,856 ms" in r.stdout and "7,855.812" not in r.stdout
    missing = _cli("summarize", str(tmp_path / "nope.jsonl"))
    assert missing.returncode == 2 and "None" not in missing.stdout
