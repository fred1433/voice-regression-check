"""Hand-built envelope lines for parser edge cases (no Pipecat needed)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

META = {
    "schema": "voice-check.envelope",
    "schema_version": 1,
    "framework": "pipecat-ai",
    "framework_version": "1.15.0",
    "run_id": "r",
    "config_id": "c",
    "scenario_id": "scn",
    "provenance": "synthetic",
    "inference_backend": "unknown",
}


def breakdown(total, parts=None, *, measured_from="user_silence", ttfb=(), user_turn_secs=0.1, t0=100.0, function_calls=()):
    parts = parts if parts is not None else [("llm_inference", total)]
    contributions, t = [], t0
    for item in parts:
        key, secs = item[0], item[1]
        start = item[2] if len(item) > 2 else t
        contributions.append(
            {"key": key, "label": key.replace("_", " "), "owner": "OpenAILLMService#0" if key == "llm_inference" else "x",
             "owner_kind": "service", "start_time": start, "duration_secs": secs}
        )
        t = start + secs
    return {
        "ttfb": [{"processor": p, "model": m, "start_time": t0, "duration_secs": s} for p, m, s in ttfb],
        "contributions": contributions,
        "measured_from": measured_from,
        "total_secs": total,
        "text_aggregation": None,
        "user_turn_start_time": t0 if measured_from == "user_silence" else None,
        "user_turn_secs": user_turn_secs,
        "function_calls": [{"function_name": f, "start_time": t0, "duration_secs": d} for f, d in function_calls],
    }


class Log:
    def __init__(self, **meta):
        self.meta = {**META, **meta}
        self.lines: list[dict] = []
        self.seq: dict[str, int] = {}
        self.expected: dict[str, list[str]] = {}

    def add(self, session, turn, status, event_type, native_type, payload, *, expect=True, **override):
        self.seq[session] = self.seq.get(session, 0) + 1
        line = {**self.meta, "session_id": session, "turn_id": turn, "turn_status": status,
                "seq": self.seq[session], "event_type": event_type, "native_type": native_type,
                "observed_at": 0.0, "payload": payload, **override}
        line["event_id"] = hashlib.sha256(json.dumps(line, sort_keys=True).encode()).hexdigest()[:16]
        self.lines.append(line)
        if turn is not None and expect:
            self.expected.setdefault(session, [])
            if turn not in self.expected[session]:
                self.expected[session].append(turn)
        return line

    def turn(self, session, turn, total, status="responded", **kw):
        return self.add(session, turn, status, "latency_breakdown", "LatencyBreakdown", breakdown(total, **kw))

    def metric(self, session, turn, status, native_type, **payload):
        payload.setdefault("model", None)
        return self.add(session, turn, status, "metrics", native_type, payload)

    def expect(self, session, turn):
        self.expected.setdefault(session, []).append(turn)

    def write(self, path: Path, *, manifest=True, extra_lines=()) -> Path:
        path.write_text("".join(json.dumps(l) + "\n" for l in self.lines) + "".join(x + "\n" for x in extra_lines))
        if manifest:
            m = {"schema": "voice-check.manifest", "schema_version": 1,
                 "run_id": self.meta["run_id"], "config_id": self.meta["config_id"],
                 "scenario_id": self.meta["scenario_id"], "provenance": self.meta["provenance"],
                 "expected_turns": [{"session_id": s, "turns": t} for s, t in self.expected.items()]}
            path.with_name(path.name.removesuffix(".jsonl") + ".manifest.json").write_text(json.dumps(m))
        return path


def uniform(path: Path, totals: list[float], **meta) -> Path:
    """One session per 5 turns, one measured breakdown per turn."""
    log = Log(**meta)
    for i, total in enumerate(totals):
        log.turn(f"s{i // 5:04d}", f"u{i % 5 + 1}", total)
    return log.write(path)
