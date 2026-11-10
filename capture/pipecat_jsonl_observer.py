"""Write Pipecat 1.15.0 latency events as voice-check envelope lines (JSONL).

The envelope is this project's own convention, not a Pipecat format. Each line
wraps one native Pipecat object, left as Pipecat serializes it:

* ``latency_breakdown``: a ``LatencyBreakdown`` (pydantic ``BaseModel``,
  ``pipecat/observers/user_bot_latency_observer.py`` line 395), written with
  ``model_dump(mode="json")``.
* ``metrics``: one ``MetricsData`` item of a ``MetricsFrame`` (pydantic
  ``BaseModel``, ``pipecat/metrics/metrics.py`` line 19), written with
  ``model_dump(mode="json")``. The class name goes in ``native_type`` because
  the dump alone does not say whether it was a TTFB, a TTFA or a TTFAT.
* ``error``: an ``ErrorFrame`` (a dataclass, ``pipecat/frames/frames.py`` line
  1296). Its ``processor`` and ``exception`` fields are live objects, so the
  line keeps ``error``, ``fatal``, ``category`` and the processor's name.

Turn ids come from the scenario driver (``begin_turn``), not from the VAD, so
two configurations replaying the same scenario produce comparable turn ids.
Lines are buffered per turn and written when the turn closes, so every line of
a turn carries the turn's final status.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    InterruptionFrame,
    MetricsFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.observers.user_bot_latency_observer import LatencyBreakdown, UserBotLatencyObserver

SCHEMA = "voice-check.envelope"
SCHEMA_VERSION = 1
FRAMEWORK = "pipecat-ai"


def _framework_version() -> str:
    from importlib.metadata import version

    return version("pipecat-ai")


class EnvelopeWriter:
    """Buffers native Pipecat objects per turn and writes envelope lines."""

    def __init__(
        self,
        path: str | Path,
        *,
        run_id: str,
        config_id: str,
        scenario_id: str,
        provenance: str,
        inference_backend: str = "unknown",
        clock=time.time,
    ):
        if provenance not in ("synthetic", "local-capture"):
            raise ValueError("provenance must be 'synthetic' or 'local-capture'")
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self._path.open("w", encoding="utf-8")
        self._clock = clock
        self._common = {
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "framework": FRAMEWORK,
            "framework_version": _framework_version(),
            "run_id": run_id,
            "config_id": config_id,
            "scenario_id": scenario_id,
            "provenance": provenance,
            "inference_backend": inference_backend,
        }
        self._session_id: str | None = None
        self._seq = 0
        self._turn_id: str | None = None
        self._turn_events: list[dict[str, Any]] = []
        self._interrupted = False

    # -- session and turn boundaries ------------------------------------------------

    def begin_session(self, session_id: str) -> None:
        self.end_turn()
        self._session_id = session_id
        self._seq = 0

    def begin_turn(self, turn_id: str) -> None:
        self.end_turn()
        self._turn_id = turn_id
        self._turn_events = []
        self._interrupted = False

    def end_turn(self) -> None:
        if self._turn_id is None:
            return
        status = self._turn_status(self._turn_events, self._interrupted)
        for event in self._turn_events:
            self._write(event, turn_id=self._turn_id, turn_status=status)
        self._turn_id = None
        self._turn_events = []

    def close(self) -> None:
        self.end_turn()
        self._fh.close()

    # -- native objects --------------------------------------------------------------

    def add_breakdown(self, breakdown: LatencyBreakdown) -> None:
        self._add(
            "latency_breakdown", "LatencyBreakdown", breakdown.model_dump(mode="json")
        )

    def add_metrics_frame(self, frame: MetricsFrame) -> None:
        frame_ref = f"{frame.name}"
        for item in frame.data:
            self._add(
                "metrics", type(item).__name__, item.model_dump(mode="json"), frame_ref=frame_ref
            )

    def add_error_frame(self, frame: ErrorFrame) -> None:
        processor = getattr(frame, "processor", None)
        category = getattr(frame, "category", None)
        payload = {
            "error": frame.error,
            "fatal": bool(frame.fatal),
            "category": str(category.value if hasattr(category, "value") else category)
            if category is not None
            else None,
            "processor": processor.name if processor is not None else None,
        }
        self._add("error", type(frame).__name__, payload)

    def add_interruption(self) -> None:
        # Kept as the turn's status only: an InterruptionFrame says that the
        # user cut in, not when the bot's audio stopped.
        if self._turn_id is not None:
            self._interrupted = True

    # -- internals ---------------------------------------------------------------------

    def _add(self, event_type: str, native_type: str, payload: dict, frame_ref: str | None = None):
        event = {
            "event_type": event_type,
            "native_type": native_type,
            "observed_at": self._clock(),
            "payload": payload,
        }
        if frame_ref:
            event["frame_ref"] = frame_ref
        if self._turn_id is None:
            # Outside a turn: startup metrics, a greeting before the first user
            # turn. Written at once, with no turn and no status.
            self._write(event, turn_id=None, turn_status=None)
        else:
            self._turn_events.append(event)

    @staticmethod
    def _turn_status(events: list[dict[str, Any]], interrupted: bool) -> str:
        responded = any(
            e["event_type"] == "latency_breakdown"
            and e["payload"].get("measured_from") == "user_silence"
            for e in events
        )
        no_audio = any(
            e["event_type"] == "error" and "completed with no audio" in (e["payload"].get("error") or "")
            for e in events
        )
        if responded:
            return "responded_then_interrupted" if interrupted else "responded"
        if no_audio:
            return "no_audio"
        if interrupted:
            return "interrupted_before_response"
        return "no_response"

    def _write(self, event: dict, *, turn_id: str | None, turn_status: str | None) -> None:
        if self._session_id is None:
            raise RuntimeError("begin_session() must be called before events arrive")
        self._seq += 1
        line = {
            **self._common,
            "session_id": self._session_id,
            "turn_id": turn_id,
            "turn_status": turn_status,
            "seq": self._seq,
            **event,
        }
        body = json.dumps(line, sort_keys=True, separators=(",", ":"))
        line["event_id"] = hashlib.sha256(body.encode()).hexdigest()[:16]
        self._fh.write(json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n")
        self._fh.flush()


class PipecatJsonlObserver(BaseObserver):
    """Pipeline observer feeding an :class:`EnvelopeWriter`.

    ``MetricsFrame`` and ``InterruptionFrame`` travel downstream and
    ``ErrorFrame`` travels upstream (``FrameProcessor.push_error``), so both
    directions are watched. Breakdowns come from a ``UserBotLatencyObserver``
    passed to :meth:`attach`.
    """

    def __init__(self, writer: EnvelopeWriter, **kwargs):
        super().__init__(observe_every_push=False, **kwargs)
        self.writer = writer
        self._bot_speaking = False

    def attach(self, latency_observer: UserBotLatencyObserver) -> None:
        @latency_observer.event_handler("on_latency_breakdown")
        async def _on_breakdown(_observer, breakdown: LatencyBreakdown):
            self.writer.add_breakdown(breakdown)

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        if isinstance(frame, MetricsFrame):
            self.writer.add_metrics_frame(frame)
        elif isinstance(frame, ErrorFrame):
            self.writer.add_error_frame(frame)
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
        elif isinstance(frame, InterruptionFrame) and self._bot_speaking:
            # Only an interruption of bot audio counts; Pipecat can also push
            # an InterruptionFrame when nothing is playing.
            self.writer.add_interruption()
