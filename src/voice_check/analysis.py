"""Turn a parsed run (or two) into report.json. Every number here is computed by
code from the logs; every sentence is a fixed template filled with those
numbers. Nothing is written by a language model.
"""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from . import envelope as E
from .parse import Run, Turn

INF = math.inf
REPORT_SCHEMA = 1

# ---------------------------------------------------------------------------
# numbers


def to_us(secs: float) -> int:
    """Seconds (native Pipecat unit) to integer microseconds, so 0.42 and
    0.41999999997 compare equal against a 420 ms threshold."""
    return round(secs * 1_000_000)


def us_to_ms(us: float | int | None) -> float | None:
    if us is None or us == INF:
        return None
    return round(us / 1000, 3)


def nearest_rank(sorted_values: list, pct: int):
    """Nearest-rank percentile: the value at rank ceil(pct/100 * n), 1-based."""
    n = len(sorted_values)
    rank = -(-pct * n // 100)
    return sorted_values[max(rank, 1) - 1]


def distribution(values_us: list, min_n: int) -> dict:
    """Median and p95 with their N. ``inf`` stands for a failed turn: it stays
    in the denominator and sorts above every measured value."""
    vals = sorted(values_us)
    n = len(vals)
    out: dict[str, Any] = {"n": n}
    if n == 0:
        out.update(median_ms=None, p95_ms=None, p95_note="p95 not shown (N = 0)")
        return out
    med = nearest_rank(vals, 50)
    out["median_ms"] = us_to_ms(med)
    out["median_is_failed_turn"] = med == INF
    if n < min_n:
        out["p95_ms"] = None
        out["p95_note"] = f"p95 not shown (N = {n})"
    else:
        p95 = nearest_rank(vals, 95)
        out["p95_ms"] = us_to_ms(p95)
        out["p95_is_failed_turn"] = p95 == INF
        out["_p95_us"] = p95
    finite = [v for v in vals if v != INF]
    out["min_ms"] = us_to_ms(finite[0]) if finite else None
    out["max_ms"] = us_to_ms(finite[-1]) if finite else None
    out["mean_ms_measured_only"] = us_to_ms(sum(finite) / len(finite)) if finite else None
    return out


# ---------------------------------------------------------------------------
# policy


class PolicyError(Exception):
    pass


def load_policy(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise PolicyError(f"policy not found: {path}")
    try:
        p = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise PolicyError(f"policy is not valid YAML: {exc}") from exc
    if not isinstance(p, dict) or p.get("schema") != "voice-check.policy" or p.get("schema_version") != 1:
        raise PolicyError("policy: unknown schema or schema_version")
    rule = p.get("rule") or {}
    if rule.get("measure") != "turn_total" or rule.get("statistic") != "p95" or rule.get("operator") != "<=":
        raise PolicyError("policy: the only supported rule is turn_total p95 <= threshold_ms")
    if not isinstance(rule.get("threshold_ms"), (int, float)) or rule["threshold_ms"] <= 0:
        raise PolicyError("policy: rule.threshold_ms must be a positive number")
    for k in ("min_measured_turns", "max_incomplete_turns"):
        if not isinstance(p.get(k), int) or p[k] < 0:
            raise PolicyError(f"policy: {k} must be a non-negative integer")
    p["_path"] = str(path)
    return p


def _policy_view(policy: dict) -> dict:
    return {
        "path": policy["_path"],
        "name": policy.get("name"),
        "source": policy.get("source"),
        "nature": policy.get("nature"),
        "note": policy.get("note"),
        "rule": policy["rule"],
        "min_measured_turns": policy["min_measured_turns"],
        "min_measured_turns_note": "A configurable floor, not a statistical guarantee.",
        "max_incomplete_turns": policy["max_incomplete_turns"],
        "published_breakdown": policy.get("published_breakdown"),
        "published_breakdown_evaluated": False,
    }


# ---------------------------------------------------------------------------
# measurement scope (what the numbers cover, and what they do not)

SCOPE = {
    "measures": [
        {
            "name": "turn total (LatencyBreakdown.total_secs)",
            "start_event": "user fell silent, as Pipecat estimates it: VADUserStoppedSpeakingFrame.timestamp minus the VAD stop_secs",
            "end_event": "BotStartedSpeakingFrame seen in the bot's pipeline",
            "scope": "SDK-observed",
        },
        {
            "name": "contributions (LatencyBreakdown.contributions)",
            "start_event": "the Pipecat moment that opens each span (for example LLMFullResponseStartFrame for LLM inference)",
            "end_event": "the Pipecat moment that closes it (for example the LLM's first chunk, worked back from its TTFB)",
            "scope": "SDK-observed",
        },
    ],
    "not_measured": [
        "audio travelling from the bot to the caller, buffering and playback on the caller's device (client-side)",
        "barge-in stop latency (no reliable Pipecat metric)",
    ],
}


# ---------------------------------------------------------------------------
# one run


def _contrib_by_key(breakdown: dict) -> dict[str, dict]:
    """Per contribution key, the wall-clock time covered. Spans of the same key
    are merged as intervals, so overlapping spans are not counted twice."""
    spans: dict[str, list[tuple[float, float]]] = defaultdict(list)
    meta: dict[str, dict] = {}
    for c in breakdown["contributions"]:
        spans[c["key"]].append((c["start_time"], c["start_time"] + c["duration_secs"]))
        meta.setdefault(c["key"], {"label": c["label"], "owner": c["owner"], "owner_kind": c["owner_kind"]})
    out = {}
    for key, ivs in spans.items():
        ivs.sort()
        covered, cur_s, cur_e = 0.0, None, None
        for s, e in ivs:
            if cur_e is None or s > cur_e:
                if cur_e is not None:
                    covered += cur_e - cur_s
                cur_s, cur_e = s, e
            else:
                cur_e = max(cur_e, e)
        covered += cur_e - cur_s
        out[key] = {**meta[key], "secs": covered}
    return out


def _service(name: str) -> str:
    """Group a processor across sessions: each pipeline numbers its own
    instances (OpenAILLMService#0, #1, ...), so the #N suffix is dropped."""
    return name.split("#", 1)[0]


def _model_label(model) -> str:
    return model if isinstance(model, str) and model.strip() else "model not reported"


def _turn_value_us(turn: Turn | None) -> float | None:
    """Microseconds for a measured turn, inf for a failed one, None otherwise."""
    if turn is None:
        return None
    if turn.status in E.RESPONDED:
        return to_us(turn.total_secs)
    if turn.status in E.FAILED:
        return INF
    return None


def summarize(run: Run, *, min_n: int, threshold_ms: float | None = None) -> dict:
    expected = run.expected
    observed = run.turns
    counts = {
        "sessions_expected": len(run.expected_sessions),
        "sessions_observed": len({k[0] for k in observed}),
        "turns_expected": len(expected),
        "turns_observed": len(observed),
        "turns_measured": sum(t.status in E.RESPONDED for t in observed.values()),
        "responded_then_interrupted": sum(t.status == "responded_then_interrupted" for t in observed.values()),
        "failed_no_audio": sum(t.status == "no_audio" for t in observed.values()),
        "failed_no_response": sum(t.status == "no_response" for t in observed.values()),
        "interrupted_before_response": sum(t.status == "interrupted_before_response" for t in observed.values()),
        "missing_from_log": sum(k not in observed for k in expected),
        "greetings_kept_apart": len(run.greetings),
        "unanchored_breakdowns": run.unanchored_breakdowns,
        "no_audio_errors_in_responded_turns": sum(
            1 for t in observed.values() if t.status in E.RESPONDED
            for e in t.errors if E.NO_AUDIO_MARKER in (e["payload"].get("error") or "")
        ),
        "duplicate_lines_ignored": run.duplicates_ignored,
        "lines_read": run.lines_read,
    }
    counts["incomplete_turns"] = counts["interrupted_before_response"] + counts["missing_from_log"]
    counts["failed_turns"] = counts["failed_no_audio"] + counts["failed_no_response"]

    values = [v for k in expected if (v := _turn_value_us(observed.get(k))) is not None]
    total = distribution(values, min_n)
    total["n_measured"] = counts["turns_measured"]
    total["n_failed"] = counts["failed_turns"]
    if threshold_ms is not None:
        thr = round(threshold_ms * 1000)
        total["over_threshold"] = sum(1 for v in values if v > thr)

    # contributions, per key, over measured turns
    per_key: dict[str, list[int]] = defaultdict(list)
    labels: dict[str, dict] = {}
    absent: dict[str, int] = defaultdict(int)
    zeros = dict(run.zeros)
    for k in expected:
        t = observed.get(k)
        if not t or not t.breakdown:
            continue
        by_key = _contrib_by_key(t.breakdown)
        for key, c in by_key.items():
            per_key[key].append(to_us(c["secs"]))
            labels.setdefault(key, {"label": c["label"], "owners": set()})["owners"].add(c["owner"])
        for key in E.CORE_USER_TURN_KEYS:
            if key not in by_key:
                absent[key] += 1
                zeros["absent"] += 1
        if t.breakdown["user_turn_secs"] is None:
            zeros["absent"] += 1
        if not t.breakdown["function_calls"]:
            zeros["not_applicable"] += 1
        if t.breakdown["text_aggregation"] is None:
            zeros["not_applicable"] += 1

    contributions = []
    order = E.CORE_USER_TURN_KEYS + sorted(k for k in per_key if k not in E.CORE_USER_TURN_KEYS)
    for key in order:
        if key not in per_key and key not in absent:
            continue
        d = distribution(per_key.get(key, []), min_n)
        d.pop("_p95_us", None)
        contributions.append(
            {
                "key": key,
                "label": labels.get(key, {}).get("label", key.replace("_", " ")),
                "owners": sorted(labels.get(key, {}).get("owners", [])),
                "absent_turns": absent.get(key, 0),
                **d,
            }
        )

    # service metrics: TTFB from the breakdown (first per processor and turn),
    # TTFA and TTFAT from metrics lines; their ``ttfb`` copy is never re-counted.
    ttfb: dict[tuple[str, str], list[int]] = defaultdict(list)
    ttfa: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    ttfat: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    usage_seen = False
    for k in expected:
        t = observed.get(k)
        if not t:
            continue
        seen_proc = set()
        if t.breakdown:
            for item in t.breakdown["ttfb"]:
                if item["processor"] in seen_proc:
                    continue
                seen_proc.add(item["processor"])
                ttfb[(_service(item["processor"]), _model_label(item["model"]))].append(to_us(item["duration_secs"]))
        for line in t.metrics:
            p, nt = line["payload"], line["native_type"]
            if nt == "TTFBMetricsData" and not t.breakdown and p["processor"] not in seen_proc:
                seen_proc.add(p["processor"])
                ttfb[(_service(p["processor"]), _model_label(p["model"]))].append(to_us(p["value"]))
            elif nt == "TTFAMetricsData":
                ttfa[_service(p["processor"])]["ttfa"].append(to_us(p["ttfa"]))
                ttfa[_service(p["processor"])]["leading_silence"].append(to_us(p["leading_silence"]))
            elif nt == "TTFATMetricsData":
                ttfat[_service(p["processor"])]["ttfat"].append(to_us(p["ttfat"]))
                ttfat[_service(p["processor"])]["thinking_time"].append(to_us(p["thinking_time"]))
            elif nt == "LLMUsageMetricsData":
                usage_seen = True

    def _clean(d):
        d.pop("_p95_us", None)
        return d

    services = [
        {"processor": proc, "model": model, "metric": "ttfb", "source": "LatencyBreakdown.ttfb, first per turn", **_clean(distribution(v, min_n))}
        for (proc, model), v in sorted(ttfb.items())
    ]
    for proc, parts in sorted(ttfa.items()):
        services.append(
            {
                "processor": proc,
                "metric": "ttfa",
                "note": "TTFA = TTFB + leading_silence; its ttfb field copies the TTFB above and is not counted again",
                **_clean(distribution(parts["ttfa"], min_n)),
                "leading_silence": _clean(distribution(parts["leading_silence"], min_n)),
            }
        )
    for proc, parts in sorted(ttfat.items()):
        services.append(
            {
                "processor": proc,
                "metric": "ttfat",
                "note": "TTFAT = TTFB + thinking_time; its ttfb field copies the TTFB above and is not counted again",
                **_clean(distribution(parts["ttfat"], min_n)),
                "thinking_time": _clean(distribution(parts["thinking_time"], min_n)),
            }
        )

    greet = distribution([to_us(g["breakdown"]["total_secs"]) for g in run.greetings], min_n)
    greet.pop("_p95_us", None)
    manifest = run.manifest or {}
    return {
        "path": run.path,
        "manifest": run.manifest_path,
        "run_id": run.meta.get("run_id"),
        "config_id": run.meta.get("config_id"),
        "scenario_id": run.meta.get("scenario_id"),
        "provenance": run.meta.get("provenance"),
        "inference_backend": run.meta.get("inference_backend", "unknown"),
        "framework": run.meta.get("framework"),
        "framework_version": run.meta.get("framework_version"),
        "conditions": manifest.get("conditions"),
        "config": manifest.get("config"),
        "counts": counts,
        "total": total,
        "greetings": {"note": "Measured from client connected; never mixed with user turns.", **greet},
        "contributions": contributions,
        "services": services,
        "llm_usage_metrics_present": usage_seen,
        "zero_and_missing": zeros,
        "input_errors": run.input_errors[:50],
        "input_error_count": len(run.input_errors),
    }


def turn_table(run: Run) -> list[dict]:
    rows = []
    for k in run.expected:
        t = run.turns.get(k)
        row = {"session_id": k[0], "turn_id": k[1], "status": t.status if t else "missing_from_log"}
        if t and t.breakdown:
            row["total_ms"] = us_to_ms(to_us(t.total_secs))
            row["contributions_ms"] = {key: us_to_ms(to_us(c["secs"])) for key, c in _contrib_by_key(t.breakdown).items()}
        else:
            row["total_ms"] = None
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# attribution templates: compatible explanations, never a verdict on the cause

HYPOTHESES = {
    "llm_inference": [
        "The LLM service took longer to return its first chunk on these turns.",
        "The requests on these turns differed (prompt length, tools, model or sampling settings).",
        "The inference server queued these requests behind others (load, batching, a slower replica).",
    ],
    "transcription": [
        "The STT service finalized these transcripts later.",
        "The audio on these turns differed (length, noise, codec) in a way the STT is sensitive to.",
    ],
    "speech_synthesis": [
        "The TTS service took longer to return first audio on these turns.",
        "The text handed to the TTS differed (first sentence length, aggregation settings).",
    ],
    "endpointing_wait": ["The VAD stop_secs setting differs between the two configurations."],
    "turn_detection": ["The user turn strategy (a timeout or a turn model) waited longer before releasing the turn."],
    "output_transport": ["The output transport took longer between first audio and the bot starting to speak."],
    "function_handler": ["A function handler ran longer on these turns."],
    "pipeline": ["Time between named stages grew: frame hops, scheduling, or a stage Pipecat does not name."],
}

MISSING = {
    "llm_inference": [
        "server-side timings from the inference backend for these requests (queue time, prefill time)",
        "which host or replica served each session",
    ],
    "transcription": ["the STT service's own timings for these utterances", "the input audio of the affected turns"],
    "speech_synthesis": ["the TTS service's own timings for these requests"],
}


def _attribution(base: Run, cand: Run, threshold_ms: float, cand_manifest: dict | None) -> dict:
    thr = round(threshold_ms * 1000)
    affected = []
    for k in cand.expected:
        ct = cand.turns.get(k)
        v = _turn_value_us(ct)
        if v is None or v <= thr:
            continue
        bt = base.turns.get(k)
        bv = _turn_value_us(bt)
        row = {
            "session_id": k[0],
            "turn_id": k[1],
            "status": ct.status,
            "candidate_total_ms": us_to_ms(v),
            "baseline_total_ms": us_to_ms(bv) if bv is not None else None,
            "delta_ms": us_to_ms(v - bv) if (bv is not None and v != INF and bv != INF) else None,
        }
        if ct.breakdown and bt and bt.breakdown:
            cb, bb = _contrib_by_key(ct.breakdown), _contrib_by_key(bt.breakdown)
            row["contribution_delta_ms"] = {
                key: us_to_ms(to_us(cb.get(key, {}).get("secs", 0.0)) - to_us(bb.get(key, {}).get("secs", 0.0)))
                for key in sorted(set(cb) | set(bb))
                if key in cb and key in bb
            }
            row["trace"] = {
                "baseline": _trace(bt.breakdown),
                "candidate": _trace(ct.breakdown),
            }
        affected.append(row)
    affected.sort(key=lambda r: (-(r["delta_ms"] or 0), r["session_id"], r["turn_id"]))

    paired = [r for r in affected if r.get("contribution_delta_ms")]
    total_delta = sum(r["delta_ms"] for r in paired if r["delta_ms"] and r["delta_ms"] > 0)
    by_key: dict[str, float] = defaultdict(float)
    for r in paired:
        for key, d in r["contribution_delta_ms"].items():
            if d and d > 0:
                by_key[key] += d
    changed = []
    for key, s in sorted(by_key.items(), key=lambda kv: -kv[1]):
        share = s / total_delta if total_delta else 0.0
        changed.append({"contribution_key": key, "sum_delta_ms": round(s, 3), "share_of_total_delta": round(share, 4)})

    primary = changed[0]["contribution_key"] if changed and changed[0]["share_of_total_delta"] >= 0.5 else None
    associated = []
    if primary:
        owners = set()
        for r in paired:
            owners |= {c["owner"] for c in cand.turns[(r["session_id"], r["turn_id"])].breakdown["contributions"] if c["key"] == primary}
        for owner in sorted(owners):
            associated.append({"metric": "ttfb", "processor": owner})

    sessions_hit = sorted({r["session_id"] for r in affected})
    hit = set(sessions_hit)
    turns_in_hit = [k for k in cand.expected if k[0] in hit]
    affected_keys = {(r["session_id"], r["turn_id"]) for r in affected}
    whole_sessions = bool(hit) and all(k in affected_keys for k in turns_in_hit)
    by_position: dict[str, int] = defaultdict(int)
    for r in affected:
        by_position[r["turn_id"]] += 1

    facts = {
        "affected_turns": len(affected),
        "affected_sessions": len(sessions_hit),
        "sessions_expected": len(cand.expected_sessions),
        "every_turn_of_those_sessions_affected": whole_sessions,
        "affected_by_turn_position": dict(sorted(by_position.items())),
        "paired_with_baseline": len(paired),
    }
    missing = list(MISSING.get(primary, [])) if primary else ["a contribution that accounts for most of the slowdown"]
    usage_logged = any(l["native_type"] == "LLMUsageMetricsData" for t in cand.turns.values() for l in t.metrics)
    if primary == "llm_inference" and not usage_logged:
        missing.append("prompt token counts per turn (Pipecat LLMUsageMetricsData, enable_usage_metrics=True), absent from these logs")

    known = None
    if cand_manifest and cand_manifest.get("provenance") == "synthetic" and cand_manifest.get("injected_change"):
        ic = cand_manifest["injected_change"]
        known = {
            "description": ic["description"],
            "matches_diagnosis": primary == ic.get("contribution_key") and set(sessions_hit) == set(ic.get("sessions", [])),
            "note": "Named only because this scenario is synthetic and the change was injected on purpose.",
        }
    return {
        "changed_measures": changed,
        "associated_metrics": associated,
        "primary_contribution": primary,
        "facts": facts,
        "hypotheses_compatible": HYPOTHESES.get(primary, []) if primary else [],
        "hypotheses_note": "Compatible with these logs, not confirmed by them.",
        "missing_to_decide": missing,
        "known_cause": known,
        "affected_turns": affected,
    }


def _trace(breakdown: dict) -> list[dict]:
    """One turn's own contributions, in Pipecat's order, as offsets from the
    start of the interval. A single measured turn, never a percentile."""
    contribs = breakdown["contributions"]
    if not contribs:
        return []
    t0 = breakdown["user_turn_start_time"] or contribs[0]["start_time"]
    return [
        {
            "key": c["key"],
            "label": c["label"],
            "owner": c["owner"],
            "start_ms": us_to_ms(to_us(c["start_time"] - t0)) if c["key"] != "pipeline" else None,
            "duration_ms": us_to_ms(to_us(c["duration_secs"])),
        }
        for c in contribs
    ]


# ---------------------------------------------------------------------------
# two runs


INCOMPLETE_TEXT = {
    "input_error": "INCOMPLETE: input error",
    "not_comparable": "INCOMPLETE: runs are not comparable",
    "coverage": "INCOMPLETE: expected turns are missing or unmeasured",
    "too_few_turns": "INCOMPLETE: too few turns for a p95",
}


def compare(base: Run, cand: Run, policy: dict) -> dict:
    thr_ms = policy["rule"]["threshold_ms"]
    min_n = policy["min_measured_turns"]
    reasons: list[dict] = []

    for name, run in (("baseline", base), ("candidate", cand)):
        if run.input_errors:
            reasons.append({"code": "input_error", "run": name, "detail": f"{len(run.input_errors)} input error(s); first: {run.input_errors[0]}"})

    sb = summarize(base, min_n=min_n, threshold_ms=thr_ms)
    sc = summarize(cand, min_n=min_n, threshold_ms=thr_ms)

    comparability = {
        "same_scenario": sb["scenario_id"] == sc["scenario_id"],
        "same_expected_turns": set(base.expected) == set(cand.expected),
        "same_provenance": sb["provenance"] == sc["provenance"],
        "differences": {},
    }
    for k in ("config_id", "framework_version", "inference_backend"):
        if sb[k] != sc[k]:
            comparability["differences"][k] = {"baseline": sb[k], "candidate": sc[k]}
    for section in ("config", "conditions"):
        b, c = sb.get(section) or {}, sc.get(section) or {}
        for k in sorted(set(b) | set(c)):
            if b.get(k) != c.get(k):
                comparability["differences"][f"{section}.{k}"] = {"baseline": b.get(k), "candidate": c.get(k)}
    if not any(r["code"] == "input_error" for r in reasons):
        if not (comparability["same_scenario"] and comparability["same_expected_turns"] and comparability["same_provenance"]):
            reasons.append({"code": "not_comparable", "detail": "scenario, expected turns or provenance differ"})

    if not reasons:
        for name, s in (("baseline", sb), ("candidate", sc)):
            inc = s["counts"]["incomplete_turns"]
            if inc > policy["max_incomplete_turns"]:
                reasons.append(
                    {
                        "code": "coverage",
                        "run": name,
                        "detail": (
                            f"{inc} of {s['counts']['turns_expected']} expected turns have no measurement "
                            f"({s['counts']['missing_from_log']} missing from the log, "
                            f"{s['counts']['interrupted_before_response']} interrupted before a response); "
                            f"the policy allows {policy['max_incomplete_turns']}"
                        ),
                    }
                )
            elif s["total"]["n"] < min_n:
                reasons.append({"code": "too_few_turns", "run": name, "detail": f"N = {s['total']['n']}, the policy asks for {min_n}"})

    thr_us = round(thr_ms * 1000)
    evaluation = None
    if reasons:
        verdict, code = "INCOMPLETE", 2
        label = INCOMPLETE_TEXT[reasons[0]["code"]]
    else:
        p95_us = sc["total"]["_p95_us"]
        passed = p95_us <= thr_us
        verdict, code = ("PASS", 0) if passed else ("FAIL", 1)
        label = "Latency policy passed" if passed else "FAIL: configured latency threshold exceeded"
        evaluation = {
            "run": "candidate",
            "measure": "turn_total",
            "statistic": "p95",
            "value_ms": us_to_ms(p95_us),
            "value_is_failed_turn": p95_us == INF,
            "n": sc["total"]["n"],
            "operator": "<=",
            "threshold_ms": thr_ms,
            "compared_at": "microsecond",
            "passed": passed,
            "baseline_value_ms": us_to_ms(sb["total"]["_p95_us"]),
            "baseline_passed": sb["total"]["_p95_us"] <= thr_us,
        }
    for s in (sb, sc):
        s["total"].pop("_p95_us", None)

    change = None
    if not any(r["code"] in ("input_error", "not_comparable") for r in reasons):
        change = _attribution(base, cand, thr_ms, cand.manifest)

    def plot(run: Run):
        return [
            [k[0], k[1], (us_to_ms(v) if (v := _turn_value_us(run.turns.get(k))) not in (None, INF) else None),
             run.turns[k].status if k in run.turns else "missing_from_log"]
            for k in run.expected
        ]

    return {
        "tool": "voice-check",
        "tool_version": __version__,
        "report_schema": REPORT_SCHEMA,
        "unit": "ms",
        "units_note": "Pipecat reports seconds as floats; converted to ms here only, compared at the microsecond.",
        "command": "compare",
        "verdict": verdict,
        "exit_code": code,
        "label": label,
        "label_note": "A latency check only; it says nothing about conversation quality.",
        "reasons": reasons,
        "policy": _policy_view(policy),
        "scope": SCOPE,
        "comparability": comparability,
        "evaluation": evaluation,
        "runs": {"baseline": sb, "candidate": sc},
        "change": change,
        "turns": {"columns": ["session_id", "turn_id", "total_ms", "status"], "baseline": plot(base), "candidate": plot(cand)},
    }


def single(run: Run, *, min_n: int) -> dict:
    s = summarize(run, min_n=min_n)
    s["total"].pop("_p95_us", None)
    ok = not run.input_errors
    return {
        "tool": "voice-check",
        "tool_version": __version__,
        "report_schema": REPORT_SCHEMA,
        "unit": "ms",
        "units_note": "Pipecat reports seconds as floats; converted to ms here only.",
        "command": "summarize",
        "verdict": None,
        "exit_code": 0 if ok else 2,
        "label": "Run summarized" if ok else "INCOMPLETE: input error",
        "min_measured_turns": min_n,
        "scope": SCOPE,
        "run": s,
        "turn_table": turn_table(run),
        "greeting_table": [
            {"session_id": g["session_id"], "total_ms": us_to_ms(to_us(g["breakdown"]["total_secs"])),
             "contributions_ms": {k: us_to_ms(to_us(c["secs"])) for k, c in _contrib_by_key(g["breakdown"]).items()}}
            for g in run.greetings
        ],
    }
