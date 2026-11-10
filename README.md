# Reproducible voice regression check

Compare two configurations of a Pipecat voice agent on the same scenarios, find
the turns that got slower, and refuse to conclude when the logs do not allow it.
`voice-check` reads the latency data Pipecat already produces
(`LatencyBreakdown` and `MetricsFrame`), applies one written policy, and returns
an exit code a CI step can use.

Page: https://theaipipe.com/demos/voice-regression-check/

```
git clone https://github.com/fred1433/voice-regression-check
cd voice-regression-check
uv sync
uv run voice-check compare fixtures/baseline.jsonl fixtures/candidate.jsonl \
  --policy policies/published-example-420.yaml --report report.json
```

```
FAIL: configured latency threshold exceeded
A latency check only; it says nothing about conversation quality.

Policy  policies/published-example-420.yaml: turn total p95 <= 420 ms (illustrative budget)
Baseline  baseline  [synthetic, backend simulated]
          median 400 ms, p95 420 ms (N = 1,000)
          200/200 sessions, 1,000 turns expected, 1,000 measured, 0 failed (no audio 0, no response 0), 0 incomplete, 0 interrupted
Candidate candidate-llm-first-chunk-plus-500ms  [synthetic, backend simulated]
          median 400 ms, p95 900 ms (N = 1,000)
          ...
Affected turns: 100 over the threshold, in 20 of 200 sessions
  every turn of those sessions is affected, and no turn elsewhere
  contribution llm_inference: +50,000 ms summed, 100% of the added time
  associated metric: TTFB of ScriptedLLM#0
  ...
Exit code 1
```

Log analysis runs offline: `compare` reads two JSONL files, their manifests and
a policy. It downloads nothing and starts no service. The terminal output and the
page are both rendered from `report.json` (`uv run voice-check show report.json`).

## Exit codes

| Code | Verdict | Label |
|---|---|---|
| 0 | `PASS` | Latency policy passed |
| 1 | `FAIL` | FAIL: configured latency threshold exceeded |
| 2 | `INCOMPLETE` | input error (unknown version or structure, missing manifest, bad arguments, no policy), runs not comparable, expected turns missing or unmeasured, or too few turns for a p95 |

`PASS` is about latency only. It says nothing about what the agent said.
There is no default threshold: without `--policy`, `compare` exits 2.

## The policy

`policies/published-example-420.yaml` takes the 420 ms streaming budget from
Bearworks' post of 14 Oct 2026, "Sub-500ms Voice Agents: Optimizing the Full
STT-LLM-TTS Pipeline" (Greg M). The post calls it an illustrative budget, not a
measurement. Applying it at p95 is this demo's choice.

* Rule evaluated: turn total, p95 (nearest rank, `ceil(0.95 × N)`), `<= 420 ms`,
  compared at the microsecond, so a p95 of exactly 420 ms passes and 420.001 ms fails.
* `min_measured_turns: 300`: below it, no p95 is printed ("p95 not shown (N = x)")
  and the verdict is `INCOMPLETE`. A floor, not a statistical guarantee.
* `max_incomplete_turns: 0`: an expected turn missing from the log, or interrupted
  before any response, makes the verdict `INCOMPLETE`.
* `published_breakdown`: the post's four lines, copied word for word. Informative,
  never evaluated; a test checks that changing them cannot change a verdict.

## What is measured, and what is not

| Measure | Starts | Ends | Scope |
|---|---|---|---|
| Turn total (`LatencyBreakdown.total_secs`) | user silence as Pipecat estimates it: `VADUserStoppedSpeakingFrame.timestamp - stop_secs` | `BotStartedSpeakingFrame` in the bot's pipeline | SDK-observed |
| Contributions (`LatencyBreakdown.contributions`) | the Pipecat moment opening each span | the moment closing it | SDK-observed |

Not measured: audio travelling to the caller, buffering and playback on the
caller's device (client-side), and barge-in stop latency (Pipecat 1.15.0 has no
reliable metric for it). These are reported as "not measured", never as 0 ms.

How the numbers are built:

* The total comes from the turn's own measurement. Percentiles are never added up,
  and neither TTFBs, tool time nor `user_turn_secs` are added to anything.
* Contributions explain where the time went. Spans with the same key are merged
  as intervals, so overlapping spans are not counted twice.
* Greetings (`measured_from: client_connected`) are kept apart from user turns.
* A turn that ended with "completed with no audio", or with no response at all,
  stays in the denominator as a failed turn and sorts above every measured value.
* An interruption is kept as the turn's status. The boolean is never read as the
  time audio stopped.
* Units: Pipecat reports seconds as floats (checked in 1.15.0). `report.json`
  converts to milliseconds (`"unit": "ms"`); fixtures stay in native seconds.
* Sessions and turns are counted separately; the report keeps scenario, config,
  framework version, provenance, transport and machine for both runs.

## Attribution, with care

For the turns over the threshold, the report separates four things: the measure
that changed (the contribution, or the associated metric, that accounts for the
added time), the turns concerned, hypotheses compatible with the logs, and what
is missing to decide. It does not name a cause. The only cause ever named is the
one declared in a synthetic fixture's manifest, where it is known because it was
injected. All of this text is fixed templates filled by code; no language model
writes any of it.

## The envelope (our convention, not a Pipecat format)

Each JSONL line wraps one native Pipecat object, left as Pipecat serializes it.
`capture/pipecat_jsonl_observer.py` writes it; add it to a pipeline next to a
`UserBotLatencyObserver`:

```python
# sketch: inside your bot, around your existing pipeline
from capture.pipecat_jsonl_observer import EnvelopeWriter, PipecatJsonlObserver
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver

writer = EnvelopeWriter("runs/candidate.jsonl", run_id="2026-11-10-a", config_id="candidate",
                        scenario_id="regression-set-v1", provenance="local-capture",
                        inference_backend="unknown")   # declared, never guessed
latency = UserBotLatencyObserver()
jsonl = PipecatJsonlObserver(writer)
jsonl.attach(latency)
worker = PipelineWorker(pipeline, params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
                        observers=[latency, jsonl])
writer.begin_session("call-0001"); writer.begin_turn("u1")   # turn ids come from your scenario
```

Envelope fields: `schema`, `schema_version`, `framework`, `framework_version`,
`run_id`, `config_id`, `scenario_id`, `session_id`, `turn_id`, `turn_status`,
`seq`, `event_id`, `event_type` (`latency_breakdown`, `metrics` or `error`),
`native_type`, `provenance` (`synthetic` or `local-capture`), `inference_backend`
(default `unknown`), `observed_at`, `payload`. A manifest next to each log
(`<name>.manifest.json`) lists the expected turns and the run's conditions.

Payloads: `LatencyBreakdown` and every `MetricsData` are pydantic models,
written with `model_dump(mode="json")`; `ErrorFrame` is a dataclass whose
`processor` and `exception` fields are live objects, so the line keeps `error`,
`fatal`, `category` and the processor's name. A line without the envelope, an
unknown framework version, event type, native type or key set is an input
error: it never produces `PASS`. A session imported twice is read once.

## Parser cases, as found in pipecat-ai 1.15.0

File and line references: [`docs/pipecat-1.15.0-sources.md`](docs/pipecat-1.15.0-sources.md).

| Case | Where it shows up in 1.15.0 | What voice-check does |
|---|---|---|
| TTFB of 0.0 at pipeline start | `metrics` lines outside any turn (`PipelineWorker._initial_metrics_frame`); never in a breakdown | counted as "initialization", kept apart from a true 0.0 inside a turn, an absent field and a stage that does not apply |
| `model` empty or null | Kokoro reports `""`, the startup zeros `null` | kept, labelled "model not reported" |
| TTFA and TTFAT | `metrics` lines; `ttfa = ttfb + leading_silence`, `ttfat = ttfb + thinking_time`, and their `ttfb` sub-field copies the TTFB | the copy is not counted again; `leading_silence` and `thinking_time` are kept |
| "completed with no audio" | `error` lines (an `ErrorFrame` pushed upstream) | reading continues; a turn with no reply is a failed turn in the denominator |
| LLM served by an OpenAI-compatible server | the processor is `OpenAILLMService#0` whatever serves it | the backend comes from the declared `inference_backend`, default `unknown` |

## Correspondence with the post's four lines

"No verified equivalent" means the field exists but its boundaries were not
checked against the post's definition; the nearest field is given for reading,
not for comparison.

| Line in the post (streaming target) | Pipecat 1.15.0 | LiveKit Agents 1.8.11 |
|---|---|---|
| Remaining turn detection and STT processing (120 ms) | no verified equivalent (nearest: contributions `endpointing_wait`, `transcription`, `turn_detection`) | no verified equivalent (nearest: `end_of_turn_delay`, `transcription_delay`) |
| LLM response generation / time to first token (120 ms) | no verified equivalent (nearest: contribution `llm_inference`, ending at the first chunk) | no verified equivalent (nearest: `llm_node_ttft`) |
| TTS synthesis / time to first audio (100 ms) | no verified equivalent (nearest: contribution `speech_synthesis`) | no verified equivalent (nearest: `tts_node_ttfb`) |
| Network, buffering and playback (80 ms) | no verified equivalent: client-side, not measured | no verified equivalent |
| Total (420 ms) | no verified equivalent (nearest: `total_secs`, ending at `BotStartedSpeakingFrame`, not at audio playable to the caller) | no verified equivalent (nearest: `e2e_latency`) |

LiveKit's fields were located in the 1.8.11 source
([`docs/livekit-agents-1.8.11-sources.md`](docs/livekit-agents-1.8.11-sources.md)).
No LiveKit adapter ships yet: none has been validated on a real
`SessionReport.to_dict()` export.

## The synthetic fixtures

`fixtures/baseline.jsonl` and `fixtures/candidate.jsonl` are synthetic and say so
(`provenance: synthetic`, `inference_backend: simulated`). They were not typed by
hand: `capture/synthetic_scenario.py` drives Pipecat 1.15.0's real
`UserBotLatencyObserver` with a scripted clock and scripted frames (no model
runs), and the same envelope writer records what it emits.

* 200 sessions of 5 user turns (1,000 turns) plus one greeting per session.
* Baseline: 200 turns at each of 380, 390, 400, 410 and 420 ms.
* Candidate: the LLM's first chunk arrives 500 ms later on every turn of 20
  sessions (100 turns, 20 at each value). The delay is injected into the events;
  first text, first audio and bot speaking move with it.

| | Baseline | Candidate |
|---|---|---|
| Median | 400 ms | 400 ms |
| Mean | 400 ms | 450 ms |
| p95 | 420 ms | 900 ms |
| Turns over 420 ms | 0 | 100 |
| Verdict | PASS | FAIL |

`uv run python scripts/make_fixtures.py` regenerates them byte for byte (a test
checks it). `uv run python scripts/build_reports.py` runs the two control cases:
the candidate regenerated without the delay (`PASS`, exit 0), and the candidate
with its 100 slow turns deleted from the log (900 turns left, p95 420 ms, but
`INCOMPLETE`, exit 2, because 100 expected turns are missing).

## A real local capture

`capture/out/` holds a real run of a full Pipecat 1.15.0 pipeline on one Apple
M1 Max Mac with 64 GB (macOS 27.2, no NVIDIA GPU), with nothing in the cloud:

* input: four caller utterances generated with Kokoro (synthetic speech, not a
  person), replayed at real time; text, voice and hashes in `capture/audio/provenance.json`;
* Silero VAD and Pipecat's default turn stop strategy (`LocalSmartTurnAnalyzerV3`);
* STT: `WhisperSTTService`, faster-whisper 1.2.1, model `base`, on CPU (int8);
* LLM: `OpenAILLMService` pointed at Ollama 0.40.13 on the same Mac, `qwen2.5:3b`
  (Q4_K_M, digest in the manifest), `inference_backend: ollama` declared;
* TTS: `KokoroTTSService`, kokoro-onnx 0.6.1;
* transport: none; frames go in at real time and audio out is dropped at real time;
* three sessions, one at a time, each a fresh pipeline; nothing trimmed.

`uv run voice-check summarize capture/out/local-capture.jsonl` gives 12 measured
turns: median 7.86 s, min 3.36 s, max 16.19 s, and no p95 (N = 12). Kokoro also
logged eight "completed with no audio" errors in turns that still got a spoken
reply. These are laptop numbers: they are nowhere near 420 ms and are not meant
to be. Totals rose from the first session to the third; this run does not say why.
Nothing here is presented as equivalent to a production inference server.

To run it again (models are downloaded on first use, all free and local):

```
ollama serve &
ollama pull qwen2.5:3b
uv sync --all-extras
uv run python capture/make_input_audio.py
uv run python capture/run_local_capture.py
```

## Tests

```
uv run pytest
```

They cover the brief's table, the return to `PASS` without the delay, the
`INCOMPLETE` case with the slow traces removed, the exact-threshold and
float-noise cases, a three-turn report with no numeric p95, every parser case
above, duplicates, greetings, overlapping contributions, unknown versions and
structures, and a line of each event type from the real capture read back
without losing a field.

## Versions

Pinned in `uv.lock`: pipecat-ai 1.15.0 (wheel sha256
`e3c004bef6003567f4b20d098bce5129c1f17554ac44aabad3f7755d613d8815`) and
livekit-agents 1.8.11 (wheel sha256
`addb4005d8c65d64054ee0d0893c808c8dfb193bac915f1999f0897956ee2d12`). The
sources were read in those installed wheels, not on `main`.

Built by Frederic de Lavenne de Choulot, The AI Pipe. BSD-2-Clause.
