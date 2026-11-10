"""Make the caller utterances the local capture replays (synthetic speech).

The caller's voice is Kokoro itself (voice ``am_michael``), so the input is
reproducible on any machine. It is synthetic speech, not a recording of a
person. Output: 16 kHz mono 16-bit WAV files plus ``capture/audio/provenance.json``.

    uv run --extra capture python capture/make_input_audio.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import wave
from importlib.metadata import version
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "capture" / "audio"
VOICE = "am_michael"
RATE = 16000

UTTERANCES = {
    "u1": "Hi, I'm calling about the quote you sent me last week.",
    "u2": "Can you tell me if the price includes installation?",
    "u3": "And how soon could someone come out to the house?",
    "u4": "Great, thanks, that is all I needed.",
}


def main() -> None:
    from kokoro_onnx import Kokoro
    from pipecat.services.kokoro.tts import KOKORO_CACHE_DIR, KOKORO_MODEL_URL, KOKORO_VOICES_URL, _ensure_model_files

    model, voices = KOKORO_CACHE_DIR / "kokoro-v1.0.onnx", KOKORO_CACHE_DIR / "voices-v1.0.bin"
    _ensure_model_files(model, voices)
    k = Kokoro(str(model), str(voices))
    OUT.mkdir(parents=True, exist_ok=True)
    files = {}
    for tid, text in UTTERANCES.items():
        samples, sr = k.create(text, voice=VOICE, speed=1.0, lang="en-us")
        x = np.asarray(samples, dtype=np.float32)
        t_src = np.arange(len(x)) / sr
        t_dst = np.arange(int(len(x) * RATE / sr)) / RATE
        y = np.interp(t_dst, t_src, x)
        pcm = (np.clip(y, -1, 1) * 32767).astype("<i2").tobytes()
        path = OUT / f"{tid}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm)
        files[tid] = {
            "file": path.name,
            "text": text,
            "seconds": round(len(y) / RATE, 3),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    prov = {
        "what": "Synthetic caller speech generated with Kokoro; not a recording of a person.",
        "generator": {
            "kokoro_onnx": version("kokoro-onnx"),
            "model_url": KOKORO_MODEL_URL,
            "voices_url": KOKORO_VOICES_URL,
            "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
            "voices_sha256": hashlib.sha256(voices.read_bytes()).hexdigest(),
            "voice": VOICE,
            "resampled_to_hz": RATE,
        },
        "utterances": files,
    }
    (OUT / "provenance.json").write_text(json.dumps(prov, indent=2) + "\n")
    print(json.dumps(files, indent=2))


if __name__ == "__main__":
    sys.exit(main())
