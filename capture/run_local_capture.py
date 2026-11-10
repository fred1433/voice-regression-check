"""Local capture: a real Pipecat 1.15.0 pipeline on this machine, no cloud service.

    caller WAV files (synthetic speech, see capture/audio/provenance.json)
      -> ReplayInput (20 ms frames, paced at real time)
      -> WhisperSTTService (faster-whisper, CPU, int8) with Silero VAD in the user aggregator
      -> OpenAILLMService pointed at a local Ollama server (http://127.0.0.1:11434/v1)
      -> KokoroTTSService (kokoro-onnx)
      -> NullOutput (no network, no speaker: writes are paced at real time and dropped)

Each session is a fresh pipeline that plays the four utterances in order and
waits for the bot to finish speaking before the next one. Every line goes
through capture/pipecat_jsonl_observer.py. Nothing is trimmed: the first
turn of each session includes whatever warm-up it pays.

    ollama serve &            # in another shell
    ollama pull qwen2.5:3b
    uv run --extra capture python capture/run_local_capture.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import subprocess
import time
import urllib.request
import wave
from importlib.metadata import version
from pathlib import Path

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    StartFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from capture.pipecat_jsonl_observer import EnvelopeWriter, PipecatJsonlObserver  # noqa: E402

RATE_IN = 16000
FRAME_SECS = 0.02
AUDIO = ROOT / "capture" / "audio"
TURNS = ["u1", "u2", "u3", "u4"]
SYSTEM = (
    "You are a phone assistant for a home services company. Answer in one short "
    "spoken sentence, with no lists and no markdown."
)


def read_wav(path: Path) -> bytes:
    with wave.open(str(path)) as w:
        assert w.getframerate() == RATE_IN and w.getnchannels() == 1 and w.getsampwidth() == 2
        return w.readframes(w.getnframes())


class ReplayInput(BaseInputTransport):
    """Pushes 20 ms frames at real time: the queued utterance, then silence."""

    def __init__(self, params: TransportParams):
        super().__init__(params)
        self._pending = b""
        self._task = None
        self.ready = asyncio.Event()

    def play(self, pcm: bytes):
        self._pending += pcm

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self.set_transport_ready(frame)
        self._task = self.create_task(self._feed())
        self.ready.set()

    async def stop(self, frame: EndFrame):
        if self._task:
            await self.cancel_task(self._task)
            self._task = None
        await super().stop(frame)

    async def cancel(self, frame):
        if self._task:
            await self.cancel_task(self._task)
            self._task = None
        await super().cancel(frame)

    async def _feed(self):
        n = int(RATE_IN * FRAME_SECS) * 2
        t_next = time.monotonic()
        while True:
            chunk, self._pending = self._pending[:n], self._pending[n:]
            if len(chunk) < n:
                chunk += b"\x00" * (n - len(chunk))
            await self.push_audio_frame(InputAudioRawFrame(audio=chunk, sample_rate=RATE_IN, num_channels=1))
            t_next += FRAME_SECS
            await asyncio.sleep(max(0.0, t_next - time.monotonic()))


class NullOutput(BaseOutputTransport):
    """Accepts audio at real-time pace and drops it (no device, no network)."""

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self.set_transport_ready(frame)

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        await asyncio.sleep(len(frame.audio) / (2 * frame.sample_rate * frame.num_channels))
        return True


class ReplayTransport(BaseTransport):
    def __init__(self, params: TransportParams):
        super().__init__()
        self._in = ReplayInput(params)
        self._out = NullOutput(params)

    def input(self):
        return self._in

    def output(self):
        return self._out


class BotState(BaseObserver):
    def __init__(self):
        super().__init__(observe_every_push=False)
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()

    async def on_push_frame(self, data: FramePushed):
        if isinstance(data.frame, BotStartedSpeakingFrame):
            self.started.set()
        elif isinstance(data.frame, BotStoppedSpeakingFrame):
            self.stopped.set()


async def run_session(writer: EnvelopeWriter, session_id: str, args, audio: dict[str, bytes], log: list):
    writer.begin_session(session_id)
    params = TransportParams(audio_in_enabled=True, audio_out_enabled=True, audio_in_sample_rate=RATE_IN)
    transport = ReplayTransport(params)
    stt = WhisperSTTService(settings=WhisperSTTService.Settings(model=args.whisper_model), device="cpu", compute_type="int8")
    llm = OpenAILLMService(
        api_key="ollama",
        base_url=args.ollama_url,
        settings=OpenAILLMService.Settings(model=args.llm_model, temperature=0.0, max_tokens=60),
    )
    tts = KokoroTTSService(settings=KokoroTTSService.Settings(voice="af_heart"))
    context = LLMContext(messages=[{"role": "system", "content": SYSTEM}])
    agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=args.vad_stop_secs))),
    )
    pipeline = Pipeline([transport.input(), stt, agg.user(), llm, tts, transport.output(), agg.assistant()])

    latency = UserBotLatencyObserver()
    jsonl = PipecatJsonlObserver(writer)
    jsonl.attach(latency)
    bot = BotState()
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True, audio_in_sample_rate=RATE_IN),
        observers=[latency, jsonl, bot],
        idle_timeout_secs=None,
    )
    t_start = time.monotonic()
    runner = asyncio.create_task(PipelineRunner(handle_sigint=False).run(worker))
    await asyncio.wait_for(transport._in.ready.wait(), timeout=120)
    log.append({"session_id": session_id, "event": "pipeline_ready", "secs_after_start": round(time.monotonic() - t_start, 3)})
    await asyncio.sleep(1.0)  # let the VAD settle on silence

    for tid in TURNS:
        writer.begin_turn(tid)
        bot.started.clear()
        bot.stopped.clear()
        t0 = time.monotonic()
        transport._in.play(audio[tid])
        outcome = "bot_finished"
        try:
            await asyncio.wait_for(bot.started.wait(), timeout=args.turn_timeout)
            await asyncio.wait_for(bot.stopped.wait(), timeout=args.turn_timeout)
        except asyncio.TimeoutError:
            outcome = "timeout"
        log.append({"session_id": session_id, "turn_id": tid, "event": outcome, "wall_secs": round(time.monotonic() - t0, 3)})
        await asyncio.sleep(1.0)
    writer.end_turn()
    # What was actually said: Whisper's transcripts and the model's replies.
    log.append({"session_id": session_id, "event": "conversation",
                "messages": [m for m in context.get_messages() if m.get("role") in ("user", "assistant")]})
    await worker.queue_frame(EndFrame())
    await asyncio.wait_for(runner, timeout=60)


def machine_facts() -> dict:
    def sh(*cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return None

    return {
        "os": f"{platform.system()} {platform.release()}",
        "macos": sh("sw_vers", "-productVersion"),
        "arch": platform.machine(),
        "cpu": sh("sysctl", "-n", "machdep.cpu.brand_string"),
        "memory_gb": round(int(sh("sysctl", "-n", "hw.memsize") or 0) / 2**30),
        "python": platform.python_version(),
        "nvidia_gpu": False,
    }


def whisper_snapshot(name: str) -> str | None:
    snaps = Path.home() / ".cache" / "huggingface" / "hub" / f"models--Systran--faster-whisper-{name}" / "snapshots"
    found = sorted(p.name for p in snaps.glob("*")) if snaps.exists() else []
    return f"Systran/faster-whisper-{name}@{found[0]}" if len(found) == 1 else None


def ollama_facts(url: str, model: str) -> dict:
    base = url.removesuffix("/v1")
    with urllib.request.urlopen(f"{base}/api/version", timeout=5) as r:
        ver = json.load(r)["version"]
    req = urllib.request.Request(f"{base}/api/show", data=json.dumps({"model": model}).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        show = json.load(r)
    with urllib.request.urlopen(f"{base}/api/tags", timeout=5) as r:
        tags = {m["name"]: m for m in json.load(r)["models"]}
    with urllib.request.urlopen(f"{base}/api/ps", timeout=5) as r:
        ps = json.load(r)
    tag = tags.get(model, {})
    return {
        "ollama_version": ver,
        "model": model,
        "digest": tag.get("digest"),
        "size_bytes": tag.get("size"),
        "details": show.get("details"),
        "loaded_models": [{"name": m.get("name"), "size_vram": m.get("size_vram"), "size": m.get("size")} for m in ps.get("models", [])],
    }


async def main_async(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    audio = {t: read_wav(AUDIO / f"{t}.wav") for t in TURNS}
    run_id = f"local-capture-{time.strftime('%Y%m%dT%H%M%S')}"
    config_id = f"whisper-{args.whisper_model}-cpu+ollama-{args.llm_model}+kokoro"
    writer = EnvelopeWriter(
        out / "local-capture.jsonl",
        run_id=run_id,
        config_id=config_id,
        scenario_id="local-4-utterances-v1",
        provenance="local-capture",
        inference_backend="ollama",
    )
    log: list = []
    sessions = [f"c{i + 1:02d}" for i in range(args.sessions)]
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    for sid in sessions:
        await run_session(writer, sid, args, audio, log)
    writer.close()
    ollama = ollama_facts(args.ollama_url, args.llm_model)
    manifest = {
        "schema": "voice-check.manifest",
        "schema_version": 1,
        "run_id": run_id,
        "config_id": config_id,
        "scenario_id": "local-4-utterances-v1",
        "provenance": "local-capture",
        "inference_backend": "ollama",
        "framework": "pipecat-ai",
        "framework_version": version("pipecat-ai"),
        "started_at": started,
        "conditions": {
            "machine": machine_facts(),
            "transport": "none: ReplayInput pushes WAV frames at real time, NullOutput drops audio at real time",
            "concurrency": "one session at a time",
            "startup": "a fresh pipeline per session; Ollama already running with the model pulled; the first turn of each session is kept",
            "where_things_ran": {
                "stt": "faster-whisper on CPU (device=cpu, compute_type=int8)",
                "llm": "Ollama on this Mac (Ollama uses Apple Metal when available; see loaded_models.size_vram)",
                "tts": "kokoro-onnx via onnxruntime (CPU execution provider)",
                "vad": "Silero VAD (onnxruntime, CPU)",
            },
        },
        "config": {
            "stt": f"WhisperSTTService, faster-whisper {version('faster-whisper')}, model {args.whisper_model}",
            "llm": f"OpenAILLMService -> {args.ollama_url}, model {args.llm_model}, temperature 0, max_tokens 60",
            "tts": f"KokoroTTSService, kokoro-onnx {version('kokoro-onnx')}, voice af_heart",
            "vad_stop_secs": args.vad_stop_secs,
            "turn_stop_strategy": "Pipecat default: TurnAnalyzerUserTurnStopStrategy(LocalSmartTurnAnalyzerV3), local ONNX model",
            "whisper_model_snapshot": whisper_snapshot(args.whisper_model),
            "onnxruntime": version("onnxruntime"),
            "ollama": ollama,
        },
        "expected_turns": [{"session_id": s, "turns": TURNS} for s in sessions],
        "injected_change": None,
        "driver_log": log,
    }
    (out / "local-capture.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(log, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "capture" / "out"))
    ap.add_argument("--sessions", type=int, default=3)
    ap.add_argument("--whisper-model", default="base")
    ap.add_argument("--llm-model", default="qwen2.5:3b")
    ap.add_argument("--ollama-url", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--vad-stop-secs", type=float, default=0.2)
    ap.add_argument("--turn-timeout", type=float, default=45.0)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
