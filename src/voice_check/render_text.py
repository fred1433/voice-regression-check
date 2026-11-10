"""Terminal output, rendered from a report dict (the same one written to report.json)."""

from __future__ import annotations


def _ms(v) -> str:
    """Whole milliseconds from 1 s up (7,856 ms); below, up to the microsecond
    the comparison uses (420.001 ms), without trailing zeros."""
    if v is None:
        return "n/a"
    if abs(v) >= 1000 or float(v).is_integer():
        return f"{v:,.0f} ms"
    return f"{v:,.3f}".rstrip("0").rstrip(".") + " ms"


def _dist(d: dict) -> str:
    med = d["median_note"] if d.get("median_note") else f"median {_ms(d.get('median_ms'))}"
    if d.get("p95_ms") is None and d.get("p95_note"):
        p95 = d["p95_note"]
        if p95.startswith("p95 not shown"):
            return f"{med}, {p95}"
    else:
        p95 = f"p95 {_ms(d.get('p95_ms'))}"
    return f"{med}, {p95} (N = {d['n']:,})"


def _ident(s: dict) -> str:
    if s.get("config_id") is None:
        return "log not read"
    return f"{s['config_id']}  [{s['provenance']}, backend {s['inference_backend']}]"


def _counts(c: dict) -> str:
    return (
        f"{c['sessions_observed']:,}/{c['sessions_expected']:,} sessions, "
        f"{c['turns_expected']:,} turns expected, {c['turns_measured']:,} measured, "
        f"{c['failed_turns']:,} failed (no audio {c['failed_no_audio']:,}, no response {c['failed_no_response']:,}), "
        f"{c['incomplete_turns']:,} incomplete, {c['responded_then_interrupted'] + c['interrupted_before_response']:,} interrupted"
    )


def render(report: dict) -> str:
    if report["command"] == "summarize":
        return _render_single(report)
    out = [report["label"], report["label_note"], ""]
    for r in report["reasons"]:
        out.append(f"  reason: {r['code']}" + (f" ({r['run']})" if r.get("run") else "") + f": {r['detail']}")
    if report["reasons"]:
        out.append("")
    pol = report["policy"]
    rule = pol["rule"]
    out.append(f"Policy  {pol['path']}: turn total {rule['statistic']} {rule['operator']} {_ms(rule['threshold_ms'])} ({pol['nature']})")
    for name in ("baseline", "candidate"):
        s = report["runs"][name]
        out.append(f"{name.capitalize():<10}{_ident(s)}")
        out.append(f"          {_dist(s['total'])}")
        out.append(f"          {_counts(s['counts'])}")
    ev = report.get("evaluation")
    if ev:
        out.append("")
        verdict = "yes" if ev["passed"] else "no"
        if ev.get("value_note"):
            out.append(f"Candidate p95 {ev['operator']} {_ms(ev['threshold_ms'])}: {verdict}, the {ev['value_note']} (N = {ev['n']:,})")
        else:
            out.append(f"Candidate p95 {_ms(ev['value_ms'])} {ev['operator']} {_ms(ev['threshold_ms'])}: {verdict} (N = {ev['n']:,})")
    ch = report.get("change")
    if ch and ch["affected_turns"]:
        f = ch["facts"]
        out.append("")
        out.append(f"Affected turns: {f['affected_turns']:,} over the threshold, in {f['affected_sessions']:,} of {f['sessions_expected']:,} sessions")
        if f["every_turn_of_those_sessions_affected"]:
            out.append("  every turn of those sessions is affected, and no turn elsewhere")
        for m in ch["changed_measures"][:3]:
            out.append(f"  contribution {m['contribution_key']}: +{m['sum_delta_ms']:,.0f} ms summed, {m['share_of_total_delta']:.0%} of the added time")
        for a in ch["associated_metrics"]:
            out.append(f"  associated metric: {a['metric'].upper()} of {a['processor']}")
        if ch["hypotheses_compatible"]:
            out.append(f"  {ch['hypotheses_note']}")
            out.extend(f"   - {h}" for h in ch["hypotheses_compatible"])
        out.append("  Missing to decide:")
        out.extend(f"   - {m}" for m in ch["missing_to_decide"])
        if ch.get("known_cause"):
            kc = ch["known_cause"]
            out.append(f"  Known cause (synthetic only): {kc['description']} Matches the diagnosis: {'yes' if kc['matches_diagnosis'] else 'no'}.")
        first = ch["affected_turns"][0]
        out.append(f"  First affected turn: {first['session_id']}/{first['turn_id']}, {_ms(first['baseline_total_ms'])} -> {_ms(first['candidate_total_ms'])}")
    out.append("")
    out.append(f"Scope: SDK-observed. Not measured: {'; '.join(report['scope']['not_measured'])}.")
    out.append(f"Exit code {report['exit_code']}")
    return "\n".join(out)


def _render_single(report: dict) -> str:
    s = report["run"]
    out = [report["label"], _ident(s), f"  {_counts(s['counts'])}"]
    t = s["total"]
    out.append(f"  user turns: {_dist(t)}, min {_ms(t.get('min_ms'))}, max {_ms(t.get('max_ms'))}")
    g = s["greetings"]
    if g["n"]:
        out.append(f"  greetings (kept apart): {_dist(g)}")
    out.append("")
    if report["turn_table"]:
        out.append(f"  {'turn':<12}{'status':<28}{'total':>12}")
    for row in report["turn_table"]:
        out.append(f"  {row['session_id'] + '/' + row['turn_id']:<12}{row['status']:<28}{_ms(row['total_ms']):>12}")
    for e in s["input_errors"][:10]:
        out.append(f"  input error: {e}")
    out.append(f"Exit code {report['exit_code']}")
    return "\n".join(out)
