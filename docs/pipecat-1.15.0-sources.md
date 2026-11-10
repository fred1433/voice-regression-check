# What pipecat-ai 1.15.0 actually serializes

Read in the installed wheel, not on `main`:
`pipecat_ai-1.15.0-py3-none-any.whl`, sha256
`e3c004bef6003567f4b20d098bce5129c1f17554ac44aabad3f7755d613d8815`, pinned in `uv.lock`.
Paths below are relative to `site-packages/pipecat/`. Every case was then checked
against the real capture in `capture/out/local-capture.jsonl`.

## Types and how they serialize

| Object | Kind | Where | Serialized with |
|---|---|---|---|
| `LatencyBreakdown` | pydantic `BaseModel` | `observers/user_bot_latency_observer.py:395` | `model_dump(mode="json")` |
| `LatencyContribution` | pydantic `BaseModel` | same file, `:366` | inside the breakdown |
| `TTFBBreakdownMetrics` | pydantic `BaseModel` | same file, `:48` | inside the breakdown |
| `MetricsData` and subclasses | pydantic `BaseModel` | `metrics/metrics.py:19` | `model_dump(mode="json")`, class name kept in `native_type` |
| `MetricsFrame` | dataclass holding `data: list[MetricsData]` | `frames/frames.py:1671` | one envelope line per item |
| `ErrorFrame` | dataclass | `frames/frames.py:1296` | `error`, `fatal`, `category` and the processor's name (the `processor` and `exception` fields are live objects) |

`LatencyBreakdown` fields (`:426-433`): `ttfb`, `contributions`, `measured_from`,
`total_secs`, `text_aggregation`, `user_turn_start_time`, `user_turn_secs`,
`function_calls`. The parser rejects any other key set.

## Units

All durations are seconds as floats: `TTFBMetricsData.value` (`metrics/metrics.py:38`),
`LatencyBreakdown.total_secs` and every `duration_secs`. voice-check converts to
milliseconds only in `report.json` (`"unit": "ms"`) and compares at the microsecond.

## Where each parser case appears in 1.15.0

| Case | Where it appears | Evidence |
|---|---|---|
| TTFB of 0.0 at start | **`metrics` lines only**, outside any turn. Never in a breakdown. | `pipeline/worker.py:1276-1282` builds `TTFBMetricsData(processor=p.name, value=0.0)` for every processor, sent at `:1541` when `send_initial_empty_metrics` is on (default `True`, `:207`). The observer keeps only `metrics_data.value > 0` (`user_bot_latency_observer.py:1016`). Seen in the capture: six zero lines (TTFB and processing, three services) before the first turn of each session. |
| `model` empty or null | **Both** `metrics` lines and `breakdown.ttfb[].model`. | `MetricsData.model: str \| None = None` (`metrics/metrics.py:28`); `TTFBBreakdownMetrics.model` (`user_bot_latency_observer.py:59`). Seen in the capture: Kokoro reports `""`, the startup zeros and `TurnMetricsData` report `null`. |
| TTFA and TTFAT copying the TTFB | **`metrics` lines only** (`TTFAMetricsData`, `TTFATMetricsData`). | `metrics/metrics.py:41-96`: `ttfa = ttfb + leading_silence`, `ttfat = ttfb + thinking_time`, and the `ttfb` sub-field "mirrors the standalone TTFBMetricsData ... don't aggregate both" (`:52-54`, `:85-87`). Emitted in `processors/metrics/frame_processor_metrics.py:231` and `:272`. |
| "completed with no audio" | **`error` lines only**, travelling upstream. | `services/tts_service.py:2303-2322` calls `push_error(...)`, which pushes an `ErrorFrame` upstream (`processors/frame_processor.py:893-903`). Seen in the capture inside turns that still got a spoken reply. |
| Interruptions | Not in the breakdown: the observer drops its accumulators on `InterruptionFrame` (`user_bot_latency_observer.py:687-689`). | voice-check keeps an interruption as the turn's status only, and only when it cuts a reply: audio already playing, or a reply engaged (`LLMFullResponseStartFrame` or `TTSStartedFrame`) before its first audio. |
| Greetings | A breakdown with `measured_from: client_connected` (`:341-349`, `:985-990`). | Kept apart from user turns. |
| Unanchored cycle | A breakdown with `measured_from: null` and `total_secs: 0.0` when no contribution could be built (`:985-992`). | Counted as unanchored, never as a 0 ms turn. |

## What the turn total covers

`total_secs` is the sum of `contributions` (`:992`), which tile the interval from
the user's estimated silence (`VADUserStoppedSpeakingFrame.timestamp - stop_secs`,
`:652`) to `BotStartedSpeakingFrame` (`:708-710`). The observer only watches
downstream frames (`:629`). Both ends are seen inside the bot's process: this is
an SDK-observed measure. Audio delivery and playback on the caller's side are not in it.

## Zeros and missing values: four cases kept apart

* initialization: the startup TTFB of 0.0 above;
* zero value: a 0.0 inside a turn, kept as 0;
* absent: a core stage (`core=True` spans, `:179-313`) missing from a measured turn, or `user_turn_secs: null`;
* not applicable: no function call, no text aggregation.

## Not used

Barge-in latency: there is no reliable metric for it in 1.15.0, so none is computed.
