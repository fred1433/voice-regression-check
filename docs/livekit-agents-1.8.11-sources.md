# LiveKit Agents 1.8.11: fields located, adapter not shipped

Read in the installed wheel `livekit_agents-1.8.11-py3-none-any.whl`, sha256
`addb4005d8c65d64054ee0d0893c808c8dfb193bac915f1999f0897956ee2d12`, pinned in `uv.lock`.
Paths are relative to `site-packages/livekit/agents/`.

No LiveKit adapter ships with voice-check yet, because none has been validated
on a real `SessionReport.to_dict()` export. This file only records where the
fields live, for the correspondence table in the README.

| Field | Where | Meaning in the source |
|---|---|---|
| `SessionReport` | `voice/report.py:34` | dataclass; `to_dict()` at `:58` |
| events in `to_dict()` | `voice/report.py:66-73` | `metrics_collected` events are skipped; chat history is the source of truth |
| `MetricsReport` | `llm/chat_context.py:286` | `TypedDict, total=False`: any key can be absent |
| `transcription_delay` | `llm/chat_context.py:290` | user message only |
| `end_of_turn_delay` | `llm/chat_context.py:296` | user message only |
| `llm_node_ttft` | `llm/chat_context.py:308` | assistant message only |
| `tts_node_ttfb` | `llm/chat_context.py:330` | assistant message only |
| `playback_latency` | `llm/chat_context.py:336` | "Near-zero for the default room output ... doesn't account for network delivery to the client" |
| `e2e_latency` | `llm/chat_context.py:346`; computed `voice/agent_activity.py:3439-3441` | `started_speaking_at - stopped_speaking_at`, two `time.time()` values: seconds |
| `interrupted` | `llm/chat_context.py:412` | a boolean on the message, not a time |

Because `MetricsReport` is `total=False`, a missing key (for example with a
realtime model) must stay missing, never become 0. `playback_latency` is not
"network, buffering and playback": the source says it does not include network
delivery to the client.
