"""Deterministic synthetic runs, produced by Pipecat's own latency observer.

Nothing here talks to a model. A scripted clock and scripted frames stand in
for an STT, an LLM and a TTS (the "simulated backend"); the frames go through
Pipecat 1.15.0's real ``UserBotLatencyObserver``, whose ``LatencyBreakdown`` is
then written by the same :class:`EnvelopeWriter` the local capture uses.

A delay is injected into the scenario's events, not into a reported number:
the LLM's first chunk arrives later, and every event after it (first text,
first audio, bot speaking) moves by the same amount.

Scenario: 200 sessions of 5 user turns, plus one greeting per session. Turn
``k`` of session ``s`` (both from 0) is given a total of
``380 + 10 * ((s + k) % 5)`` ms, so each of 380, 390, 400, 410 and 420 ms is
used by exactly 200 turns. The candidate adds the delay to every turn of the
sessions where ``s % 10 == 3`` (20 sessions, 100 turns, 20 at each value).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ClientConnectedFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    MetricsFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import ProcessingMetricsData, TTFAMetricsData, TTFBMetricsData
from pipecat.observers.base_observer import FramePushed
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.processors.frame_processor import FrameDirection

from capture.pipecat_jsonl_observer import EnvelopeWriter, PipecatJsonlObserver

SESSIONS = 200
TURNS = 5
SCENARIO_ID = "synthetic-200x5-v1"
STT, LLM, TTS, OUT = "ScriptedSTT#0", "ScriptedLLM#0", "ScriptedTTS#0", "ScriptedOutputTransport#0"

# Fixed parts of every user turn, in seconds. The output transport takes the rest.
STOP_SECS = 0.080  # endpointing wait (VAD stop_secs)
STT_SECS = 0.040
TURN_DETECTION_SECS = 0.010
LLM_TTFB_SECS = 0.120
TTS_TTFB_SECS = 0.100
LEADING_SILENCE_SECS = 0.012


def turn_total_secs(s: int, k: int) -> float:
    return (380 + 10 * ((s + k) % 5)) / 1000


def delayed_sessions(sessions: int = SESSIONS) -> set[int]:
    return {s for s in range(sessions) if s % 10 == 3}


class _Source:
    def __init__(self, name: str):
        self.name = name


class _Clock:
    def __init__(self):
        self.t = 0.0

    def now(self) -> float:
        return self.t


def session_id(s: int) -> str:
    return f"s{s + 1:03d}"


def turn_id(k: int) -> str:
    return f"u{k + 1}"


async def _drive(writer: EnvelopeWriter, *, delay_s: float, slow: set[int], sessions: int):
    clock = _Clock()
    latency = UserBotLatencyObserver(time_source=clock.now)
    jsonl = PipecatJsonlObserver(writer)
    jsonl.attach(latency)
    src = {n: _Source(n) for n in (STT, LLM, TTS, OUT, "ScriptedInputTransport#0", "Worker")}

    async def push(at: float, frame, source: str):
        clock.t = at
        data = FramePushed(
            source=src[source],
            destination=src[OUT],
            frame=frame,
            direction=FrameDirection.DOWNSTREAM,
            timestamp=0,
        )
        await latency.on_push_frame(data)
        await jsonl.on_push_frame(data)
        await asyncio.sleep(0)

    for s in range(sessions):
        writer.begin_session(session_id(s))
        # A fresh observer per session, as each session is its own pipeline.
        latency = UserBotLatencyObserver(time_source=clock.now)
        jsonl.attach(latency)
        start = 1_000_000.0 + s * 1000.0
        clock.t = start
        # What PipelineWorker sends at start when send_initial_empty_metrics is
        # on (pipecat/pipeline/worker.py lines 1276-1282 and 1540).
        await push(
            start,
            MetricsFrame(
                data=[
                    m
                    for p in (STT, LLM, TTS)
                    for m in (TTFBMetricsData(processor=p, value=0.0), ProcessingMetricsData(processor=p, value=0.0))
                ]
            ),
            "Worker",
        )
        await latency.on_pipeline_started()
        # Greeting, timed by Pipecat from the client connecting.
        await push(start, ClientConnectedFrame(), "ScriptedInputTransport#0")
        await push(start + 0.050, LLMFullResponseStartFrame(), LLM)
        await push(start + 0.350, MetricsFrame(data=[TTFBMetricsData(processor=LLM, model="scripted-llm", value=0.300)]), LLM)
        await push(start + 0.350, LLMTextFrame(text="Hi, thanks for calling."), LLM)
        await push(start + 0.450, MetricsFrame(data=[TTFBMetricsData(processor=TTS, model="scripted-tts", value=0.100)]), TTS)
        await push(start + 0.450, TTSAudioRawFrame(audio=b"\x00\x00" * 160, sample_rate=16000, num_channels=1), TTS)
        await push(start + 0.620, BotStartedSpeakingFrame(), OUT)
        await push(start + 2.000, BotStoppedSpeakingFrame(), OUT)
        await asyncio.sleep(0)

        for k in range(TURNS):
            writer.begin_turn(turn_id(k))
            d = delay_s if s in slow else 0.0
            total = turn_total_secs(s, k)
            b = start + 10.0 * (k + 1)  # the moment the user actually fell silent
            await push(b - 1.2, VADUserStartedSpeakingFrame(), "ScriptedInputTransport#0")
            t_vad = b + STOP_SECS
            await push(t_vad, VADUserStoppedSpeakingFrame(stop_secs=STOP_SECS, timestamp=t_vad), "ScriptedInputTransport#0")
            t_tr = t_vad + STT_SECS
            await push(t_tr, MetricsFrame(data=[TTFBMetricsData(processor=STT, model="scripted-stt", value=STT_SECS)]), STT)
            await push(t_tr, TranscriptionFrame(text="scripted utterance", user_id="caller", timestamp=""), STT)
            await push(t_tr + 0.002, UserStoppedSpeakingFrame(), "ScriptedInputTransport#0")
            t_req = t_tr + TURN_DETECTION_SECS
            await push(t_req, LLMFullResponseStartFrame(), LLM)
            t_chunk = t_req + LLM_TTFB_SECS + d  # injected delay: the first chunk is late
            await push(t_chunk, MetricsFrame(data=[TTFBMetricsData(processor=LLM, model="scripted-llm", value=LLM_TTFB_SECS + d)]), LLM)
            await push(t_chunk, LLMTextFrame(text="Sure."), LLM)
            t_audio = t_chunk + TTS_TTFB_SECS
            await push(t_audio, MetricsFrame(data=[TTFBMetricsData(processor=TTS, model="scripted-tts", value=TTS_TTFB_SECS)]), TTS)
            await push(
                t_audio,
                MetricsFrame(
                    data=[
                        TTFAMetricsData(
                            processor=TTS,
                            model="scripted-tts",
                            ttfa=TTS_TTFB_SECS + LEADING_SILENCE_SECS,
                            ttfb=TTS_TTFB_SECS,
                            leading_silence=LEADING_SILENCE_SECS,
                        )
                    ]
                ),
                TTS,
            )
            await push(t_audio, TTSAudioRawFrame(audio=b"\x00\x00" * 160, sample_rate=16000, num_channels=1), TTS)
            await push(b + total + d, BotStartedSpeakingFrame(), OUT)
            await push(b + total + d + 1.5, BotStoppedSpeakingFrame(), OUT)
            for _ in range(3):
                await asyncio.sleep(0)
        writer.end_turn()


def generate(
    jsonl_path: str | Path,
    *,
    run_id: str,
    config_id: str,
    delay_s: float = 0.0,
    sessions: int = SESSIONS,
) -> Path:
    """Write ``<name>.jsonl`` and its ``<name>.manifest.json``; return the manifest path."""
    jsonl_path = Path(jsonl_path)
    slow = delayed_sessions(sessions) if delay_s else set()
    writer = EnvelopeWriter(
        jsonl_path,
        run_id=run_id,
        config_id=config_id,
        scenario_id=SCENARIO_ID,
        provenance="synthetic",
        inference_backend="simulated",
        clock=lambda: 0.0,
    )
    asyncio.run(_drive(writer, delay_s=delay_s, slow=slow, sessions=sessions))
    writer.close()
    manifest = {
        "schema": "voice-check.manifest",
        "schema_version": 1,
        "run_id": run_id,
        "config_id": config_id,
        "scenario_id": SCENARIO_ID,
        "provenance": "synthetic",
        "inference_backend": "simulated",
        "framework": "pipecat-ai",
        "framework_version": writer._common["framework_version"],
        "conditions": {
            "backend": "simulated: scripted clock and scripted frames, no model runs",
            "transport": "none (frames pushed straight into the observer)",
            "machine": "not applicable",
            "concurrency": "not applicable",
        },
        "config": {
            "stt": "ScriptedSTT (simulated)",
            "llm": "ScriptedLLM (simulated)",
            "tts": "ScriptedTTS (simulated)",
            "vad_stop_secs": STOP_SECS,
            "llm_first_chunk_delay_secs": delay_s,
        },
        "expected_turns": [
            {"session_id": session_id(s), "turns": [turn_id(k) for k in range(TURNS)]}
            for s in range(sessions)
        ],
        "injected_change": (
            {
                "description": (
                    f"The LLM's first chunk arrives {delay_s * 1000:.0f} ms later on every turn of "
                    f"{len(slow)} sessions; first text, first audio and bot speaking move with it."
                ),
                "contribution_key": "llm_inference",
                "sessions": [session_id(s) for s in sorted(slow)],
                "delay_secs": delay_s,
            }
            if slow
            else None
        ),
    }
    manifest_path = jsonl_path.with_name(jsonl_path.name.removesuffix(".jsonl") + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest_path
