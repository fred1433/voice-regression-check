"""voice-check: compare two runs of the same voice scenarios, offline.

Exit codes for ``compare``: 0 PASS, 1 FAIL, 2 INCOMPLETE (including an input
error, a missing policy or a bad argument).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .analysis import PolicyError, compare, load_policy, single
from .parse import load_run
from .render_text import render

EXIT_INCOMPLETE = 2


def _write_report(report: dict, path: str | None) -> None:
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")


def cmd_compare(a) -> int:
    if not a.policy:
        print(
            "INCOMPLETE: no policy. Pass --policy <file.yaml>; voice-check has no default threshold.\n"
            "Example: --policy policies/published-example-420.yaml",
            file=sys.stderr,
        )
        return EXIT_INCOMPLETE
    try:
        policy = load_policy(a.policy)
    except PolicyError as exc:
        print(f"INCOMPLETE: input error: {exc}", file=sys.stderr)
        return EXIT_INCOMPLETE
    base = load_run(a.baseline, a.baseline_manifest)
    cand = load_run(a.candidate, a.candidate_manifest)
    report = compare(base, cand, policy)
    _write_report(report, a.report)
    print(render(report))
    if a.report:
        print(f"Report written to {a.report}")
    return report["exit_code"]


def cmd_summarize(a) -> int:
    run = load_run(a.log, a.manifest)
    report = single(run, min_n=a.min_turns)
    _write_report(report, a.report)
    print(render(report))
    return report["exit_code"]


def cmd_show(a) -> int:
    p = Path(a.report)
    if not p.exists():
        print(f"report not found: {p}", file=sys.stderr)
        return EXIT_INCOMPLETE
    report = json.loads(p.read_text())
    print(render(report))
    return report["exit_code"]


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # argparse exits 2 already; keep the wording ours
        self.print_usage(sys.stderr)
        print(f"INCOMPLETE: bad arguments: {message}", file=sys.stderr)
        sys.exit(EXIT_INCOMPLETE)


def main(argv: list[str] | None = None) -> int:
    ap = _Parser(prog="voice-check", description="Reproducible voice regression check (offline log analysis).")
    ap.add_argument("--version", action="version", version=f"voice-check {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True, parser_class=_Parser)

    c = sub.add_parser("compare", help="compare a candidate run against a baseline run of the same scenarios")
    c.add_argument("baseline", help="baseline envelope JSONL")
    c.add_argument("candidate", help="candidate envelope JSONL")
    c.add_argument("--policy", help="policy YAML (required: there is no default threshold)")
    c.add_argument("--report", help="write report.json here")
    c.add_argument("--baseline-manifest", help="default: <baseline>.manifest.json next to the log")
    c.add_argument("--candidate-manifest", help="default: <candidate>.manifest.json next to the log")
    c.set_defaults(func=cmd_compare)

    s = sub.add_parser("summarize", help="summarize one run (no verdict)")
    s.add_argument("log", help="envelope JSONL")
    s.add_argument("--manifest", help="default: <log>.manifest.json next to the log")
    s.add_argument("--report", help="write report.json here")
    s.add_argument("--min-turns", type=int, default=300, help="below this N no p95 is shown (default 300)")
    s.set_defaults(func=cmd_summarize)

    w = sub.add_parser("show", help="print a report.json as terminal output")
    w.add_argument("report")
    w.set_defaults(func=cmd_show)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
