"""Render site/index.html from the reports in reports/ (run build_reports.py first).

Every number on the page is read from a report.json written by the CLI.
"""

import html
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from voice_check.render_text import render  # noqa: E402

REPO = "https://github.com/fred1433/voice-regression-check"
POST = "https://www.bearworks.com/post/sub-500ms-voice-agents-stt-llm-tts-latency"
CAL = "https://cal.theaipipe.com"
FAVICON = ROOT / "site" / "favicon.svg"  # The AI Pipe favicon, already in site/


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


def main():
    main_r, pass_r, inc_r, cap_r = (load(n) for n in ("synthetic-compare", "synthetic-delay-removed", "synthetic-slow-traces-removed", "local-capture"))
    assert main_r["verdict"] == "FAIL" and pass_r["verdict"] == "PASS" and inc_r["verdict"] == "INCOMPLETE"

    b, c = main_r["runs"]["baseline"], main_r["runs"]["candidate"]
    ch = main_r["change"]
    thr = main_r["policy"]["rule"]["threshold_ms"]
    featured = next(r for r in ch["affected_turns"] if r["baseline_total_ms"] == b["total"]["median_ms"])
    affected = [
        {
            "id": f"{r['session_id']}/{r['turn_id']}",
            "s": r["session_id"],
            "t": r["turn_id"],
            "b": r["baseline_total_ms"],
            "c": r["candidate_total_ms"],
            "tr": {k: [[x["key"], x["label"], x["start_ms"], x["duration_ms"]] for x in v] for k, v in r["trace"].items()},
        }
        for r in ch["affected_turns"]
    ]
    data = {
        "thr": thr,
        "base": [[s, t, v] for s, t, v, _ in main_r["turns"]["baseline"]],
        "cand": [[s, t, v] for s, t, v, _ in main_r["turns"]["candidate"]],
        "affected": affected,
        "featured": f"{featured['session_id']}/{featured['turn_id']}",
        "median": [b["total"]["median_ms"], c["total"]["median_ms"]],
        "p95": [b["total"]["p95_ms"], c["total"]["p95_ms"]],
    }

    cm = ch["changed_measures"][0]
    facts = ch["facts"]
    kc = ch["known_cause"]
    inc_c = inc_r["runs"]["candidate"]
    cap = cap_r["run"]
    cap_t = cap["total"]
    machine = cap["conditions"]["machine"]
    capcfg = cap["config"]
    terminal = render(main_r)

    rows = []
    for row in cap_r["turn_table"]:
        cms = row.get("contributions_ms") or {}
        rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                esc(f"{row['session_id']}/{row['turn_id']}"),
                secs(row["total_ms"]) if row["total_ms"] is not None else esc(row["status"]),
                secs(cms["transcription"]) if "transcription" in cms else "absent",
                secs(cms["llm_inference"]) if "llm_inference" in cms else "absent",
                secs(cms["speech_synthesis"]) if "speech_synthesis" in cms else "absent",
                esc(row["status"].replace("_", " ")),
            )
        )
    llm_name = capcfg["ollama"]["model"]
    digest = (capcfg["ollama"].get("digest") or "")[:12]
    noaudio = cap["counts"]["no_audio_errors_in_responded_turns"]

    hyps = "".join(f"<li>{esc(h)}</li>" for h in ch["hypotheses_compatible"])
    missing = "".join(f"<li>{esc(m)}</li>" for m in ch["missing_to_decide"])
    cmd = "uv run voice-check compare fixtures/baseline.jsonl fixtures/candidate.jsonl --policy policies/published-example-420.yaml --report report.json"

    page = TEMPLATE
    subs = {
        "DATA": json.dumps(data, separators=(",", ":")),
        "LABEL": esc(main_r["label"]),
        "EXIT": str(main_r["exit_code"]),
        "THR": ms(thr),
        "B_MED": ms(b["total"]["median_ms"]),
        "C_MED": ms(c["total"]["median_ms"]),
        "B_P95": ms(b["total"]["p95_ms"]),
        "C_P95": ms(c["total"]["p95_ms"]),
        "B_N": f"{b['total']['n']:,}",
        "C_N": f"{c['total']['n']:,}",
        "SESSIONS": f"{c['counts']['sessions_expected']:,}",
        "EXPECTED": f"{c['counts']['turns_expected']:,}",
        "B_MEAS": f"{b['counts']['turns_measured']:,}",
        "C_MEAS": f"{c['counts']['turns_measured']:,}",
        "GREET": f"{c['counts']['greetings_kept_apart']:,}",
        "B_MEAN": ms(b["total"]["mean_ms_measured_only"]),
        "C_MEAN": ms(c["total"]["mean_ms_measured_only"]),
        "AFF_N": f"{facts['affected_turns']:,}",
        "AFF_S": f"{facts['affected_sessions']:,}",
        "AFF_ALL": "every turn of those sessions, and no turn elsewhere" if facts["every_turn_of_those_sessions_affected"] else "spread across sessions",
        "CM_KEY": esc(next(x["label"] for x in c["contributions"] if x["key"] == cm["contribution_key"])),
        "CM_SHARE": f"{cm['share_of_total_delta']:.0%}",
        "ASSOC": esc(", ".join(f"{a['metric'].upper()} of {a['processor']}" for a in ch["associated_metrics"])),
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
        "CAP_SESS": f"{cap['counts']['sessions_expected']:,}",
        "CAP_MED": secs(cap_t["median_ms"]),
        "CAP_MIN": secs(cap_t["min_ms"]),
        "CAP_MAX": secs(cap_t["max_ms"]),
        "CAP_P95": esc(cap_t["p95_note"]),
        "CAP_ROWS": "".join(rows),
        "CAP_CPU": esc(machine["cpu"]),
        "CAP_MEM": str(machine["memory_gb"]),
        "CAP_OS": esc(f"macOS {machine['macos']}"),
        "CAP_LLM": esc(llm_name),
        "CAP_DIGEST": esc(digest),
        "CAP_OLLAMA": esc(capcfg["ollama"]["ollama_version"]),
        "CAP_WHISPER": esc(capcfg["stt"].split(", model ")[-1]),
        "CAP_NOAUDIO": str(noaudio),
        "TERMINAL": esc(terminal),
        "CMD": esc(cmd),
        "REPO": REPO,
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
<meta name="description" content="Two runs of the same Pipecat scenarios: the median stays at 400 ms, the p95 goes to 900 ms, and the check fails with the affected turns.">
<link rel="icon" href="favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,300..800&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{
  --paper:#EEF1EE; --sheet:#F8FAF7; --ink:#18242C; --muted:#56646A; --rule:#C3CEC8; --grid:#DCE3DE;
  --blue:#1E5BA6; --blue-soft:#BBD0EA; --red:#BF1746; --red-soft:#F3C7D3; --seg:#8A989D;
  --pad:clamp(16px,4vw,56px);
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font-family:Archivo,system-ui,sans-serif;font-size:17px;line-height:1.55;font-variant-numeric:tabular-nums}
a{color:inherit;text-decoration-thickness:1px;text-underline-offset:3px}
a:hover{color:var(--blue)}
:focus-visible{outline:2px solid var(--blue);outline-offset:3px}
.wrap{max-width:1180px;margin:0 auto;padding:0 var(--pad)}
.top{display:flex;justify-content:space-between;gap:16px;padding:22px 0 0;font-size:14px;color:var(--muted)}
.top b{font-weight:600;color:var(--ink)}
h1{font-stretch:116%;font-weight:720;font-size:clamp(2rem,4.6vw,3.35rem);line-height:1.02;letter-spacing:-.018em;margin:26px 0 14px;max-width:21ch}
.lead{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 16px;max-width:940px;margin:0 0 18px}
.lead p{margin:0;flex:1 1 420px;color:#2A3840}
.synth{display:inline-block;border:1.5px solid var(--ink);padding:2px 9px 3px;font-weight:600;font-size:15px;white-space:nowrap}
.verdict{display:grid;grid-template-columns:1fr auto;gap:12px 32px;align-items:end;border-top:2px solid var(--ink);padding-top:12px}
.vline{font-stretch:80%;font-weight:760;font-size:clamp(1.4rem,2.9vw,2.05rem);line-height:1.1;color:var(--red);margin:0}
.vline .code{font-family:"IBM Plex Mono",monospace;font-weight:500;font-size:.5em;color:var(--ink);border:1px solid var(--rule);padding:3px 7px;vertical-align:.45em;margin-left:10px;white-space:nowrap}
.vsub{margin:6px 0 0;color:var(--muted);font-size:15px;max-width:70ch}
.actions{display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end}
button,.btn{font:inherit;font-size:15px;font-weight:600;cursor:pointer;border:1.5px solid var(--ink);background:var(--ink);color:#fff;padding:10px 16px;border-radius:2px;text-decoration:none;display:inline-block}
button.ghost,.btn.ghost{background:transparent;color:var(--ink)}
button:hover,.btn:hover{background:var(--blue);border-color:var(--blue);color:#fff}

/* the strip chart */
.chart{margin:16px 0 0;background:var(--sheet);border:1px solid var(--rule);position:relative;
  background-image:linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:100% 12px,12px 100%;}
.chart svg{display:block;width:100%;height:auto}
.chart text{font-family:Archivo,system-ui,sans-serif;font-variant-numeric:tabular-nums}
.sq{cursor:default}
.sq.hit{cursor:pointer}
.sq.hit:hover{stroke:var(--ink);stroke-width:1.2}
.legend{display:flex;flex-wrap:wrap;gap:6px 22px;font-size:14px;color:var(--muted);padding:12px 0 0}
.legend i{display:inline-block;width:10px;height:10px;margin-right:6px;vertical-align:-1px}
.facts{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:0;border-top:1px solid var(--rule);border-bottom:1px solid var(--rule);margin:18px 0 0}
.facts div{padding:12px 16px 12px 0}
.facts div+div{padding-left:16px;border-left:1px solid var(--rule)}
.facts strong{display:block;font-stretch:110%;font-size:1.5rem;font-weight:680;line-height:1.15}
.facts span{font-size:14px;color:var(--muted)}
.notes{display:grid;grid-template-columns:1fr 1fr;gap:12px 40px;font-size:14px;color:var(--muted);margin:14px 0 0}
.notes p{margin:0}

section.wrap{padding-top:80px}
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

.controls{display:grid;grid-template-columns:1fr 1fr 1.35fr;border-top:2px solid var(--ink)}
.controls>div{padding:18px 22px 6px 0}
.controls>div+div{padding-left:22px;border-left:1px solid var(--rule)}
.controls h3{font-size:16px;font-weight:650;margin:0 0 10px}
.big{font-stretch:84%;font-weight:740;font-size:1.45rem;line-height:1.15;margin:0 0 8px}
.big.pass{color:var(--blue)}
.big.inc{color:var(--ink)}
.controls p{margin:0 0 10px;font-size:15px}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:right;padding:4px 6px;border-bottom:1px solid var(--grid);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{font-weight:600;color:var(--muted)}
.scroll td:last-child,.scroll th:last-child{text-align:left}
.scroll{overflow-x:auto;margin:0 0 10px}
.stages{margin-top:6px;font-size:14px}
.stages td i{display:inline-block;width:9px;height:9px;margin-right:7px;vertical-align:0}
.stages tr.hl td{color:var(--red);font-weight:600}
.stages tr.hl td:first-child{color:var(--ink)}
.stages tr.tot td{border-top:1.5px solid var(--ink);font-weight:650;border-bottom:0}

.run{display:grid;grid-template-columns:minmax(0,1fr);gap:14px}
.cmd{background:#E3E9E4;border:1px solid var(--rule);padding:16px 18px;font-family:"IBM Plex Mono",monospace;font-size:14px;line-height:1.6;overflow-x:auto;white-space:pre;margin:0;color:var(--ink)}
.cmdbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
details{border:1px solid var(--rule);background:var(--sheet)}
details summary{cursor:pointer;padding:12px 16px;font-weight:600}
details pre{margin:0;padding:0 16px 16px;font-family:"IBM Plex Mono",monospace;font-size:12.5px;line-height:1.5;overflow-x:auto;white-space:pre}
.codes{display:flex;flex-wrap:wrap;gap:8px 28px;font-size:15px;margin:2px 0 0}
.codes code{font-family:"IBM Plex Mono",monospace;font-size:14px;margin-right:6px}

footer{margin:84px 0 0;border-top:2px solid var(--ink);padding:22px 0 46px;display:flex;flex-wrap:wrap;gap:16px 40px;justify-content:space-between;align-items:center}
footer p{margin:0}

dialog{border:1px solid var(--ink);padding:0;max-width:min(980px,calc(100vw - 24px));width:100%;background:var(--paper);color:var(--ink)}
dialog::backdrop{background:rgba(24,36,44,.42)}
.dlg{padding:20px 22px 22px}
.dlghead{display:flex;justify-content:space-between;gap:16px;align-items:start}
.dlghead h2{margin:0 0 6px;font-size:1.4rem}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(84px,1fr));gap:6px;margin:14px 0 16px;max-height:190px;overflow:auto;padding:2px}
.grid button{font-size:13px;font-weight:500;padding:5px 6px;background:var(--sheet);color:var(--ink);border:1px solid var(--rule)}
.grid button[aria-pressed=true]{background:var(--red);border-color:var(--red);color:#fff}
.grid button:hover{border-color:var(--ink);background:var(--sheet);color:var(--ink)}
.grid button[aria-pressed=true]:hover{background:var(--red);color:#fff}

@media (max-width:900px){
  .verdict{grid-template-columns:1fr}
  .actions{justify-content:flex-start}
  .two{grid-template-columns:minmax(0,1fr)}
  .controls{grid-template-columns:minmax(0,1fr)}
  .controls>div+div{padding-left:0;border-left:0;border-top:1px solid var(--rule)}
  .notes{grid-template-columns:1fr}
}
@media (max-width:600px){
  body{font-size:16px}
  .facts{grid-template-columns:1fr 1fr}
  .facts div:nth-child(3){padding-left:0;border-left:0}
  .facts div:nth-child(n+3){border-top:1px solid var(--rule)}
  .find>li{grid-template-columns:1fr}
  .top{flex-direction:column;gap:2px}
}
@media (prefers-reduced-motion:no-preference){
  .sq.late{animation:drop .5s ease-out both;animation-delay:calc(var(--i) * 6ms + 300ms)}
  @keyframes drop{from{opacity:0;transform:translateX(-14px)}to{opacity:1;transform:none}}
}
</style>
</head>
<body>
<div class="wrap">
  <div class="top"><span>Prepared for Bearworks</span><span><b>The AI Pipe</b>, voice agent reliability</span></div>

  <h1>A voice regression hidden by an unchanged median</h1>
  <div class="lead">
    <span class="synth">Synthetic example</span>
    <p>The same 200 scripted calls, timed by Pipecat's own latency observer, once with a baseline configuration and once with a candidate. The median turn stays at {{C_MED}}. A hundred turns got slower, and the check says which.</p>
  </div>

  <div class="verdict">
    <div>
      <p class="vline" id="verdict">{{LABEL}}<span class="code">exit {{EXIT}}</span></p>
      <p class="vsub">Candidate p95 {{C_P95}} (N = {{C_N}}) against the rule p95 &le; {{THR}}. Baseline p95 {{B_P95}} (N = {{B_N}}). A latency check only: it says nothing about conversation quality.</p>
    </div>
    <div class="actions">
      <button type="button" id="inspect">Inspect affected turns</button>
      <button type="button" class="ghost" id="runbtn">Run this example</button>
    </div>
  </div>

  <figure class="chart" id="chart" aria-label="Each square is one user turn. Baseline: 1,000 turns between 380 and 420 ms. Candidate: 900 turns between 380 and 420 ms and 100 turns between 880 and 920 ms.">
    <svg id="strip" role="img"></svg>
  </figure>
  <div class="legend">
    <span><i style="background:var(--blue)"></i>Baseline turn</span>
    <span><i style="background:#56646A"></i>Candidate turn within the threshold</span>
    <span><i style="background:var(--red)"></i>Candidate turn over {{THR}} (click one)</span>
    <span>The axis skips 425 to 875 ms.</span>
  </div>

  <div class="facts">
    <div><strong>{{SESSIONS}}</strong><span>sessions in each run</span></div>
    <div><strong>{{EXPECTED}}</strong><span>user turns expected per run</span></div>
    <div><strong>{{B_MEAS}} / {{C_MEAS}}</strong><span>turns measured, baseline / candidate</span></div>
    <div><strong>{{B_MEAN}} / {{C_MEAN}}</strong><span>mean, baseline / candidate</span></div>
  </div>
  <div class="notes">
    <p>Illustrative budget from Bearworks' 14 Oct 2026 post. Applying it at p95 is our demo choice. <a href="{{POST}}">Read the post</a>.</p>
    <p>Scope: SDK-observed, from the caller's silence as Pipecat estimates it to <code>BotStartedSpeakingFrame</code> in the bot. Audio delivery and playback on the caller's side: not measured. The {{GREET}} greetings are kept apart.</p>
  </div>
</div>

<section class="wrap" id="changed">
  <h2>What changed, and what the report can and cannot say</h2>
  <div class="two">
    <div>
      <p class="injected"><b>The injected change.</b> {{KNOWN}} It is named here only because the scenario is synthetic. The report's diagnosis {{KNOWN_MATCH}} it.</p>
      <ul class="find">
        <li><b>Measure that changed</b><span>The {{CM_KEY}} contribution: {{CM_SHARE}} of the added time. Associated metric: {{ASSOC}}.</span></li>
        <li><b>Turns concerned</b><span>{{AFF_N}} turns over {{THR}}, in {{AFF_S}} of {{SESSIONS}} sessions: {{AFF_ALL}}.</span></li>
        <li><b>Compatible hypotheses</b><div><span class="muted small">{{HYP_NOTE}}</span><ul>{{HYPS}}</ul></div></li>
        <li><b>Missing to decide</b><ul>{{MISSING}}</ul></li>
      </ul>
    </div>
    <div class="trace" id="trace">
      <h3 id="trace-title">Turn {{FEAT}}: {{FEAT_B}} before, {{FEAT_C}} after</h3>
      <p class="muted small" style="margin:0">One measured turn, Pipecat's own contributions in the order they happened. Not a percentile.</p>
      <svg id="trace-svg" role="img" aria-label="Trace of one affected turn, before and after"></svg>
      <table class="stages"></table>
    </div>
  </div>
</section>

<section class="wrap" id="controls">
  <h2>The same check, when the answer is yes, and when there is no answer</h2>
  <div class="controls">
    <div>
      <h3>Remove the delay</h3>
      <p class="big pass">{{PASS_LABEL}}</p>
      <p>The candidate regenerated without the injected delay: p95 {{PASS_P95}} (N = {{PASS_N}}), which meets p95 &le; {{THR}}. Exit {{PASS_EXIT}}.</p>
    </div>
    <div>
      <h3>Delete the 100 slow turns from the log</h3>
      <p class="big inc">{{INC_LABEL}}</p>
      <p>What is left has a p95 of {{INC_P95}} (N = {{INC_N}}) and would pass. But {{INC_MISSING}} expected turns are missing, so the check does not conclude. Exit {{INC_EXIT}}.</p>
    </div>
    <div>
      <h3>A real local run, as it came out</h3>
      <p>Pipecat 1.15.0 on one {{CAP_CPU}} with {{CAP_MEM}} GB ({{CAP_OS}}, no NVIDIA GPU): Silero VAD, faster-whisper {{CAP_WHISPER}} on CPU, {{CAP_LLM}} through Ollama {{CAP_OLLAMA}}, Kokoro for the voice. The caller is synthetic speech. {{CAP_SESS}} sessions, {{CAP_N}} turns: median {{CAP_MED}}, min {{CAP_MIN}}, max {{CAP_MAX}}, {{CAP_P95}}. Nowhere near 420 ms, and not meant to be: it is a laptop.</p>
      <div class="scroll"><table>
        <thead><tr><th>Turn</th><th>Total</th><th>Transcription</th><th>LLM</th><th>Speech</th><th>Status</th></tr></thead>
        <tbody>{{CAP_ROWS}}</tbody>
      </table></div>
      <p class="small muted">{{CAP_NOAUDIO}} "completed with no audio" errors came from Kokoro in turns that still got a spoken reply; they are kept in the log and counted. Raw log, manifest and script are in the repository.</p>
    </div>
  </div>
</section>

<section class="wrap" id="run">
  <h2>Run it</h2>
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
      <span><code>2</code>INCOMPLETE: input error, runs not comparable, missing turns, too few turns, or no policy</span>
    </div>
    <p class="small muted" style="margin:0">Log analysis runs offline: the command reads two JSONL logs, their manifests and a policy, and downloads nothing. The terminal output and this page are both generated from report.json. To use it on your own runs, add <code>capture/pipecat_jsonl_observer.py</code> to a Pipecat pipeline: it writes one envelope line per Pipecat event.</p>
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
        <h2 id="dlg-title">Affected turns</h2>
        <p class="muted small" style="margin:0">{{AFF_N}} candidate turns over {{THR}}, each paired with the same turn in the baseline.</p>
      </div>
      <button type="button" class="ghost" id="close">Close</button>
    </div>
    <div class="grid" id="turns"></div>
    <div class="trace" style="border:0;padding:0;background:transparent">
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

function strip(){
  const svg = document.getElementById('strip');
  const W = Math.max(320, svg.parentElement.clientWidth);
  const narrow = W < 640;
  const per = narrow ? 5 : 12;              // squares per row inside a 10 ms column
  const left = narrow ? 8 : 112, right = narrow ? 8 : 14;
  const gap = narrow ? 18 : 34;             // the axis break
  const segW = (W - left - right - gap) / 2;
  const vals = [380,390,400,410,420];
  const colW = segW / 5;                    // 5 slots per segment (375..425)
  const s = Math.max(2.2, Math.min(7, (colW - 8) / per - 1));
  const step = s + 1;
  const x = v => v < 650 ? left + (v - 375) / 50 * segW : left + segW + gap + (v - 875) / 50 * segW;
  svg.innerHTML = '';
  const rowsFor = n => Math.ceil(n / per);
  const counts = run => { const m={}; for(const [,,v] of run){ if(v!=null) m[v]=(m[v]||0)+1; } return m; };
  const cb = counts(D.base), cc = counts(D.cand);
  const maxRows = Math.max(...Object.values(cb).map(rowsFor), ...Object.values(cc).map(rowsFor));
  const bandH = maxRows * step;
  const lift = narrow ? 22 : 0;
  const top1 = 30 + lift, top2 = top1 + bandH + 44 + lift, axisY = top2 + bandH + 12;
  const H = axisY + 34;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  // labels
  const lab = (y, t1, t2) => {
    if(narrow){ el('text',{x:left,y:y-30,'font-size':12.5,'font-weight':650,fill:css('--ink')},svg,`${t1}, ${t2}`); return; }
    el('text',{x:0,y:y+12,'font-size':14,'font-weight':650,fill:css('--ink')},svg,t1); el('text',{x:0,y:y+30,'font-size':12.5,fill:css('--muted')},svg,t2); };
  lab(top1, 'Before', narrow ? 'baseline' : 'baseline config');
  lab(top2, 'After', narrow ? 'candidate' : 'candidate config');
  // squares
  const place = (run, top, isCand) => {
    const fill = {}; let late = 0;
    for(const [sid,tid,v] of run){
      if(v==null) continue;
      const i = fill[v] = (fill[v]||0); fill[v]++;
      const r = Math.floor(i / per), c = i % per;
      const cx = x(v) - (per*step)/2 + c*step, cy = top + bandH - (r+1)*step;
      const over = isCand && v > D.thr;
      const id = `${sid}/${tid}`;
      const q = el('rect',{x:cx.toFixed(2),y:cy.toFixed(2),width:s.toFixed(2),height:s.toFixed(2),
        fill: !isCand ? css('--blue') : over ? css('--red') : '#56646A', class:'sq'+(over?' hit late':'')},svg);
      if(over){ q.style.setProperty('--i', late++); q.setAttribute('tabindex','0'); q.setAttribute('role','button');
        q.setAttribute('aria-label',`Turn ${id}, ${v} ms`); q.dataset.id=id;
        const t = el('title',{},q,`${id}: ${byId[id] ? byId[id].b+' ms before, ' : ''}${v} ms after`); }
      else { el('title',{},q,`${id}: ${v} ms`); }
    }
  };
  place(D.base, top1, false); place(D.cand, top2, true);
  // threshold, median, p95
  const yA = top1 - 14, yB = axisY;
  el('line',{x1:x(D.thr)+colW/2-2,x2:x(D.thr)+colW/2-2,y1:yA,y2:yB,stroke:css('--ink'),'stroke-width':1.5,'stroke-dasharray':'5 4'},svg);
  el('text',{x:x(D.thr)+colW/2+4,y:yA+8,'font-size':narrow?11:13,'font-weight':650,fill:css('--ink')},svg,narrow ? `p95 ≤ ${D.thr} ms` : `threshold p95 ≤ ${D.thr} ms`);
  const mark = (v, top, color, txt, anchorRight) => {
    const xx = x(v);
    el('line',{x1:xx,x2:xx,y1:top-6,y2:top+bandH+4,stroke:color,'stroke-width':2},svg);
    el('text',{x:anchorRight?xx-6:xx+6,y:top-8,'text-anchor':anchorRight?'end':'start','font-size':narrow?11:13,'font-weight':600,fill:color},svg,txt);
  };
  mark(D.median[0], top1, css('--ink'), `median ${D.median[0]} ms`, true);
  mark(D.median[1], top2, css('--ink'), narrow ? `median ${D.median[1]} ms` : `median ${D.median[1]} ms, unchanged`, true);
  mark(D.p95[1], top2, css('--red'), `p95 ${D.p95[1]} ms`, true);
  const nLate = D.cand.filter(r => r[2] != null && r[2] > D.thr).length;
  const deltas = [...new Set(D.affected.map(a => Math.round(a.c - a.b)))];
  const slowRows = Math.max(...[880,890,900,910,920].map(v => rowsFor(cc[v]||0)));
  el('text',{x:x(880)-(per*step)/2,y:top2+bandH-slowRows*step-9,'font-size':narrow?11:13,'font-weight':650,fill:css('--red')},svg,
    deltas.length === 1 ? (narrow ? `${nLate} turns, +${deltas[0]} ms each` : `${nLate} turns, each ${deltas[0]} ms slower than before`) : `${nLate} turns over ${D.thr} ms`);
  el('text',{x:x(D.p95[0])+colW/2+4,y:top1+bandH+16,'font-size':narrow?11:12.5,fill:css('--blue'),'font-weight':600},svg,`p95 ${D.p95[0]} ms`);
  // axis with a break
  const ax = (a,b) => el('line',{x1:x(a),x2:x(b),y1:axisY,y2:axisY,stroke:css('--ink'),'stroke-width':1},svg);
  ax(375,425); ax(875,925);
  const bx = left + segW + gap/2;
  for(const d of [-4,3]) el('line',{x1:bx+d-3,x2:bx+d+3,y1:axisY+6,y2:axisY-6,stroke:css('--ink'),'stroke-width':1.2},svg);
  for(const v of [...vals, ...vals.map(v=>v+500)]){
    el('line',{x1:x(v),x2:x(v),y1:axisY,y2:axisY+5,stroke:css('--ink')},svg);
    if(!narrow || v % 20 === 0) el('text',{x:x(v),y:axisY+20,'text-anchor':'middle','font-size':narrow?10.5:12.5,fill:css('--muted')},svg,`${v}`);
  }
  el('text',{x:left+2*segW+gap,y:axisY+34,'text-anchor':'end','font-size':narrow?10.5:12,fill:css('--muted')},svg,'turn total, ms');
  svg.setAttribute('height', H);
  svg.querySelectorAll('.sq.hit').forEach(q => {
    q.addEventListener('click', () => openDlg(q.dataset.id));
    q.addEventListener('keydown', e => { if(e.key==='Enter'||e.key===' '){ e.preventDefault(); openDlg(q.dataset.id); }});
  });
}

const SEG = {endpointing_wait:'endpointing', transcription:'STT', turn_detection:'turn', llm_inference:'LLM inference', speech_synthesis:'speech synthesis', output_transport:'output', pipeline:'pipeline', sentence_aggregation:'aggregation'};
function trace(svg, a){
  svg.innerHTML = '';
  const W = Math.max(300, svg.parentElement.clientWidth - 2), narrow = W < 520;
  const left = narrow ? 52 : 68, right = 12, maxMs = Math.ceil(Math.max(a.b, a.c) / 100) * 100;
  const sx = v => left + v / maxMs * (W - left - right);
  const rows = [['Before', a.tr.baseline, a.b], ['After', a.tr.candidate, a.c]];
  const H = 196; svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  rows.forEach(([name, segs, total], i) => {
    const y = 18 + i * 78;
    el('text',{x:0,y:y+15,'font-size':13,'font-weight':650,fill:css('--ink')},svg,name);
    el('text',{x:0,y:y+31,'font-size':12,fill:css('--muted')},svg,`${total} ms`);
    for(const [key,label,start,dur] of segs){
      if(start==null) continue;
      const isLLM = key === 'llm_inference';
      const color = isLLM ? (i ? css('--red') : css('--blue')) : (['#8A989D','#AEB9BC','#6F7C81','#9AA6AA','#7D8A8F','#B8C2C4'][Object.keys(SEG).indexOf(key)%6]);
      const x0 = sx(start), w = Math.max(1, sx(start+dur) - x0);
      el('rect',{x:x0,y:y,width:w,height:24,fill:color},svg);
      const tx = SEG[key]||label;
      if(w > tx.length * 6.4 + 10) el('text',{x:x0+5,y:y+16,'font-size':11.5,fill:'#fff','font-weight':600},svg,tx);
      if(isLLM) el('text',{x:x0+2,y:y+40,'font-size':11.5,fill:i?css('--red'):css('--blue'),'font-weight':650},svg,`${dur} ms`);
    }
  });
  const tb = svg.nextElementSibling;
  if(tb && tb.tagName === 'TABLE'){
    const before = Object.fromEntries(a.tr.baseline.map(r => [r[0], r])), after = Object.fromEntries(a.tr.candidate.map(r => [r[0], r]));
    const keys = [...new Set([...a.tr.baseline.map(r=>r[0]), ...a.tr.candidate.map(r=>r[0])])];
    tb.innerHTML = '<thead><tr><th>Stage, as Pipecat names it</th><th>Before</th><th>After</th></tr></thead><tbody>' +
      keys.map(k => `<tr${k==='llm_inference'?' class="hl"':''}><td><i style="background:${k==='llm_inference'?css('--red'):(['#8A989D','#AEB9BC','#6F7C81','#9AA6AA','#7D8A8F','#B8C2C4'][Object.keys(SEG).indexOf(k)%6])}"></i>${(before[k]||after[k])[1]}</td><td>${before[k]?before[k][3]+' ms':'absent'}</td><td>${after[k]?after[k][3]+' ms':'absent'}</td></tr>`).join('') +
      `<tr class="tot"><td>Turn total, measured</td><td>${a.b} ms</td><td>${a.c} ms</td></tr></tbody>`;
  }
  const ay = 182;
  el('line',{x1:sx(0),x2:sx(maxMs),y1:ay-12,y2:ay-12,stroke:css('--rule')},svg);
  for(let v=0; v<=maxMs; v+= maxMs > 600 ? 200 : 100){
    el('text',{x:sx(v),y:ay,'text-anchor':v===0?'start':'middle','font-size':11,fill:css('--muted')},svg,v===0?'0 ms':`${v}`);
  }
}

const dlg = document.getElementById('dlg');
function select(id){
  const a = byId[id]; if(!a) return;
  document.querySelectorAll('#turns button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.id === id)));
  document.getElementById('dlg-trace-title').textContent = `Turn ${id}: ${a.b} ms before, ${a.c} ms after`;
  trace(document.getElementById('dlg-trace-svg'), a);
}
function openDlg(id){
  const g = document.getElementById('turns');
  if(!g.children.length){
    for(const a of D.affected.slice().sort((p,q)=> p.id < q.id ? -1 : 1)){
      const b = document.createElement('button'); b.type='button'; b.textContent=a.id; b.dataset.id=a.id; b.setAttribute('aria-pressed','false');
      b.addEventListener('click', () => select(a.id)); g.appendChild(b);
    }
  }
  if(typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open','');
  select(id || D.featured);
  const cur = g.querySelector('[aria-pressed=true]'); if(cur) cur.scrollIntoView({block:'nearest'});
}
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
strip(); trace(document.getElementById('trace-svg'), byId[D.featured]);
let rt; addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => { strip(); trace(document.getElementById('trace-svg'), byId[D.featured]); }, 120); });
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
