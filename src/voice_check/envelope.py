"""The envelope this project wraps around native Pipecat objects (our convention).

Every key set below was read in pipecat-ai 1.15.0; see
``docs/pipecat-1.15.0-sources.md`` for files and lines. A line whose framework
version, event type, native type or key set is not listed here is an input
error, and an input error never produces PASS.
"""

SCHEMA = "voice-check.envelope"
SCHEMA_VERSION = 1
MANIFEST_SCHEMA = "voice-check.manifest"

SUPPORTED_FRAMEWORKS = {"pipecat-ai": {"1.15.0"}}

ENVELOPE_REQUIRED = {
    "schema",
    "schema_version",
    "framework",
    "framework_version",
    "run_id",
    "config_id",
    "scenario_id",
    "session_id",
    "turn_id",
    "turn_status",
    "seq",
    "event_id",
    "event_type",
    "native_type",
    "provenance",
    "inference_backend",
    "observed_at",
    "payload",
}
ENVELOPE_OPTIONAL = {"frame_ref"}

PROVENANCES = {"synthetic", "local-capture"}

TURN_STATUSES = {
    "responded",
    "responded_then_interrupted",
    "no_audio",
    "interrupted_before_response",
    "no_response",
}
RESPONDED = {"responded", "responded_then_interrupted"}
FAILED = {"no_audio", "no_response"}

_METRICS_BASE = {"processor", "model"}

NATIVE_TYPES = {
    "latency_breakdown": {
        # LatencyBreakdown, user_bot_latency_observer.py lines 395-433
        "LatencyBreakdown": {
            "ttfb",
            "contributions",
            "measured_from",
            "total_secs",
            "text_aggregation",
            "user_turn_start_time",
            "user_turn_secs",
            "function_calls",
        },
    },
    "metrics": {
        # pipecat/metrics/metrics.py
        "TTFBMetricsData": _METRICS_BASE | {"value"},
        "TTFAMetricsData": _METRICS_BASE | {"ttfa", "ttfb", "leading_silence"},
        "TTFATMetricsData": _METRICS_BASE | {"ttfat", "ttfb", "thinking_time"},
        "ProcessingMetricsData": _METRICS_BASE | {"value"},
        "LLMUsageMetricsData": _METRICS_BASE | {"value"},
        "STTUsageMetricsData": _METRICS_BASE | {"value"},
        "TTSUsageMetricsData": _METRICS_BASE | {"value"},
        "TextAggregationMetricsData": _METRICS_BASE | {"value"},
        "TurnMetricsData": _METRICS_BASE | {"is_complete", "probability", "e2e_processing_time_ms"},
        "SmartTurnMetricsData": _METRICS_BASE
        | {"is_complete", "probability", "e2e_processing_time_ms", "inference_time_ms", "server_total_time_ms"},
    },
    "error": {
        # ErrorFrame is a dataclass (frames.py line 1296); the writer keeps these keys.
        "ErrorFrame": {"error", "fatal", "category", "processor"},
        "FatalErrorFrame": {"error", "fatal", "category", "processor"},
    },
}

CONTRIBUTION_KEYS = {"key", "label", "owner", "owner_kind", "start_time", "duration_secs"}
TTFB_ITEM_KEYS = {"processor", "model", "start_time", "duration_secs"}
FUNCTION_CALL_KEYS = {"function_name", "start_time", "duration_secs"}
TEXT_AGGREGATION_KEYS = {"processor", "start_time", "duration_secs"}
MEASURED_FROM = {"user_silence", "client_connected", None}

# Spans Pipecat marks ``core=True`` (user_bot_latency_observer.py lines 179-313):
# a core stage missing from a user turn is "absent", any other missing stage is
# "not applicable" to that turn.
CORE_USER_TURN_KEYS = ["endpointing_wait", "transcription", "turn_detection", "llm_inference", "speech_synthesis"]

NO_AUDIO_MARKER = "completed with no audio"  # tts_service.py lines 2303-2322

# Numeric fields of a metrics payload that are durations in seconds: each must
# be a finite number >= 0 (pipecat/metrics/metrics.py, all typed ``float``).
METRIC_DURATIONS = {
    "TTFBMetricsData": ("value",),
    "ProcessingMetricsData": ("value",),
    "TextAggregationMetricsData": ("value",),
    "TTFAMetricsData": ("ttfa", "ttfb", "leading_silence"),
    "TTFATMetricsData": ("ttfat", "ttfb", "thinking_time"),
}
