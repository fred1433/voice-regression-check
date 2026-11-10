"""Read one run: an envelope JSONL file and its manifest.

The parser never turns a missing value into 0, never adds a TTFB to anything,
and never guesses an inference backend from a processor or model name.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import envelope as E


@dataclass
class Turn:
    session_id: str
    turn_id: str
    status: str
    breakdown: dict | None = None  # the user_silence breakdown, if any
    metrics: list[dict] = field(default_factory=list)  # envelope lines
    errors: list[dict] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return (self.session_id, self.turn_id)

    @property
    def total_secs(self) -> float | None:
        return self.breakdown["total_secs"] if self.breakdown else None


@dataclass
class Run:
    path: str
    manifest_path: str
    manifest: dict | None = None
    meta: dict = field(default_factory=dict)
    turns: dict[tuple[str, str], Turn] = field(default_factory=dict)
    greetings: list[dict] = field(default_factory=list)  # (session_id, breakdown)
    unanchored_breakdowns: int = 0  # breakdowns Pipecat emitted with no measurement
    startup_metrics: list[dict] = field(default_factory=list)
    session_errors: list[dict] = field(default_factory=list)
    lines_read: int = 0
    duplicates_ignored: int = 0
    input_errors: list[str] = field(default_factory=list)
    zeros: dict[str, int] = field(
        default_factory=lambda: {"initialization": 0, "zero_value": 0, "absent": 0, "not_applicable": 0}
    )

    expected_keys: list[tuple[str, str]] = field(default_factory=list)  # unique, in manifest order

    @property
    def expected(self) -> list[tuple[str, str]]:
        return list(self.expected_keys) if self.manifest else []

    @property
    def expected_sessions(self) -> list[str]:
        return list(dict.fromkeys(k[0] for k in self.expected))


def default_manifest_path(jsonl_path: str | Path) -> Path:
    p = Path(jsonl_path)
    return p.with_name(p.name.removesuffix(".jsonl") + ".manifest.json")


def _check_keys(where: str, obj: Any, expected: set[str], errors: list[str]) -> bool:
    if not isinstance(obj, dict):
        errors.append(f"{where}: expected an object")
        return False
    missing, extra = expected - obj.keys(), obj.keys() - expected
    if missing or extra:
        parts = []
        if missing:
            parts.append("missing " + ", ".join(sorted(missing)))
        if extra:
            parts.append("unknown " + ", ".join(sorted(extra)))
        errors.append(f"{where}: unknown structure ({'; '.join(parts)})")
        return False
    return True


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_number(where: str, v: Any, errors: list[str], *, duration: bool, nullable: bool = False) -> bool:
    """A duration must be a finite number >= 0; a timestamp a finite number."""
    if v is None and nullable:
        return True
    if not _is_num(v) or not math.isfinite(v):
        errors.append(f"{where} is not a finite number ({v!r})")
        return False
    if duration and v < 0:
        errors.append(f"{where} is negative ({v!r})")
        return False
    return True


def _check_spans(where: str, items: list[dict], errors: list[str]) -> bool:
    ok = True
    for i, item in enumerate(items):
        ok &= _check_number(f"{where}[{i}].start_time", item["start_time"], errors, duration=False)
        ok &= _check_number(f"{where}[{i}].duration_secs", item["duration_secs"], errors, duration=True)
    return ok


def _check_breakdown(where: str, p: dict, errors: list[str]) -> bool:
    ok = True
    if p.get("measured_from") not in E.MEASURED_FROM:
        errors.append(f"{where}: unknown measured_from {p.get('measured_from')!r}")
        ok = False
    ok &= _check_number(f"{where}: total_secs", p.get("total_secs"), errors, duration=True)
    ok &= _check_number(f"{where}: user_turn_secs", p.get("user_turn_secs"), errors, duration=True, nullable=True)
    ok &= _check_number(f"{where}: user_turn_start_time", p.get("user_turn_start_time"), errors, duration=False, nullable=True)
    for name, keys in (("contributions", E.CONTRIBUTION_KEYS), ("ttfb", E.TTFB_ITEM_KEYS), ("function_calls", E.FUNCTION_CALL_KEYS)):
        items = p.get(name)
        if not isinstance(items, list):
            errors.append(f"{where}: {name} is not a list")
            ok = False
            continue
        shaped = True
        for i, item in enumerate(items):
            shaped &= _check_keys(f"{where}: {name}[{i}]", item, keys, errors)
        ok &= shaped and _check_spans(f"{where}: {name}", items, errors)
    if p.get("text_aggregation") is not None:
        if _check_keys(f"{where}: text_aggregation", p["text_aggregation"], E.TEXT_AGGREGATION_KEYS, errors):
            ok &= _check_spans(f"{where}: text_aggregation", [p["text_aggregation"]], errors)
        else:
            ok = False
    return ok


def _check_metric(where: str, native_type: str, p: dict, errors: list[str]) -> bool:
    ok = True
    for k in E.METRIC_DURATIONS.get(native_type, ()):
        ok &= _check_number(f"{where}: {k}", p.get(k), errors, duration=True)
    return ok


def _read_manifest(run: Run, manifest_path: Path, errors: list[str]) -> None:
    if not manifest_path.exists():
        errors.append(f"manifest not found: {manifest_path} (expected turns unknown)")
        return
    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"manifest cannot be read: {exc}")
        return
    except json.JSONDecodeError as exc:
        errors.append(f"manifest is not JSON: {exc}")
        return
    if not isinstance(m, dict):
        errors.append(f"manifest: expected an object, found {type(m).__name__}")
        return
    if m.get("schema") != E.MANIFEST_SCHEMA or m.get("schema_version") != 1:
        errors.append("manifest: unknown schema or schema_version")
        return
    entries = m.get("expected_turns")
    if not isinstance(entries, list) or not entries:
        errors.append("manifest: expected_turns is missing or empty")
        return
    keys: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for i, entry in enumerate(entries):
        where = f"manifest: expected_turns[{i}]"
        if not isinstance(entry, dict) or set(entry) != {"session_id", "turns"}:
            errors.append(f"{where}: expected an object with session_id and turns")
            return
        sid, turns = entry["session_id"], entry["turns"]
        if not isinstance(sid, str) or not sid:
            errors.append(f"{where}: session_id is not a non-empty string")
            return
        if not isinstance(turns, list) or not all(isinstance(t, str) and t for t in turns):
            errors.append(f"{where}: turns is not a list of turn ids")
            return
        for t in turns:
            if (sid, t) in seen:
                errors.append(f"manifest: turn {sid}/{t} is listed twice in expected_turns (counted once)")
                continue
            seen.add((sid, t))
            keys.append((sid, t))
    run.manifest = m
    run.expected_keys = keys


def load_run(jsonl_path: str | Path, manifest_path: str | Path | None = None) -> Run:
    jsonl_path = Path(jsonl_path)
    manifest_path = Path(manifest_path) if manifest_path else default_manifest_path(jsonl_path)
    run = Run(path=str(jsonl_path), manifest_path=str(manifest_path))
    errors = run.input_errors

    # -- manifest -----------------------------------------------------------------
    _read_manifest(run, manifest_path, errors)

    if not jsonl_path.exists():
        errors.append(f"log not found: {jsonl_path}")
        return run

    # -- lines ----------------------------------------------------------------------
    seen_ids: dict[str, str] = {}  # event_id -> the line's canonical content
    by_seq: dict[tuple[str, int], str] = {}
    turn_lines: dict[tuple[str, str], list[dict]] = {}
    run_keys = ("run_id", "config_id", "scenario_id", "provenance", "inference_backend", "framework", "framework_version")

    try:
        _read_lines(run, jsonl_path, seen_ids, by_seq, turn_lines, run_keys)
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"log cannot be read: {exc}")
        return run

    if run.manifest:
        for k in ("run_id", "config_id", "scenario_id", "provenance"):
            if k in run.meta and run.manifest.get(k) != run.meta[k]:
                errors.append(f"manifest {k} {run.manifest.get(k)!r} does not match the log ({run.meta[k]!r})")

    expected = set(run.expected)
    for key, lines in turn_lines.items():
        turn = _build_turn(key, lines, errors, run)
        if turn is None:
            continue
        if run.manifest and key not in expected:
            errors.append(f"turn {key[0]}/{key[1]} is not in the manifest")
            continue
        run.turns[key] = turn
    return run


def _read_lines(run: Run, jsonl_path: Path, seen_ids, by_seq, turn_lines, run_keys) -> None:
    errors = run.input_errors
    with jsonl_path.open(encoding="utf-8") as fh:
        for n, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            run.lines_read += 1
            where = f"line {n}"
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                errors.append(f"{where}: not JSON")
                continue
            if not isinstance(line, dict) or line.get("schema") != E.SCHEMA:
                errors.append(f"{where}: no voice-check envelope (a bare Pipecat dump is not accepted)")
                continue
            if line.get("schema_version") != E.SCHEMA_VERSION:
                errors.append(f"{where}: unknown envelope schema_version {line.get('schema_version')!r}")
                continue
            keys = set(line)
            missing = E.ENVELOPE_REQUIRED - keys
            extra = keys - E.ENVELOPE_REQUIRED - E.ENVELOPE_OPTIONAL
            if missing or extra:
                errors.append(f"{where}: unknown envelope structure (missing {sorted(missing)}, unknown {sorted(extra)})")
                continue
            fw, ver = line["framework"], line["framework_version"]
            if ver not in E.SUPPORTED_FRAMEWORKS.get(fw, set()):
                errors.append(f"{where}: unsupported framework version {fw} {ver}")
                continue
            if line["provenance"] not in E.PROVENANCES:
                errors.append(f"{where}: unknown provenance {line['provenance']!r}")
                continue
            et, nt = line["event_type"], line["native_type"]
            if et not in E.NATIVE_TYPES:
                errors.append(f"{where}: unknown event_type {et!r}")
                continue
            if nt not in E.NATIVE_TYPES[et]:
                errors.append(f"{where}: unknown native_type {nt!r} for {et}")
                continue
            if not _check_keys(f"{where}: {nt}", line["payload"], E.NATIVE_TYPES[et][nt], errors):
                continue
            if et == "latency_breakdown" and not _check_breakdown(f"{where}: LatencyBreakdown", line["payload"], errors):
                continue
            if et == "metrics" and not _check_metric(f"{where}: {nt}", nt, line["payload"], errors):
                continue
            ts = line["turn_status"]
            if (line["turn_id"] is None) != (ts is None) or (ts is not None and ts not in E.TURN_STATUSES):
                errors.append(f"{where}: invalid turn_status {ts!r} for turn {line['turn_id']!r}")
                continue

            # -- duplicates: a session imported twice is read once ---------------
            eid = line["event_id"]
            content = json.dumps(line, sort_keys=True)
            if eid in seen_ids:
                if seen_ids[eid] == content:
                    run.duplicates_ignored += 1
                else:
                    errors.append(f"{where}: event_id {eid} appears twice with different content")
                continue
            seen_ids[eid] = content
            seq_key = (line["session_id"], line["seq"])
            if seq_key in by_seq:
                errors.append(f"{where}: session {seq_key[0]} has two different events at seq {seq_key[1]}")
                continue
            by_seq[seq_key] = eid

            # -- one run per file -------------------------------------------------------
            for k in run_keys:
                if k not in run.meta:
                    run.meta[k] = line[k]
                elif run.meta[k] != line[k]:
                    errors.append(f"{where}: {k} {line[k]!r} differs from {run.meta[k]!r} earlier in the file")
                    break
            else:
                if line["turn_id"] is None:
                    _session_event(run, line)
                else:
                    turn_lines.setdefault((line["session_id"], line["turn_id"]), []).append(line)


def _session_event(run: Run, line: dict) -> None:
    et, p = line["event_type"], line["payload"]
    if et == "latency_breakdown":
        if p["measured_from"] == "client_connected":
            run.greetings.append({"session_id": line["session_id"], "breakdown": p})
        else:
            run.unanchored_breakdowns += 1
    elif et == "metrics":
        run.startup_metrics.append(line)
        if line["native_type"] == "TTFBMetricsData" and p["value"] == 0:
            # PipelineWorker._initial_metrics_frame (worker.py 1276-1282)
            run.zeros["initialization"] += 1
    else:
        run.session_errors.append(line)


def _build_turn(key, lines: list[dict], errors: list[str], run: Run) -> Turn | None:
    statuses = {l["turn_status"] for l in lines}
    if len(statuses) != 1:
        errors.append(f"turn {key[0]}/{key[1]}: lines disagree on turn_status {sorted(statuses)}")
        return None
    status = statuses.pop()
    turn = Turn(session_id=key[0], turn_id=key[1], status=status)
    measured = []
    for l in lines:
        et, p = l["event_type"], l["payload"]
        if et == "latency_breakdown":
            if p["measured_from"] == "user_silence":
                measured.append(p)
            elif p["measured_from"] == "client_connected":
                run.greetings.append({"session_id": key[0], "breakdown": p})
            else:
                # Pipecat sets measured_from to None, and total_secs to 0.0, when
                # it could not anchor the interval: nothing was measured.
                run.unanchored_breakdowns += 1
        elif et == "metrics":
            turn.metrics.append(l)
            if l["native_type"] == "TTFBMetricsData" and p["value"] == 0:
                run.zeros["zero_value"] += 1
        else:
            turn.errors.append(l)
    if len(measured) > 1:
        errors.append(f"turn {key[0]}/{key[1]}: {len(measured)} measured responses for one user turn")
        return None
    turn.breakdown = measured[0] if measured else None
    if (turn.breakdown is not None) != (status in E.RESPONDED):
        errors.append(
            f"turn {key[0]}/{key[1]}: status {status!r} does not match its events "
            f"({'a' if turn.breakdown else 'no'} measured breakdown)"
        )
        return None
    if status == "no_audio" and not any(E.NO_AUDIO_MARKER in (e["payload"].get("error") or "") for e in turn.errors):
        errors.append(f"turn {key[0]}/{key[1]}: status no_audio without a 'completed with no audio' error")
        return None
    return turn
