"""Render site/index.html from the reports in reports/ (run build_reports.py first).

Every number on the page is read from a report.json written by the CLI.
"""

import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from voice_check.render_text import render  # noqa: E402

REPO = "https://github.com/fred1433/voice-regression-check"
PAGE = "https://theaipipe.com/demos/voice-regression-check/"
POST = "https://www.bearworks.com/post/sub-500ms-voice-agents-stt-llm-tts-latency"
CAL = "https://cal.theaipipe.com"
FAVICON = ROOT / "site" / "favicon.svg"  # The AI Pipe favicon, already in site/
OG = ROOT / "site" / "og.png"  # link preview: a 1200x630 capture of this page, committed

# capture table: the stages shown by name; every other contribution goes to "Other"
CAP_COLS = [
    ("transcription", "Transcription", "STT"),
    ("llm_inference", "LLM inference", "LLM"),
    ("sentence_aggregation", "Sentence aggregation", "Aggr."),
    ("speech_synthesis", "Speech synthesis", "TTS"),
]


def load(name):
    return json.loads((ROOT / "reports" / f"{name}.json").read_text())


def ms(v, unit=True):
    if v is None:
        return "n/a"
    s = f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"
    return f"{s} ms" if unit else s


def secs(v):
    return f"{v / 1000:,.2f} s"


def esc(s):
    return html.escape(str(s), quote=True)


def capture_rows(cap_r):
    """Seconds with two decimals; "Other" is the measured total minus the
    displayed stages, so every row adds up on screen. Checked against the sum
    of the remaining contributions."""
    rows = []
    for row in cap_r["turn_table"]:
        assert row["total_ms"] is not None, row
        cms = row["contributions_ms"]
        shown = [round(cms.get(k, 0.0) / 1000, 2) for k, _, _ in CAP_COLS]
        total = round(row["total_ms"] / 1000, 2)
        other = round(total - sum(shown), 2)
        rest = sum(v for k, v in cms.items() if k not in {c[0] for c in CAP_COLS}) / 1000
        assert abs(other - rest) <= 0.03, (row, other, rest)
        assert abs(sum(cms.values()) - row["total_ms"]) < 0.01, row  # contributions cover the measured total
        cells = "".join(f"<td>{v:,.2f}</td>" for v in shown)
        rows.append(f"<tr><td>{esc(row['session_id'] + '/' + row['turn_id'])}</td><td><b>{total:,.2f}</b></td>{cells}<td>{other:,.2f}</td></tr>")
    return "".join(rows)


def main():
    main_r, pass_r, inc_r, cap_r = (load(n) for n in ("synthetic-compare", "synthetic-delay-removed", "synthetic-slow-traces-removed", "local-capture"))
    assert main_r["verdict"] == "FAIL" and pass_r["verdict"] == "PASS" and inc_r["verdict"] == "INCOMPLETE"

    b, c = main_r["runs"]["baseline"], main_r["runs"]["candidate"]
    ch = main_r["change"]
    thr = main_r["policy"]["rule"]["threshold_ms"]
    kc = ch["known_cause"]
    assert kc and kc["matches_diagnosis"] and ch["primary_contribution"] == "llm_inference"
    deltas = sorted({r["delta_ms"] for r in ch["affected_turns"]})
    assert len(deltas) == 1, deltas  # the page says "each N ms slower"
    delta = deltas[0]
    featured = next(r for r in ch["affected_turns"] if r["baseline_total_ms"] == b["total"]["median_ms"])
    affected = sorted(
        (
            {
                "id": f"{r['session_id']}/{r['turn_id']}",
                "b": r["baseline_total_ms"],
                "c": r["candidate_total_ms"],
                "tr": {k: [[x["key"], x["label"], x["start_ms"], x["duration_ms"]] for x in v] for k, v in r["trace"].items()},
            }
            for r in ch["affected_turns"]
        ),
        key=lambda a: (a["id"].split("/")[0], int(a["id"].split("/u")[1])),
    )
    data = {
        "thr": thr,
        "base": [[f"{s}/{t}", v] for s, t, v, _ in main_r["turns"]["baseline"]],
        "cand": [[f"{s}/{t}", v] for s, t, v, _ in main_r["turns"]["candidate"]],
        "affected": affected,
        "featured": f"{featured['session_id']}/{featured['turn_id']}",
        "median": [b["total"]["median_ms"], c["total"]["median_ms"]],
        "p95": [b["total"]["p95_ms"], c["total"]["p95_ms"]],
        "delta": delta,
    }
    over = [v for _, v in data["cand"] if v is not None and v > thr]
    within = [v for _, v in data["cand"] if v is not None and v <= thr]
    base_vals = [v for _, v in data["base"] if v is not None]
    chart_label = (
        f"Each square is one user turn. Before: {len(base_vals):,} turns between {ms(min(base_vals))} and {ms(max(base_vals))}. "
        f"After: {len(within):,} turns between {ms(min(within))} and {ms(max(within))}, and {len(over):,} turns between "
        f"{ms(min(over))} and {ms(max(over))}, over the {ms(thr)} budget."
    )

    cm = ch["changed_measures"][0]
    facts = ch["facts"]
    inc_c = inc_r["runs"]["candidate"]
    cap = cap_r["run"]
    cap_t = cap["total"]
    cap_c = cap["counts"]
    machine = cap["conditions"]["machine"]
    capcfg = cap["config"]
    assert cap_c["turns_measured"] == cap_c["turns_expected"] == cap_t["n"]
    assert all(r["status"] == "responded" for r in cap_r["turn_table"])

    hyps = "".join(f"<li>{esc(h)}</li>" for h in ch["hypotheses_compatible"])
    missing = "".join(f"<li>{esc(m)}</li>" for m in ch["missing_to_decide"])
    cmd = ("uv run voice-check compare fixtures/baseline.jsonl fixtures/candidate.jsonl \\\n"
           "  --policy policies/published-example-420.yaml --report report.json")
    cap_head = "".join(f'<th><span class="long">{esc(lg)}</span><span class="short">{esc(sh)}</span></th>' for _, lg, sh in CAP_COLS)

    page = TEMPLATE
    subs = {
        "DATA": json.dumps(data, separators=(",", ":")),
        "CHART_LABEL": esc(chart_label),
        "LABEL": esc(main_r["label"]),
        "EXIT": str(main_r["exit_code"]),
        "THR": ms(thr),
        "B_MED": ms(b["total"]["median_ms"], unit=False),
        "C_MED": ms(c["total"]["median_ms"]),
        "B_P95": ms(b["total"]["p95_ms"], unit=False),
        "C_P95": ms(c["total"]["p95_ms"]),
        "N_EACH": f"{c['total']['n']:,}" if b["total"]["n"] == c["total"]["n"] else f"{b['total']['n']:,} and {c['total']['n']:,}",
        "SESSIONS": f"{c['counts']['sessions_expected']:,}",
        "EXPECTED": f"{c['counts']['turns_expected']:,}",
        "MEAS": f"{c['counts']['turns_measured']:,}" if b["counts"]["turns_measured"] == c["counts"]["turns_measured"] else f"{b['counts']['turns_measured']:,} and {c['counts']['turns_measured']:,}",
        "GREET": f"{c['counts']['greetings_kept_apart']:,}",
        "B_MEAN": ms(b["total"]["mean_ms_measured_only"], unit=False),
        "C_MEAN": ms(c["total"]["mean_ms_measured_only"]),
        "AFF_N": f"{facts['affected_turns']:,}",
        "AFF_S": f"{facts['affected_sessions']:,}",
        "DELTA": ms(delta),
        "AFF_ALL": "every turn of those sessions, and no turn elsewhere" if facts["every_turn_of_those_sessions_affected"] else "spread across sessions",
        "CM_KEY": esc(next(x["label"] for x in c["contributions"] if x["key"] == cm["contribution_key"])),
        "CM_SHARE": f"{cm['share_of_total_delta']:.0%}",
        "ASSOC": esc(", ".join(f"{a['metric'].upper()} of {a['processor']}" for a in ch["associated_metrics"]) or "none in the logs"),
        "HYP_NOTE": esc(ch["hypotheses_note"]),
        "HYPS": hyps,
        "MISSING": missing,
        "KNOWN": esc(kc["description"]),
        "KNOWN_MATCH": "matches" if kc["matches_diagnosis"] else "does not match",
        "FEAT": esc(data["featured"]),
        "FEAT_B": ms(featured["baseline_total_ms"]),
        "FEAT_C": ms(featured["candidate_total_ms"]),
        "PASS_LABEL": esc(pass_r["label"]),
        "PASS_P95": ms(pass_r["evaluation"]["value_ms"]),
        "PASS_N": f"{pass_r['evaluation']['n']:,}",
        "PASS_EXIT": str(pass_r["exit_code"]),
        "INC_LABEL": esc(inc_r["label"]),
        "INC_P95": ms(inc_c["total"]["p95_ms"]),
        "INC_N": f"{inc_c['total']['n']:,}",
        "INC_MISSING": f"{inc_c['counts']['missing_from_log']:,}",
        "INC_EXIT": str(inc_r["exit_code"]),
        "CAP_N": f"{cap_t['n']:,}",
        "CAP_RESP": f"{sum(r['status'] == 'responded' for r in cap_r['turn_table']):,}/{cap_c['turns_expected']:,}",
        "CAP_SESS": f"{cap_c['sessions_expected']:,}",
        "CAP_MED": secs(cap_t["median_ms"]),
        "CAP_MIN": secs(cap_t["min_ms"]),
        "CAP_MAX": secs(cap_t["max_ms"]),
        "CAP_P95": esc(cap_t["p95_note"]),
        "CAP_HEAD": cap_head,
        "CAP_ROWS": capture_rows(cap_r),
        "CAP_CPU": esc(machine["cpu"]),
        "CAP_MEM": str(machine["memory_gb"]),
        "CAP_OS": esc(f"macOS {machine['macos']}"),
        "CAP_LLM": esc(capcfg["ollama"]["model"]),
        "CAP_QUANT": esc(capcfg["ollama"]["details"]["quantization_level"]),
        "CAP_DIGEST": esc((capcfg["ollama"].get("digest") or "")[:12]),
        "CAP_OLLAMA": esc(capcfg["ollama"]["ollama_version"]),
        "CAP_STT": esc(capcfg["stt"]),
        "CAP_TTS": esc(capcfg["tts"]),
        "CAP_TURN": esc(capcfg["turn_stop_strategy"]),
        "CAP_VAD": f"{capcfg['vad_stop_secs']:g}",
        "CAP_TRANSPORT": esc(cap["conditions"]["transport"]),
        "CAP_STARTUP": esc(cap["conditions"]["startup"]),
        "CAP_NOAUDIO": str(cap_c["no_audio_errors_in_responded_turns"]),
        "TERMINAL": esc(render(main_r)),
        "CMD": esc(cmd),
        "REPO": REPO,
        "PAGE": PAGE,
        "POST": POST,
        "CAL": CAL,
    }
    for k, v in subs.items():
        page = page.replace("{{" + k + "}}", v)
    assert "{{" not in page, page[page.index("{{"): page.index("{{") + 40]
    assert chr(0x2014) not in page
    site = ROOT / "site"
    site.mkdir(exist_ok=True)
    (site / "index.html").write_text(page)
    assert FAVICON.exists(), FAVICON
    print(f"wrote {site / 'index.html'} ({len(page):,} bytes)")


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Voice regression check</title>
<meta name="description" content="Two synthetic Pipecat runs: the median stays at {{C_MED}}, the p95 goes from {{B_P95}} to {{C_P95}}, and the check fails with the {{AFF_N}} turns over budget.">
<meta property="og:type" content="website">
<meta property="og:url" content="{{PAGE}}">
<meta property="og:title" content="A voice regression hidden by an unchanged median">
<meta property="og:description" content="Synthetic Pipecat example: the median stays at {{C_MED}}, the p95 goes from {{B_P95}} to {{C_P95}}, and the check lists the {{AFF_N}} turns over budget.">
<meta property="og:image" content="{{PAGE}}og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="Strip chart of {{EXPECTED}} turns per run, before and after: the median stays at {{C_MED}}, {{AFF_N}} turns move past the {{THR}} budget.">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,300..800&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{
  --paper:#EEF1EE; --sheet:#F9FBF8; --ink:#18242C; --muted:#56646A; --rule:#C3CEC8; --hair:#DFE6E1;
  --blue:#1E5BA6; --red:#BF1746; --zone:#F6E7EC; --gray:#8C999E;
  --pad:clamp(16px,4vw,56px);
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font-family:Archivo,system-ui,sans-serif;font-size:17px;line-height:1.55;font-variant-numeric:tabular-nums}
a{color:inherit;text-decoration-thickness:1px;text-underline-offset:3px}
@media (hover:hover){a:hover{color:var(--blue)}}
:focus-visible{outline:2px solid var(--blue);outline-offset:3px}
code{font-family:"IBM Plex Mono",monospace;font-size:.88em}
.wrap{max-width:1180px;margin:0 auto;padding:0 var(--pad)}
.top{padding:18px 0 0;margin:0;font-size:14px;color:var(--muted)}
h1{font-stretch:116%;font-weight:720;font-size:clamp(2rem,4.6vw,3.15rem);line-height:1.02;letter-spacing:-.018em;margin:16px 0 14px;max-width:21ch}
.lead{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 16px;margin:0 0 16px}
.lead p{margin:0;flex:1 1 440px;color:#2A3840;max-width:94ch}
.synth{display:inline-block;border:1.5px solid var(--ink);padding:2px 9px 3px;font-weight:600;font-size:15px;white-space:nowrap}

/* verdict and the two figures */
.verdict{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:14px 40px;align-items:end;border-top:2px solid var(--ink);padding-top:14px}
.vline{font-stretch:80%;font-weight:760;font-size:clamp(1.4rem,2.9vw,2.05rem);line-height:1.1;color:var(--red);margin:0}
.vline .code{font-family:"IBM Plex Mono",monospace;font-weight:500;font-size:.48em;color:var(--ink);border:1px solid var(--rule);padding:3px 7px;vertical-align:middle;margin-left:10px;white-space:nowrap;position:relative;top:-.1em}
.policy{margin:8px 0 0;color:var(--muted);font-size:15px;max-width:66ch}
.figs{display:grid;grid-template-columns:auto auto;gap:0}
.fig{padding:0 0 2px 26px;border-left:1px solid var(--rule)}
.fig+.fig{margin-left:26px}
.fig>span{display:block;font-size:14px;color:var(--muted)}
.fig strong{display:block;font-stretch:88%;font-weight:720;font-size:clamp(1.6rem,3.1vw,2.3rem);line-height:1.05;white-space:nowrap;letter-spacing:-.01em}
.fig strong .to{font-weight:400;color:var(--muted);margin:0 .18em}
.fig strong .unit{font-size:.55em;font-weight:600;color:var(--muted);margin-left:.15em}
.fig strong.red .after{color:var(--red)}

/* the strip chart */
.chart{margin:14px 0 0;background:var(--sheet);border:1px solid var(--rule);position:relative}
.chart svg{display:block;width:100%;height:auto;touch-action:manipulation}
.chart text{font-family:Archivo,system-ui,sans-serif;font-variant-numeric:tabular-nums}
.chart svg.hot{cursor:pointer}
.hitcol{fill:transparent;pointer-events:none}
.hitcol:focus{outline:none}
.hitcol:focus-visible{stroke:var(--ink);stroke-width:2}
.under{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:12px 24px;padding:12px 0 0}
.legend{display:flex;flex-wrap:wrap;gap:4px 20px;font-size:14px;color:var(--muted)}
.legend i{display:inline-block;width:10px;height:10px;margin-right:6px;vertical-align:-1px}
.actions{display:flex;gap:10px;flex-wrap:wrap}
button,.btn{font:inherit;font-size:15px;font-weight:600;cursor:pointer;border:1.5px solid var(--ink);background:var(--ink);color:#fff;padding:10px 16px;border-radius:2px;text-decoration:none;display:inline-block}
button.ghost,.btn.ghost{background:transparent;color:var(--ink)}
@media (hover:hover){button:hover,.btn:hover{background:var(--blue);border-color:var(--blue);color:#fff}}
button:disabled{opacity:.35;cursor:default}
@media (hover:hover){button:disabled:hover{background:transparent;border-color:var(--ink);color:var(--ink)}}
.detail{font-size:14.5px;color:var(--muted);margin:16px 0 0;max-width:96ch}
.detail p{margin:0 0 6px}

section.wrap{padding-top:84px}
h2{font-stretch:112%;font-weight:700;font-size:clamp(1.5rem,3vw,2.1rem);line-height:1.1;letter-spacing:-.01em;margin:0 0 20px;max-width:26ch}
.two{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,6fr);gap:40px;align-items:start}
.find{margin:0;padding:0;list-style:none;border-top:1px solid var(--rule)}
.find>li{display:grid;grid-template-columns:150px 1fr;gap:4px 18px;padding:12px 0;border-bottom:1px solid var(--rule)}
.find>li>b{font-weight:600;font-size:15px}
.find ul{margin:0;padding-left:18px}
.find ul li{margin:0 0 3px}
.muted{color:var(--muted)}
.small{font-size:14px}
.injected{border-left:3px solid var(--red);padding:2px 0 2px 14px;margin:0 0 22px;max-width:60ch}
.trace{background:var(--sheet);border:1px solid var(--rule);padding:18px 18px 12px}
.trace h3{font-size:16px;font-weight:650;margin:0 0 2px}
.trace svg{display:block;width:100%;height:auto;margin-top:8px}
.trace text{font-family:Archivo,system-ui,sans-serif;font-variant-numeric:tabular-nums}

/* control cases, then the local run */
.cases{display:grid;grid-template-columns:1fr 1fr;border-top:2px solid var(--ink)}
.cases>div{padding:18px 28px 18px 0}
.cases>div+div{padding-left:28px;border-left:1px solid var(--rule)}
.cases h3,.local h3{font-size:16px;font-weight:650;margin:0 0 10px}
.big{font-stretch:84%;font-weight:740;font-size:1.45rem;line-height:1.15;margin:0 0 8px}
.big.pass{color:var(--blue)}
.big.inc{color:var(--ink)}
.cases p{margin:0 0 6px;font-size:15.5px;max-width:52ch}
.cases p.big{font-size:1.45rem;margin-bottom:8px}
.local{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:18px 40px;border-top:1px solid var(--rule);padding-top:20px;margin-top:6px}
.local p{margin:0 0 10px;font-size:15.5px}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:right;padding:5px 7px;border-bottom:1px solid var(--hair);white-space:nowrap}
th:first-child,td:first-child{text-align:left;padding-left:0}
th{font-weight:600;color:var(--muted);vertical-align:bottom;white-space:normal;line-height:1.25}
.captab caption{caption-side:bottom;text-align:left;font-size:13px;color:var(--muted);padding-top:8px}
.captab td b{font-weight:650}
.short{display:none}
.stages{margin-top:6px;font-size:14px}
.stages td i{display:inline-block;width:9px;height:9px;margin-right:7px;vertical-align:0}
.stages tr.hl td{color:var(--red);font-weight:600}
.stages tr.hl td:first-child{color:var(--ink)}
.stages tr.tot td{border-top:1.5px solid var(--ink);font-weight:650;border-bottom:0}
details{border:1px solid var(--rule);background:var(--sheet)}
details summary{cursor:pointer;padding:11px 16px;font-weight:600;font-size:15px}
details .in{padding:0 16px 14px;font-size:14px;color:var(--muted)}
details .in p{margin:0 0 6px}
details pre{margin:0;padding:0 16px 16px;font-family:"IBM Plex Mono",monospace;font-size:12.5px;line-height:1.5;overflow-x:auto;white-space:pre}

.run{display:grid;grid-template-columns:minmax(0,1fr);gap:14px}
.cmd{background:#E3E9E4;border:1px solid var(--rule);padding:16px 18px;font-family:"IBM Plex Mono",monospace;font-size:14px;line-height:1.6;overflow-x:auto;white-space:pre;margin:0;color:var(--ink)}
.cmdbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.codes{display:flex;flex-wrap:wrap;gap:8px 28px;font-size:15px;margin:2px 0 0}
.codes code{font-size:14px;margin-right:6px}

footer{margin:84px 0 0;border-top:2px solid var(--ink);padding:22px 0 46px;display:flex;flex-wrap:wrap;gap:16px 40px;justify-content:space-between;align-items:center}
footer p{margin:0}

dialog{border:1px solid var(--ink);padding:0;max-width:min(940px,calc(100vw - 24px));width:100%;background:var(--paper);color:var(--ink)}
dialog::backdrop{background:rgba(24,36,44,.42)}
.dlg{padding:20px 22px 22px}
.dlghead{display:flex;justify-content:space-between;gap:16px;align-items:start}
.dlghead h2{margin:0 0 6px;font-size:1.4rem}
.picker{display:flex;flex-wrap:wrap;align-items:center;gap:8px 10px;margin:16px 0 18px;padding:12px 0;border-top:1px solid var(--rule);border-bottom:1px solid var(--rule)}
.picker select{font:inherit;font-size:15px;font-weight:600;padding:9px 10px;border:1.5px solid var(--ink);background:var(--sheet);color:var(--ink);border-radius:2px;min-width:0;flex:1 1 220px;max-width:340px}
.picker .pos{font-size:15px;color:var(--muted);margin-left:auto;white-space:nowrap}
.picker .pos b{color:var(--ink);font-weight:650}
.dlg .trace{border:0;padding:0;background:transparent}
.dlg .trace h3{font-size:1.15rem}

@media (max-width:900px){
  .verdict{grid-template-columns:minmax(0,1fr)}
  .figs{margin-top:4px}
  .fig:first-child{padding-left:0;border-left:0}
  .two,.local{grid-template-columns:minmax(0,1fr)}
}
@media (max-width:640px){
  body{font-size:16px}
  .fig+.fig{margin-left:18px}
  .fig{padding-left:18px}
  .fig strong{font-size:1.55rem}
  .find>li{grid-template-columns:1fr}
  .cases{grid-template-columns:minmax(0,1fr)}
  .cases>div+div{padding-left:0;border-left:0;border-top:1px solid var(--rule)}
  .long{display:none}
  .short{display:inline}
  th,td{padding:5px 4px}
  table{font-size:13px}
  .under{flex-direction:column;align-items:stretch}
  .actions button{flex:1 1 auto}
  .picker{display:grid;grid-template-columns:auto 1fr auto;gap:10px}
  .picker select{grid-column:1/-1;grid-row:2;max-width:none}
  #prev{grid-column:1;grid-row:1}
  .picker .pos{grid-column:2;grid-row:1;justify-self:center;margin:0}
  #next{grid-column:3;grid-row:1}
}
</style>
</head>
<body>
<div class="wrap">
  <p class="top">Inspired by <a href="{{POST}}">Bearworks' 420 ms example</a></p>

  <h1>A voice regression hidden by an unchanged median</h1>
  <div class="lead">
    <span class="synth">Synthetic example</span>
    <p>{{EXPECTED}} synthetic turns across {{SESSIONS}} simulated sessions, run through Pipecat's real latency observer with a scripted clock; no model runs. In the candidate, a slower simulated LLM adds {{DELTA}} to {{AFF_N}} turns; the median stays at {{C_MED}}.</p>
  </div>

  <div class="verdict">
    <div>
      <p class="vline" id="verdict">{{LABEL}}<span class="code">exit {{EXIT}}</span></p>
      <p class="policy">Demo policy: SDK-observed p95 &le; {{THR}}, Bearworks' illustrative budget. Caller-side delivery is not measured.</p>
    </div>
    <div class="figs" aria-label="Median and p95, baseline then candidate">
      <div class="fig"><span>Median, N = {{N_EACH}} per run</span><strong>{{B_MED}}<span class="to">&rarr;</span>{{C_MED}}</strong></div>
      <div class="fig"><span>p95, N = {{N_EACH}} per run</span><strong class="red">{{B_P95}}<span class="to">&rarr;</span><span class="after">{{C_P95}}</span></strong></div>
    </div>
  </div>

  <figure class="chart" id="chart" aria-label="{{CHART_LABEL}}" style="margin-bottom:0">
    <svg id="strip" role="img" aria-label="{{CHART_LABEL}}"></svg>
  </figure>
  <div class="under">
    <div class="legend">
      <span><i style="background:var(--blue)"></i>Baseline turn</span>
      <span><i style="background:var(--gray)"></i>Candidate turn within budget</span>
      <span><i style="background:var(--red)"></i>Candidate turn over {{THR}}, <span id="hint">point at one</span></span>
      <span>Axis skips 425 to 875 ms</span>
    </div>
    <div class="actions">
      <button type="button" id="inspect">Inspect turns over budget</button>
      <button type="button" class="ghost" id="runbtn">Reproduce locally</button>
    </div>
  </div>
  <div class="detail">
    <p>Mean {{B_MEAN}} &rarr; {{C_MEAN}}. {{SESSIONS}} sessions and {{EXPECTED}} expected user turns in each run, {{MEAS}} measured in each; the {{GREET}} greetings are kept apart. Turn total runs from the caller's silence, as Pipecat estimates it, to <code>BotStartedSpeakingFrame</code> in the bot.</p>
    <p>Illustrative budget from Bearworks' 14 Oct 2026 post. Applying it at p95 is our demo choice. <a href="{{POST}}">Read the post</a>.</p>
  </div>
</div>

<section class="wrap" id="changed">
  <h2>What changed, and what the report cannot say</h2>
  <div class="two">
    <div>
      <p class="injected"><b>The injected change.</b> {{KNOWN}} It is named here only because the scenario is synthetic. The report's diagnosis {{KNOWN_MATCH}} it.</p>
      <ul class="find">
        <li><b>Measure that changed</b><span>The {{CM_KEY}} contribution: {{CM_SHARE}} of the added time. Associated metric: {{ASSOC}}.</span></li>
        <li><b>Turns over budget</b><span>{{AFF_N}} turns over {{THR}}, in {{AFF_S}} of {{SESSIONS}} sessions: {{AFF_ALL}}.</span></li>
        <li><b>Compatible hypotheses</b><div><span class="muted small">{{HYP_NOTE}}</span><ul>{{HYPS}}</ul></div></li>
        <li><b>Missing to decide</b><ul>{{MISSING}}</ul></li>
      </ul>
    </div>
    <div class="trace" id="trace">
      <h3 id="trace-title">Turn {{FEAT}}: {{FEAT_B}} before, {{FEAT_C}} after</h3>
      <p class="muted small" style="margin:0">One measured turn, Pipecat's own contributions in the order they happened. Not a percentile.</p>
      <svg id="trace-svg" role="img" aria-label="Trace of one turn over budget, before and after"></svg>
      <table class="stages"></table>
    </div>
  </div>
</section>

<section class="wrap" id="controls">
  <h2>Control cases</h2>
  <div class="cases">
    <div>
      <h3>Remove the delay</h3>
      <p class="big pass">{{PASS_LABEL}}</p>
      <p>The candidate regenerated without the injected delay: p95 {{PASS_P95}} (N = {{PASS_N}}), which meets p95 &le; {{THR}}. Exit {{PASS_EXIT}}.</p>
    </div>
    <div>
      <h3>Delete the {{AFF_N}} slow turns from the log</h3>
      <p class="big inc">{{INC_LABEL}}</p>
      <p>What is left has a p95 of {{INC_P95}} (N = {{INC_N}}) and would pass. But {{INC_MISSING}} expected turns are missing, so the check does not conclude. Exit {{INC_EXIT}}.</p>
    </div>
  </div>
  <div class="local">
    <div>
      <h3>A real local run, as it came out</h3>
      <p>A full Pipecat 1.15.0 pipeline on one Mac, nothing in the cloud: Silero VAD, faster-whisper on CPU, {{CAP_LLM}} through Ollama, Kokoro for the voice. The caller is synthetic speech.</p>
      <p>{{CAP_SESS}} sessions, {{CAP_N}} turns, {{CAP_RESP}} responded: median {{CAP_MED}}, min {{CAP_MIN}}, max {{CAP_MAX}}, {{CAP_P95}}. The run checks the capture on a real pipeline, not its speed.</p>
      <details>
        <summary>Versions and machine</summary>
        <div class="in">
          <p>{{CAP_CPU}} with {{CAP_MEM}} GB, {{CAP_OS}}, no NVIDIA GPU.</p>
          <p>STT: {{CAP_STT}}, on CPU. LLM: {{CAP_LLM}} ({{CAP_QUANT}}, digest {{CAP_DIGEST}}) on Ollama {{CAP_OLLAMA}}. TTS: {{CAP_TTS}}.</p>
          <p>VAD stop_secs {{CAP_VAD}}; turn stop: {{CAP_TURN}}.</p>
          <p>Transport: {{CAP_TRANSPORT}}. Startup: {{CAP_STARTUP}}.</p>
          <p>{{CAP_NOAUDIO}} "completed with no audio" errors came from Kokoro in turns that still got a spoken reply; they are kept in the log and counted.</p>
          <p>Raw log, manifest, input audio and script: <a href="{{REPO}}/tree/master/capture">capture/ in the repository</a>.</p>
        </div>
      </details>
    </div>
    <div>
      <table class="captab">
        <thead><tr><th>Turn</th><th>Total</th>{{CAP_HEAD}}<th>Other</th></tr></thead>
        <tbody>{{CAP_ROWS}}</tbody>
        <caption>Seconds. Pipecat's contributions for each turn; "Other" holds endpointing wait, turn detection, output and pipeline time, so each row adds up to its measured total.</caption>
      </table>
    </div>
  </div>
</section>

<section class="wrap" id="run">
  <h2>Reproduce it locally</h2>
  <div class="run">
    <pre class="cmd" id="cmd">git clone {{REPO}}
cd voice-regression-check
uv sync
{{CMD}}</pre>
    <div class="cmdbar">
      <button type="button" class="ghost" id="copy">Copy commands</button>
      <a class="btn ghost" href="{{REPO}}">Open the repository</a>
    </div>
    <div class="codes">
      <span><code>0</code>Latency policy passed</span>
      <span><code>1</code>FAIL: configured latency threshold exceeded</span>
      <span><code>2</code>INCOMPLETE: input error, runs not comparable, missing turns, too few measured turns, or no policy</span>
    </div>
    <p class="small muted" style="margin:0;max-width:96ch">Log analysis runs offline: the command reads two JSONL logs, their manifests and a policy, and downloads nothing. The terminal output and this page are both generated from report.json. To use it on your own runs, add <code>capture/pipecat_jsonl_observer.py</code> to a Pipecat pipeline and mark sessions and turns from your scenario driver (see the README).</p>
    <details id="output"><summary>Terminal output of that command</summary><pre>{{TERMINAL}}</pre></details>
  </div>
</section>

<div class="wrap">
  <footer>
    <p>Built by Frederic de Lavenne de Choulot, The AI Pipe.</p>
    <a class="btn" href="{{CAL}}">Book 15 minutes</a>
  </footer>
</div>

<dialog id="dlg" aria-labelledby="dlg-title">
  <div class="dlg">
    <div class="dlghead">
      <div>
        <h2 id="dlg-title">Turns over budget</h2>
        <p class="muted small" style="margin:0">{{AFF_N}} candidate turns over {{THR}}, each paired with the same turn in the baseline.</p>
      </div>
      <button type="button" class="ghost" id="close">Close</button>
    </div>
    <div class="picker">
      <button type="button" class="ghost" id="prev">Previous</button>
      <label class="small muted" for="turnsel" style="position:absolute;left:-9999px">Turn</label>
      <select id="turnsel"></select>
      <button type="button" class="ghost" id="next">Next</button>
      <span class="pos" id="pos" aria-live="polite"></span>
    </div>
    <div class="trace">
      <h3 id="dlg-trace-title"></h3>
      <svg id="dlg-trace-svg" role="img" aria-label="Trace of the selected turn, before and after"></svg>
      <table class="stages"></table>
    </div>
  </div>
</dialog>

<script>
const D = {{DATA}};
const NS = 'http://www.w3.org/2000/svg';
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function el(tag, attrs, parent, text){ const e=document.createElementNS(NS,tag); for(const k in attrs) e.setAttribute(k,attrs[k]); if(text!=null) e.textContent=text; if(parent) parent.appendChild(e); return e; }
const byId = Object.fromEntries(D.affected.map(a => [a.id, a]));
const order = D.affected.map(a => a.id);
let reds = [];      // {id, x, y, s, v, node} for every turn over budget
let zone = null;    // the over-budget area of the After band

function strip(){
  const svg = document.getElementById('strip');
  const W = Math.max(300, svg.parentElement.clientWidth);
  const narrow = W < 640;
  const per = narrow ? 9 : 10;                 // squares per row inside a 10 ms column
  const left = narrow ? 6 : 124, right = narrow ? 6 : 26;
  const gap = narrow ? 16 : 44;                // the axis break
  const segW = (W - left - right - gap) / 2;
  const colW = segW / 5;                       // 5 slots per segment (375..425, 875..925)
  const step = Math.min(narrow ? 4.2 : 8.4, (colW - (narrow ? 4 : 16)) / per);
  const s = step - (narrow ? 0.6 : 1.4);
  const x = v => v < 650 ? left + (v - 375) / 50 * segW : left + segW + gap + (v - 875) / 50 * segW;
  svg.innerHTML = ''; reds = [];
  const counts = run => { const m={}; for(const [,v] of run){ if(v!=null) m[v]=(m[v]||0)+1; } return m; };
  const cb = counts(D.base), cc = counts(D.cand);
  const rowsFor = n => Math.ceil(n / per);
  const maxRows = Math.max(...Object.values(cb).map(rowsFor), ...Object.values(cc).map(rowsFor));
  const bandH = maxRows * step;
  const top1 = narrow ? 34 : 27, top2 = top1 + bandH + (narrow ? 30 : 42), axisY = top2 + bandH + 8;
  const H = axisY + (narrow ? 38 : 41);
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('height', H);
  const thrX = x(D.thr) + colW / 2;
  // the over-budget zone, behind everything
  el('rect',{x:thrX,y:top1-18,width:W-thrX,height:axisY-top1+18,fill:css('--zone')},svg);
  zone = {x0:thrX, x1:W, y0:top2-8, y1:axisY+24};
  // band baselines
  for(const t of [top1, top2]) el('line',{x1:left,x2:W-right,y1:t+bandH+0.5,y2:t+bandH+0.5,stroke:css('--hair')},svg);
  // labels
  const lab = (y, t1, t2) => {
    if(narrow){ el('text',{x:left,y:y-9,'font-size':12.5,'font-weight':650,fill:css('--ink')},svg,t1); return; }
    el('text',{x:0,y:y+bandH-22,'font-size':15,'font-weight':650,fill:css('--ink')},svg,t1);
    el('text',{x:0,y:y+bandH-5,'font-size':12.5,fill:css('--muted')},svg,t2); };
  lab(top1, 'Before', narrow ? 'baseline' : 'baseline config');
  lab(top2, 'After', narrow ? 'candidate' : 'candidate config');
  // squares, stacked by value from the band's baseline
  const place = (run, top, isCand) => {
    const fill = {};
    for(const [id,v] of run){
      if(v==null) continue;
      const i = fill[v] = (fill[v]||0); fill[v]++;
      const r = Math.floor(i / per), c = i % per;
      const cx = x(v) - (per*step)/2 + c*step, cy = top + bandH - (r+1)*step + (step - s);
      const over = isCand && v > D.thr;
      const q = el('rect',{x:cx.toFixed(2),y:cy.toFixed(2),width:s.toFixed(2),height:s.toFixed(2),
        fill: !isCand ? css('--blue') : over ? css('--red') : css('--gray')},svg);
      if(over){ reds.push({id, v, x:cx + s/2, y:cy + s/2, s, node:q}); }
    }
  };
  place(D.base, top1, false); place(D.cand, top2, true);
  // threshold
  el('line',{x1:thrX,x2:thrX,y1:top1-18,y2:axisY,stroke:css('--ink'),'stroke-width':1.5,'stroke-dasharray':'5 4'},svg);
  el('text',{x:thrX+7,y:top1-4,'font-size':narrow?11:13,'font-weight':650,fill:css('--ink')},svg,`budget, p95 ≤ ${D.thr} ms`);
  // medians
  const fs = narrow ? 11 : 13;
  const vmark = (xx, y1, y2, color, w) => el('line',{x1:xx,x2:xx,y1,y2,stroke:color,'stroke-width':w},svg);
  // narrow: the label sits right of the line, so the band title fits on its left
  const medLab = (v, top, txt) => el('text',{x:narrow?x(v)+5:x(v)-6,y:top-9,'text-anchor':narrow?'start':'end','font-size':fs,'font-weight':600,fill:css('--ink')},svg,txt);
  vmark(x(D.median[0]), top1-6, top1+bandH+3, css('--ink'), 2);
  medLab(D.median[0], top1, narrow ? 'median' : `median ${D.median[0]} ms`);
  vmark(x(D.median[1]), top2-6, top2+bandH+3, css('--ink'), 2);
  medLab(D.median[1], top2, narrow ? 'median' : `median ${D.median[1]} ms, unchanged`);
  // baseline p95, at the foot of the budget line
  el('text',{x:thrX+6,y:top1+bandH-4,'font-size':narrow?10.5:12.5,'font-weight':650,fill:css('--blue')},svg,`p95 ${D.p95[0]} ms`);
  // the red cluster: its label sits above, the p95 mark between label and squares
  const slowVals = Object.keys(cc).map(Number).filter(v => v > D.thr);
  const slowRows = Math.max(...slowVals.map(v => rowsFor(cc[v])));
  const clusterTop = top2 + bandH - slowRows*step;
  const cl = x(Math.min(...slowVals)) - (per*step)/2;
  const nLate = reds.length;
  const nEarly = D.base.filter(r => r[1] != null && r[1] > D.thr).length;
  const big = (y, color, txt) => el('text',{x:cl,y,'font-size':narrow?15:26,'font-weight':760,'font-stretch':'88%',fill:color},svg,txt);
  const sub = (y, color, txt) => el('text',{x:cl,y,'font-size':narrow?11:14,'font-weight':600,fill:color},svg,txt);
  big(clusterTop-(narrow?30:58), css('--red'), `${nLate} turns`);
  sub(clusterTop-(narrow?17:37), css('--red'), narrow ? `each ${D.delta} ms slower` : `each ${D.delta} ms slower than before`);
  // the same place in the Before band: what was over budget there
  big(top1+bandH-(narrow?36:58), css('--muted'), `${nEarly} turns`);
  sub(top1+bandH-(narrow?23:37), css('--muted'), 'over budget before');
  const p95x = x(D.p95[1]);
  vmark(p95x, clusterTop-(narrow?9:20), top2+bandH+3, css('--red'), 2);
  el('text',{x:p95x+5,y:clusterTop-(narrow?3:9),'font-size':narrow?10.5:12.5,'font-weight':650,fill:css('--red')},svg,`p95 ${D.p95[1]} ms`);
  // readout for the pointed turn
  const ro = el('text',{id:'readout',x:W-right,y:narrow?14:20,'text-anchor':'end','font-size':narrow?11.5:13.5,'font-weight':650,fill:css('--ink')},svg,'');
  // axis with a break
  const ax = (a,b) => el('line',{x1:x(a),x2:x(b),y1:axisY,y2:axisY,stroke:css('--ink'),'stroke-width':1},svg);
  ax(375,425); ax(875,925);
  const bx = left + segW + gap/2;
  for(const d of [-4,3]) el('line',{x1:bx+d-3,x2:bx+d+3,y1:axisY+6,y2:axisY-6,stroke:css('--ink'),'stroke-width':1.2},svg);
  for(const v of [380,390,400,410,420,880,890,900,910,920]){
    el('line',{x1:x(v),x2:x(v),y1:axisY,y2:axisY+5,stroke:css('--ink')},svg);
    if(!narrow || v % 20 === 0) el('text',{x:x(v),y:axisY+(narrow?17:19),'text-anchor':'middle','font-size':narrow?10.5:12.5,fill:css('--muted')},svg,`${v}`);
  }
  el('text',{x:narrow?left:0,y:axisY+(narrow?31:36),'text-anchor':'start','font-size':narrow?10.5:12.5,fill:css('--muted')},svg,'turn total, ms');
  // one keyboard stop per slow value
  for(const v of slowVals.sort((a,b)=>a-b)){
    const first = reds.find(r => r.v === v);
    const h = el('rect',{class:'hitcol',x:x(v)-(per*step)/2-3,y:top2+bandH-rowsFor(cc[v])*step-3,width:per*step+4,height:rowsFor(cc[v])*step+4,tabindex:0,role:'button',
      'aria-label':`${cc[v]} turns at ${v} ms, over budget: inspect them`},svg);
    h.addEventListener('keydown', e => { if(e.key==='Enter'||e.key===' '){ e.preventDefault(); openDlg(first.id); }});
  }
  wire(svg, ro, narrow);
}

let hot = null;
function nearest(px, py){
  let best = null, bd = Infinity;
  for(const r of reds){ const d = Math.hypot(r.x - px, r.y - py); if(d < bd){ bd = d; best = r; } }
  return [best, bd];
}
function wire(svg, ro, narrow){
  const pt = e => { const b = svg.getBoundingClientRect(); const k = svg.viewBox.baseVal.width / b.width; return [(e.clientX - b.left) * k, (e.clientY - b.top) * k]; };
  const inZone = (px, py) => zone && px >= zone.x0 && px <= zone.x1 && py >= zone.y0 && py <= zone.y1;
  const show = r => {
    if(hot && hot !== r){ hot.node.removeAttribute('stroke'); }
    hot = r;
    if(r){ r.node.setAttribute('stroke', css('--ink')); r.node.setAttribute('stroke-width', narrow ? 1 : 1.6);
      const a = byId[r.id]; ro.textContent = `${r.id}: ${a.b} ms before, ${a.c} ms after`; svg.classList.add('hot'); }
    else { ro.textContent = ''; svg.classList.remove('hot'); }
  };
  svg.addEventListener('pointermove', e => {
    if(e.pointerType !== 'mouse') return;
    const [px, py] = pt(e); const [r, d] = nearest(px, py);
    show(d <= Math.max(14, r.s * 2) ? r : null);
  });
  svg.addEventListener('pointerleave', () => show(null));
  svg.addEventListener('click', e => {
    const [px, py] = pt(e); const [r, d] = nearest(px, py);
    if(d <= Math.max(14, r.s * 2) || inZone(px, py)) openDlg(r.id);
  });
}

const SEG = {endpointing_wait:'endpointing', transcription:'STT', turn_detection:'turn', llm_inference:'LLM inference', speech_synthesis:'speech synthesis', output_transport:'output', pipeline:'pipeline', sentence_aggregation:'aggregation'};
const GRAYS = ['#8A989D','#AEB9BC','#6F7C81','#9AA6AA','#7D8A8F','#B8C2C4'];
const segColor = (k, after) => k === 'llm_inference' ? (after ? css('--red') : css('--blue')) : GRAYS[Object.keys(SEG).indexOf(k) % 6];
function trace(svg, a){
  svg.innerHTML = '';
  const W = Math.max(280, svg.parentElement.clientWidth - 2);
  const left = W < 520 ? 52 : 68, right = 12, maxMs = Math.ceil(Math.max(a.b, a.c) / 100) * 100;
  const sx = v => left + v / maxMs * (W - left - right);
  const rows = [['Before', a.tr.baseline, a.b], ['After', a.tr.candidate, a.c]];
  const H = 196; svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  rows.forEach(([name, segs, total], i) => {
    const y = 18 + i * 78;
    el('text',{x:0,y:y+15,'font-size':13,'font-weight':650,fill:css('--ink')},svg,name);
    el('text',{x:0,y:y+31,'font-size':12,fill:css('--muted')},svg,`${total} ms`);
    for(const [key,label,start,dur] of segs){
      if(start==null) continue;
      const x0 = sx(start), w = Math.max(1, sx(start+dur) - x0);
      el('rect',{x:x0,y:y,width:w,height:24,fill:segColor(key, i)},svg);
      const tx = SEG[key]||label;
      if(w > tx.length * 6.4 + 10) el('text',{x:x0+5,y:y+16,'font-size':11.5,fill:'#fff','font-weight':600},svg,tx);
      if(key === 'llm_inference') el('text',{x:x0+2,y:y+40,'font-size':11.5,fill:i?css('--red'):css('--blue'),'font-weight':650},svg,`${dur} ms`);
    }
  });
  const tb = svg.nextElementSibling;
  if(tb && tb.tagName === 'TABLE'){
    const before = Object.fromEntries(a.tr.baseline.map(r => [r[0], r])), after = Object.fromEntries(a.tr.candidate.map(r => [r[0], r]));
    const keys = [...new Set([...a.tr.baseline.map(r=>r[0]), ...a.tr.candidate.map(r=>r[0])])];
    tb.innerHTML = '<thead><tr><th>Stage, as Pipecat names it</th><th>Before</th><th>After</th></tr></thead><tbody>' +
      keys.map(k => `<tr${k==='llm_inference'?' class="hl"':''}><td><i style="background:${segColor(k, true)}"></i>${(before[k]||after[k])[1]}</td><td>${before[k]?before[k][3]+' ms':'absent'}</td><td>${after[k]?after[k][3]+' ms':'absent'}</td></tr>`).join('') +
      `<tr class="tot"><td>Turn total, measured</td><td>${a.b} ms</td><td>${a.c} ms</td></tr></tbody>`;
  }
  const ay = 182;
  el('line',{x1:sx(0),x2:sx(maxMs),y1:ay-12,y2:ay-12,stroke:css('--rule')},svg);
  for(let v=0; v<=maxMs; v+= maxMs > 600 ? 200 : 100){
    el('text',{x:sx(v),y:ay,'text-anchor':v===0?'start':'middle','font-size':11,fill:css('--muted')},svg,v===0?'0 ms':`${v}`);
  }
}

const dlg = document.getElementById('dlg');
const sel = document.getElementById('turnsel');
for(const a of D.affected){ const o = document.createElement('option'); o.value = a.id; o.textContent = `${a.id}, ${a.b} to ${a.c} ms`; sel.appendChild(o); }
let cur = 0;
function select(i){
  cur = Math.max(0, Math.min(order.length - 1, i));
  const id = order[cur], a = byId[id];
  sel.value = id;
  document.getElementById('pos').innerHTML = `<b>${cur + 1}</b> / ${order.length}`;
  document.getElementById('prev').disabled = cur === 0;
  document.getElementById('next').disabled = cur === order.length - 1;
  document.getElementById('dlg-trace-title').textContent = `Turn ${id}: ${a.b} ms before, ${a.c} ms after`;
  trace(document.getElementById('dlg-trace-svg'), a);
}
function openDlg(id){
  if(!dlg.open){ if(typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open',''); }
  select(order.indexOf(id in byId ? id : D.featured));
}
sel.addEventListener('change', () => select(order.indexOf(sel.value)));
document.getElementById('prev').addEventListener('click', () => select(cur - 1));
document.getElementById('next').addEventListener('click', () => select(cur + 1));
dlg.addEventListener('keydown', e => {
  if(e.target === sel) return;
  if(e.key === 'ArrowLeft'){ e.preventDefault(); select(cur - 1); }
  if(e.key === 'ArrowRight'){ e.preventDefault(); select(cur + 1); }
});
document.getElementById('inspect').addEventListener('click', () => openDlg(D.featured));
document.getElementById('close').addEventListener('click', () => dlg.close());
dlg.addEventListener('click', e => { if(e.target === dlg) dlg.close(); });
document.getElementById('runbtn').addEventListener('click', () => {
  document.getElementById('output').open = true;
  document.getElementById('run').scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
  document.getElementById('copy').focus({preventScroll:true});
});
document.getElementById('copy').addEventListener('click', async e => {
  const t = document.getElementById('cmd').textContent;
  try { await navigator.clipboard.writeText(t); e.target.textContent = 'Copied'; }
  catch { const r=document.createRange(); r.selectNodeContents(document.getElementById('cmd')); const s=getSelection(); s.removeAllRanges(); s.addRange(r); e.target.textContent = 'Selected, press Ctrl+C'; }
  setTimeout(() => e.target.textContent = 'Copy commands', 2400);
});
if(matchMedia('(hover: none)').matches) document.getElementById('hint').textContent = 'tap the red group';
strip(); trace(document.getElementById('trace-svg'), byId[D.featured]);
let rt, lastW = innerWidth; addEventListener('resize', () => { if(innerWidth === lastW) return; lastW = innerWidth; clearTimeout(rt); rt = setTimeout(() => { strip(); trace(document.getElementById('trace-svg'), byId[D.featured]); if(dlg.open) select(cur); }, 120); });
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
