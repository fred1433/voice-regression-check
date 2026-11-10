"""Parser cases relevés in pipecat-ai 1.15.0 (docs/pipecat-1.15.0-sources.md)."""

import json

from builders import Log, breakdown

from voice_check.analysis import single, summarize
from voice_check.parse import load_run


def _summary(path, min_n=1):
    return summarize(load_run(path), min_n=min_n)


def test_initial_zero_ttfb_is_initialization_not_a_measurement(tmp_path):
    log = Log()
    # PipelineWorker._initial_metrics_frame: TTFB 0.0 for every processor, outside any turn
    log.metric("s1", None, None, "TTFBMetricsData", processor="OpenAILLMService#0", value=0.0)
    log.metric("s1", None, None, "ProcessingMetricsData", processor="OpenAILLMService#0", value=0.0)
    log.metric("s1", "u1", "responded", "TTFBMetricsData", processor="OpenAILLMService#0", model="m", value=0.12)
    log.turn("s1", "u1", 0.4, ttfb=[("OpenAILLMService#0", "m", 0.12)])
    s = _summary(log.write(tmp_path / "a.jsonl"))
    assert s["zero_and_missing"]["initialization"] == 1
    ttfb = [x for x in s["services"] if x["metric"] == "ttfb"]
    assert len(ttfb) == 1 and ttfb[0]["n"] == 1 and ttfb[0]["median_ms"] == 120.0


def test_true_zero_inside_a_turn_is_kept_as_zero(tmp_path):
    log = Log()
    log.metric("s1", "u1", "no_response", "TTFBMetricsData", processor="P#0", value=0.0)
    s = _summary(log.write(tmp_path / "a.jsonl"))
    assert s["zero_and_missing"]["zero_value"] == 1
    ttfb = [x for x in s["services"] if x["metric"] == "ttfb"]
    assert ttfb[0]["median_ms"] == 0.0 and ttfb[0]["n"] == 1


def test_absent_and_not_applicable_are_never_zero(tmp_path):
    log = Log()
    # only llm_inference: the other core stages are absent, function calls and
    # text aggregation do not apply, user_turn_secs is absent
    log.turn("s1", "u1", 0.4, parts=[("llm_inference", 0.4)], user_turn_secs=None)
    s = _summary(log.write(tmp_path / "a.jsonl"))
    z = s["zero_and_missing"]
    assert z["absent"] == 4 + 1  # transcription, endpointing, turn detection, synthesis + user_turn_secs
    assert z["not_applicable"] == 2
    tr = next(c for c in s["contributions"] if c["key"] == "transcription")
    assert tr["n"] == 0 and tr["median_ms"] is None and tr["absent_turns"] == 1


def test_model_empty_or_null_is_labelled_not_invented(tmp_path):
    log = Log()
    log.turn("s1", "u1", 0.4, ttfb=[("OpenAILLMService#0", None, 0.1)])
    log.turn("s1", "u2", 0.4, ttfb=[("OpenAILLMService#0", "", 0.2)])
    log.turn("s1", "u3", 0.4, ttfb=[("OpenAILLMService#0", "qwen2.5:3b", 0.3)])
    s = _summary(log.write(tmp_path / "a.jsonl"))
    models = {(x["processor"], x["model"]): x["n"] for x in s["services"] if x["metric"] == "ttfb"}
    assert models == {("OpenAILLMService", "model not reported"): 2, ("OpenAILLMService", "qwen2.5:3b"): 1}


def test_ttfa_and_ttfat_ttfb_copy_is_not_counted_twice_and_parts_are_kept(tmp_path):
    log = Log()
    log.metric("s1", "u1", "responded", "TTFBMetricsData", processor="TTS#0", value=0.100)
    log.metric("s1", "u1", "responded", "TTFAMetricsData", processor="TTS#0", ttfa=0.150, ttfb=0.100, leading_silence=0.050)
    log.metric("s1", "u1", "responded", "TTFATMetricsData", processor="LLM#0", ttfat=0.320, ttfb=0.120, thinking_time=0.200)
    log.turn("s1", "u1", 0.5, ttfb=[("TTS#0", None, 0.100), ("LLM#0", None, 0.120)])
    s = _summary(log.write(tmp_path / "a.jsonl"))
    ttfb = {x["processor"] + "#0": x for x in s["services"] if x["metric"] == "ttfb"}
    assert ttfb["TTS#0"]["n"] == 1 and ttfb["TTS#0"]["median_ms"] == 100.0
    assert ttfb["LLM#0"]["n"] == 1 and ttfb["LLM#0"]["median_ms"] == 120.0
    ttfa = next(x for x in s["services"] if x["metric"] == "ttfa")
    assert ttfa["median_ms"] == 150.0 and ttfa["leading_silence"]["median_ms"] == 50.0
    ttfat = next(x for x in s["services"] if x["metric"] == "ttfat")
    assert ttfat["median_ms"] == 320.0 and ttfat["thinking_time"]["median_ms"] == 200.0
    # the turn total is the measured one, never a sum of service metrics
    assert s["total"]["median_ms"] == 500.0


def test_completed_with_no_audio_keeps_reading_and_stays_in_denominator(tmp_path):
    log = Log()
    log.turn("s1", "u1", 0.4)
    log.add("s1", "u2", "no_audio", "error", "ErrorFrame",
            {"error": "TTS context ctx-2 completed with no audio", "fatal": False, "category": None, "processor": "KokoroTTSService#0"})
    log.turn("s1", "u3", 0.41)
    s = _summary(log.write(tmp_path / "a.jsonl"))
    assert s["input_error_count"] == 0
    assert s["counts"]["turns_measured"] == 2 and s["counts"]["failed_no_audio"] == 1
    assert s["total"]["n"] == 3  # the silent turn is in the denominator
    assert s["total"]["n_failed"] == 1


def test_inference_backend_is_declared_never_inferred(tmp_path):
    log = Log(inference_backend="unknown")
    log.turn("s1", "u1", 0.4, ttfb=[("OpenAILLMService#0", "meta-llama/Llama-3.1-8B-Instruct", 0.1)])
    s = _summary(log.write(tmp_path / "a.jsonl"))
    assert s["inference_backend"] == "unknown"
    assert "vllm" not in json.dumps(s).lower()


def test_interruptions_are_a_status_not_a_timing(tmp_path):
    log = Log()
    log.turn("s1", "u1", 0.4, status="responded_then_interrupted")
    log.add("s1", "u2", "interrupted_before_response", "metrics", "TTFBMetricsData", {"processor": "P#0", "model": None, "value": 0.2})
    s = _summary(log.write(tmp_path / "a.jsonl"))
    c = s["counts"]
    assert c["responded_then_interrupted"] == 1 and c["turns_measured"] == 1
    assert c["interrupted_before_response"] == 1 and c["incomplete_turns"] == 1
    assert "barge" not in json.dumps(s["contributions"]).lower()


def test_unanchored_breakdown_total_zero_is_not_a_zero_latency_turn(tmp_path):
    log = Log()
    # Pipecat emits measured_from None and total_secs 0.0 when it could not anchor the cycle
    log.add("s1", "u1", "no_response", "latency_breakdown", "LatencyBreakdown",
            {**breakdown(0.0, parts=[]), "measured_from": None, "user_turn_start_time": None})
    s = _summary(log.write(tmp_path / "a.jsonl"))
    assert s["counts"]["unanchored_breakdowns"] == 1
    assert s["counts"]["turns_measured"] == 0
    assert s["total"]["min_ms"] is None  # no 0 ms anywhere


def test_three_turn_report_has_no_numeric_p95(tmp_path):
    log = Log()
    for i, t in enumerate((0.9, 1.1, 1.3)):
        log.turn("s1", f"u{i + 1}", t)
    rep = single(load_run(log.write(tmp_path / "a.jsonl")), min_n=300)

    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                if "p95" in k and isinstance(v, (int, float)) and not isinstance(v, bool):
                    raise AssertionError(f"numeric p95 at {path}.{k}")
                walk(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")

    walk(rep)
    assert rep["run"]["total"]["p95_note"] == "p95 not shown (N = 3)"
    assert rep["run"]["total"]["median_ms"] == 1100.0
    assert rep["run"]["total"]["min_ms"] == 900.0 and rep["run"]["total"]["max_ms"] == 1300.0
