#!/usr/bin/env python3
"""Build the static, publishable benchmark gallery in ./published/.

Reads (read-only):  archive/2026-10-17-model-survey/*  and  results/*
Writes:             published/  (wiped and rebuilt on every run)

Stdlib only. Screenshot conversion uses macOS `sips`.
"""
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from html import escape
from pathlib import Path
from statistics import mean
from urllib.parse import quote

BENCH = Path(__file__).resolve().parent
SOURCES = [(BENCH / "archive" / "2026-10-17-model-survey", True), (BENCH / "results", False)]
OUT = BENCH / "published"

SKIP_DIRS = {".git", "node_modules", "dist", ".astro", ".DS_Store", "__pycache__"}
SKIP_FILES = {"package-lock.json", ".DS_Store"}
MAX_FILE = 1_000_000  # workspace files larger than this are listed as omitted

# ---------------------------------------------------------------- label scheme
MODEL = {
    "Qwen3.6-35B-A3B-UD-Q6_K_XL": "Qwen3.6 35B-A3B", "qwen3.6-35b-general": "Qwen3.6 35B-A3B",
    "qwen3.6-35b-baseline": "Qwen3.6 35B-A3B", "qwen3.6-35b-budget4k": "Qwen3.6 35B-A3B",
    "Qwen3.8-27B-UD-Q6_K_XL": "Qwen3.8 27B", "qwen3.8-27b-budget": "Qwen3.8 27B",
    "qwen3.8-27b-budget4k": "Qwen3.8 27B", "qwen3.8-27b-budget4k-mtp": "Qwen3.8 27B",
    "qwen3.8-27b-instruct": "Qwen3.8 27B",
    "gemma-4-26B-A4B-it-UD-Q6_K_XL": "Gemma 4 26B-A4B",
    "Qwen3-Coder-Next-UD-IQ4_XS": "Qwen3-Coder-Next", "qwen3-coder-next-t0.6": "Qwen3-Coder-Next",
    "cline-pass/kimi-k3": "Kimi K3", "cline-pass/glm-5.3": "GLM-5.3",
    "cline-pass/deepseek-v4-pro": "DeepSeek V4 Pro", "cline-pass/qwen3.8-max": "Qwen3.8-Max",
}
WHERE = {"local": "Local (Mac Studio)", "clinepass": "Cloud (ClinePass)"}
TASK = {"astro-blog": "Astro blog", "tetris": "Tetris (browser)"}
TASK_SHORT = {"astro-blog": "Astro blog", "tetris": "Tetris"}
SAMPLING = {"qwen3.6-35b-baseline": "Defaults (untuned)", "qwen3.6-35b-general": "General",
            "qwen3-coder-next-t0.6": "Low temp", "qwen3.8-27b-instruct": "Non-thinking"}
HARNESS = [  # (profile, label, explanation)
    ("minimal", "Bare pi", "pi's 4 default tools (read, bash, edit, write); no extensions."),
    ("batteries-nolens", "Tools (lens off)",
     "Adds web search/fetch, task tracking, a headless browser and grep/find/ls. The pi-lens code-diagnostics "
     "extension was installed but inactive (a harness bug)."),
    ("batteries", "Tools", "Same tools, with pi-lens code diagnostics working."),
    ("batteries+agents", "Tools + guidance", "Adds general working-practices guidance to the system prompt."),
    ("batteries+agents-nolens", "Tools + guidance − lens", "Tools + guidance, with pi-lens off."),
    ("batteries+docs", "Tools + docs rule",
     "Guidance with a firm “read the official docs before first using a framework API” rule."),
    ("tuned", "Tuned", "Docs rule plus a default bash timeout; no pi-lens."),
    ("tuned+finish", "Tuned + finish check",
     "Adds “re-read the request, check every part, remove placeholder content before finishing”."),
    ("tuned+finish+lens", "Tuned + finish check + lens", "Tuned + finish check, with pi-lens on."),
    ("tuned+finish+codemode", "Tuned + finish check + codemode", "Tuned + finish check, with the codemode extension."),
]
HARNESS_LABEL = {p: l for p, l, _ in HARNESS}
EXPERIMENTS = ["1 · Model survey", "2 · Environment tuning", "3 · Probes", "4 · Final confirmation"]
CAPS = ["No cap", "8K cap", "4K cap"]
SAMPLING_ORDER = ["Model card", "Defaults (untuned)", "General", "Low temp", "Non-thinking"]
MODEL_ORDER = ["Qwen3.6 35B-A3B", "Qwen3.8 27B", "Gemma 4 26B-A4B", "Qwen3-Coder-Next",
               "Kimi K3", "GLM-5.3", "DeepSeek V4 Pro", "Qwen3.8-Max"]
WARN = []


def experiment(name, from_archive):
    if from_archive:
        return EXPERIMENTS[0]
    if any(f"probe{i}" in name for i in (1, 2, 3)) or "think-budget-8k" in name or "no-think" in name:
        return EXPERIMENTS[2]
    if "· final ·" in name:
        return EXPERIMENTS[3]
    return EXPERIMENTS[1]


def labels(r, from_archive):
    m = r.get("model", "")
    if m not in MODEL:
        WARN.append(f"unmapped model {m!r}")
    prov = r.get("provider") or "local"
    if prov not in WHERE:
        WARN.append(f"unmapped provider {prov!r}")
    prof = r.get("profile") or "minimal"
    if prof not in HARNESS_LABEL:
        WARN.append(f"unmapped profile {prof!r}")
    task = r.get("task")
    if task not in TASK:
        WARN.append(f"unmapped task {task!r}")
    cap = "8K cap" if m == "qwen3.8-27b-budget" else "4K cap" if "budget4k" in m else "No cap"
    return {
        "experiment": experiment(r.get("name", ""), from_archive),
        "model": MODEL.get(m, m),
        "where": WHERE.get(prov, prov),
        "task": TASK.get(task, task),
        "harness": HARNESS_LABEL.get(prof, prof),
        "thinking": "Thinking off" if r.get("thinking") == "off" else "Thinking on",
        "cap": cap,
        "speed": "MTP" if "mtp" in m else "No MTP",
        "sampling": SAMPLING.get(m, "Model card"),
    }


DIMS = [  # key, title, ordered values, help
    ("experiment", "Experiment", EXPERIMENTS, "Which batch of runs this belongs to."),
    ("model", "Model", MODEL_ORDER, "The model that drove the agent."),
    ("where", "Where it ran", list(WHERE.values()), "Local = llama.cpp on a Mac Studio M2 Ultra 64 GB; Cloud = hosted via ClinePass."),
    ("task", "Task", list(TASK.values()), "The single prompt the agent was given."),
    ("harness", "Harness", [l for _, l, _ in HARNESS], "What pi was given besides the model (tools, prompt guidance)."),
    ("thinking", "Thinking", ["Thinking on", "Thinking off"], "Whether the model's reasoning mode was enabled."),
    ("cap", "Thinking cap", CAPS, "llama.cpp limit on reasoning tokens per response."),
    ("speed", "Speed-up", ["MTP", "No MTP"], "MTP = multi-token prediction (speculative decoding), roughly 2x faster generation."),
    ("sampling", "Sampling", SAMPLING_ORDER, "Sampling settings used (temperature etc.)."),
]


# ---------------------------------------------------------------- helpers
def read_json(p):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return None


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def sips(args):
    subprocess.run(["sips", *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def convert_shot(job):
    src, full, thumb, tw = job
    try:
        full.parent.mkdir(parents=True, exist_ok=True)
        thumb.parent.mkdir(parents=True, exist_ok=True)
        fw = 480 if tw == 240 else 960
        sips(["-s", "format", "jpeg", "-s", "formatOptions", "50", "--resampleWidth", str(fw), str(src), "--out", str(full)])
        sips(["-s", "format", "jpeg", "-s", "formatOptions", "55", "--resampleWidth", str(tw), str(src), "--out", str(thumb)])
        return True
    except Exception as ex:
        WARN.append(f"screenshot failed {src}: {ex}")
        return False


FILES_CSS = ("body{font:15px/1.5 system-ui,sans-serif;max-width:50rem;margin:0 auto;padding:1.5rem 16px;"
             "background:#fff;color:#1c1c1e}a{color:#0a58ca}li{padding:.1rem 0;word-break:break-all}"
             "small,.m{color:#6b6b70}@media(prefers-color-scheme:dark){body{background:#17171a;color:#e8e8ea}a{color:#7db3ff}"
             "small,.m{color:#9a9aa2}}")


def copy_workspace(src, dst):
    """Copy workspace sans junk; return (listing entries, omitted entries)."""
    entries, omitted = [], []
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            p = Path(root) / f
            rel = p.relative_to(src).as_posix()
            if f in SKIP_FILES or f.endswith(".log") or p.is_symlink():
                continue
            size = p.stat().st_size
            if size > MAX_FILE:
                omitted.append((rel, size))
                continue
            t = dst / rel
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, t)
            entries.append((rel, size))
    return entries, omitted


def write_files_page(rundir, title, entries, omitted):
    li = "".join(f"<li><a href='workspace/{quote(rel)}'>{escape(rel)}</a> <small>{human(sz)}</small></li>"
                 for rel, sz in entries) or "<li class=m>No files were produced.</li>"
    om = ""
    if omitted:
        om = ("<p class=m>Omitted from this copy (too large): " +
              ", ".join(f"{escape(r)} ({human(s)})" for r, s in omitted) + "</p>")
    (rundir / "files.html").write_text(
        f"<!doctype html><html lang=en><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width, initial-scale=1'><title>Files – {escape(title)}</title>"
        f"<style>{FILES_CSS}</style></head><body><p><a href='../../index.html'>&larr; Gallery</a></p>"
        f"<h1 style='font-size:1.2rem'>Files produced: {escape(title)}</h1>"
        f"<p class=m>Source files the agent wrote (build output, node_modules and lock files are left out).</p>"
        f"<ul>{li}</ul>{om}</body></html>")


# ---------------------------------------------------------------- build
def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "runs").mkdir(parents=True)

    runs, jobs = [], []
    seen = set()
    for base, is_archive in SOURCES:
        for d in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")):
            r = read_json(d / "result.json")
            if not r:
                WARN.append(f"no result.json in {d}")
                continue
            did = d.name
            if did in seen:
                did = ("a-" if is_archive else "r-") + did
            seen.add(did)
            dst = OUT / "runs" / did
            dst.mkdir(parents=True)
            lab = labels(r, is_archive)
            rev = read_json(d / "review.json")
            title = f"{lab['model']} · {TASK_SHORT.get(r.get('task'), lab['task'])}"

            for f in ("result.json", "check.json", "review.json", "transcript.md", "session.html"):
                if (d / f).exists():
                    shutil.copyfile(d / f, dst / f)
            entries, omitted = ([], [])
            if (d / "workspace").is_dir():
                entries, omitted = copy_workspace(d / "workspace", dst / "workspace")
            write_files_page(dst, f"{title} (run {r.get('repeat')})", entries, omitted)

            # screenshots: home + (play-later | post) + phone
            avail = set(r.get("shots") or [])
            shots = []
            mid = ("play-later.png", "after playing") if "play-later.png" in avail else ("post-desktop.png", "post")
            for fn, cap, tw in (("home-desktop.png", "home", 400), mid + (400,), ("home-mobile.png", "phone", 240)):
                src = d / "shots" / fn
                if fn in avail and src.exists():
                    stem = fn[:-4]
                    jobs.append((src, dst / "shots" / f"{stem}.jpg", dst / "shots" / "t" / f"{stem}.jpg", tw))
                    shots.append({"cap": cap, "full": f"shots/{stem}.jpg", "thumb": f"shots/t/{stem}.jpg"})
                else:
                    shots.append({"cap": cap, "full": None, "thumb": None})

            checks = r.get("checks") or {}
            failed = [k for k, v in checks.items() if not v]
            revr = r.get("review") or {}
            cfg = "|".join(str(x) for x in (r.get("name"),
                                            r.get("model"), r.get("provider"), r.get("profile"),
                                            r.get("thinking"), r.get("task"), lab["experiment"]))
            runs.append({
                "id": did, "title": title, "name": r.get("name"), "labels": lab,
                "profile": r.get("profile") or "minimal", "repeat": r.get("repeat"),
                "started": r.get("started"), "cfg": cfg,
                "score": r.get("score"), "verdict": (rev or {}).get("verdict") or revr.get("verdict"),
                "idio": (rev or {}).get("idiomatic", revr.get("idiomatic")),
                "qual": (rev or {}).get("quality", revr.get("quality")),
                "minutes": round((r.get("agent_seconds") or 0) / 60, 1) if r.get("agent_seconds") is not None else None,
                "turns": r.get("turns"), "toolCalls": r.get("tool_calls"), "toolErrors": r.get("tool_errors"),
                "timedOut": bool(r.get("timed_out")), "failed": failed, "noChecks": not checks,
                "brokenLinks": r.get("broken_links"), "contrast": r.get("contrast_failures"),
                "tools": r.get("tools") or {},
                "review": ({"summary": rev.get("summary"), "findings": rev.get("findings") or [],
                            "process": rev.get("process_notes")} if rev else None),
                "has": {k: (dst / f).exists() for k, f in (("session", "session.html"), ("transcript", "transcript.md"),
                                                          ("check", "check.json"), ("review", "review.json"),
                                                          ("result", "result.json"))},
                "files": len(entries), "shots": shots,
            })

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(convert_shot, jobs))
    # any shot whose conversion failed -> treat as missing
    for run in runs:
        for s in run["shots"]:
            if s["full"] and not (OUT / "runs" / run["id"] / s["full"]).exists():
                s["full"] = s["thumb"] = None

    data = {
        "dims": [{"key": k, "title": t, "values": v, "help": h} for k, t, v, h in DIMS],
        "harness": [{"label": l, "profile": p, "desc": e} for p, l, e in HARNESS],
        "runs": runs,
    }
    blob = json.dumps(data, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c")
    html = TEMPLATE.replace("/*__DATA__*/", blob)
    (OUT / "index.html").write_text(html, encoding="utf-8")

    # ---- report
    def du(p):
        return sum(f.stat().st_size for f in Path(p).rglob("*") if f.is_file())
    tot = du(OUT)
    parts = {"index.html": (OUT / "index.html").stat().st_size}
    for name, pats in (("session.html", ["session.html"]), ("transcript.md", ["transcript.md"]),
                       ("json (result/check/review)", ["result.json", "check.json", "review.json"]),
                       ("files.html", ["files.html"])):
        parts[name] = sum((p / f).stat().st_size for p in (OUT / "runs").iterdir() for f in pats if (p / f).exists())
    parts["screenshots (full)"] = sum(f.stat().st_size for f in (OUT / "runs").glob("*/shots/*.jpg"))
    parts["screenshots (thumbs)"] = sum(f.stat().st_size for f in (OUT / "runs").glob("*/shots/t/*.jpg"))
    parts["workspace sources"] = sum(du(p / "workspace") for p in (OUT / "runs").iterdir() if (p / "workspace").exists())
    print(f"{len(runs)} runs, {len(jobs)} screenshots converted")
    print(f"total {human(tot)}")
    for k, v in parts.items():
        print(f"  {k:28s}{human(v):>10s}")
    for w in sorted(set(WARN)):
        print("WARN", w)


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Local LLM coding-agent benchmark gallery</title>
<style>
:root{--bg:#f7f7f5;--card:#fff;--fg:#1c1c1e;--mut:#66666c;--line:#dcdcd6;--acc:#2456c8;--accbg:#e6edfb;--chip:#efefea;
--good:#1f7a3d;--goodbg:#e0f2e6;--warn:#8a5a00;--warnbg:#fbefd0;--bad:#a8321f;--badbg:#fbe3de;--orange:#9a4a00;--orangebg:#fde6cf}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#131316;--card:#1c1c20;--fg:#e8e8ea;--mut:#9a9aa3;--line:#33333a;--acc:#8fb4ff;--accbg:#25324f;--chip:#2a2a30;
--good:#7fd69b;--goodbg:#1c3626;--warn:#e8c26a;--warnbg:#3b3117;--bad:#ff9a88;--badbg:#44211b;--orange:#f2b27a;--orangebg:#40291a}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:20px 16px 60px}
a{color:var(--acc)}
h1{font-size:1.5rem;margin:.2em 0 .3em}
h2{font-size:1.05rem;margin:0}
.mut{color:var(--mut)}
.intro p{margin:.5em 0;max-width:62rem}
.intro blockquote{margin:.4em 0;padding:.3em .8em;border-left:3px solid var(--line);color:var(--mut)}
details.legend{margin:.8em 0;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.5em .9em}
details.legend>summary,details.filters>summary{cursor:pointer;font-weight:600}
.legend dl{display:grid;grid-template-columns:max-content 1fr;gap:.25em 1em;margin:.6em 0}
.legend dt{font-weight:600}.legend dd{margin:0}
@media(max-width:640px){.legend dl{grid-template-columns:1fr}.legend dd{margin-bottom:.4em}}
.filters{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.6em .9em;margin:.8em 0}
.frow{display:flex;flex-wrap:wrap;gap:.35em;align-items:center;margin:.5em 0}
.frow .fl{min-width:7.5rem;font-size:.82rem;color:var(--mut);font-weight:600}
button.chip,span.chip{font:inherit;font-size:.78rem;line-height:1.25;border:1px solid var(--line);background:var(--chip);color:var(--fg);
border-radius:999px;padding:.15em .65em;display:inline-block}
button.chip{cursor:pointer}
button.chip[aria-pressed=true]{background:var(--acc);border-color:var(--acc);color:var(--bg);font-weight:600}
button.chip small{opacity:.65}
span.chip.h{background:var(--accbg);border-color:transparent}
span.chip.e{background:transparent}
.bar{display:flex;flex-wrap:wrap;gap:.6em 1.2em;align-items:center;margin:.8em 0}
.bar select,.bar button{font:inherit;font-size:.9rem;padding:.25em .5em;border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
.bar button{cursor:pointer}
#count{font-weight:600}
.group{margin:1.4em 0 .4em}
.ghead{display:flex;flex-wrap:wrap;gap:.3em .6em;align-items:baseline;margin-bottom:.4em}
.ghead .gs{font-size:.85rem;color:var(--mut)}
.chips{display:flex;flex-wrap:wrap;gap:.3em;margin:.3em 0 .5em}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,520px),1fr));gap:14px;align-items:start}
.run{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
.head{display:flex;flex-wrap:wrap;align-items:center;gap:.4em .7em}
.score{font-size:1.6rem;font-weight:700;line-height:1}
.score small{font-size:.7rem;font-weight:400;color:var(--mut)}
.badge{font-size:.72rem;border-radius:5px;padding:.1em .5em;font-weight:600;background:var(--chip);white-space:nowrap}
.badge.clean,.badge.good,.badge.sev-info{background:var(--goodbg);color:var(--good)}
.badge.minor_issues,.badge.sev-low{background:var(--warnbg);color:var(--warn)}
.badge.significant_issues,.badge.sev-medium{background:var(--orangebg);color:var(--orange)}
.badge.broken,.badge.sev-high{background:var(--badbg);color:var(--bad)}
.badge.to{background:var(--badbg);color:var(--bad)}
.rt{margin:.35em 0 0;font-size:.88rem}
.rtitle{font-weight:600;margin-top:.3em}
.shots{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin:.6em 0}
.shots figure{margin:0;min-width:0}
.shots a,.shots .miss{display:block;aspect-ratio:4/3;overflow:hidden;border:1px solid var(--line);border-radius:6px;background:var(--chip)}
.shots img{width:100%;height:100%;object-fit:cover;object-position:top;display:block}
.shots .miss{display:flex;align-items:center;justify-content:center;text-align:center;font-size:.7rem;color:var(--mut);padding:4px}
.shots figcaption{font-size:.72rem;color:var(--mut);text-align:center}
.facts{font-size:.84rem;color:var(--mut);margin:.3em 0}
.facts b{color:var(--fg)}
.sum{margin:.5em 0;font-size:.92rem}
ul.findings{margin:.4em 0;padding-left:1.1em;font-size:.86rem}
ul.findings li{margin:.3em 0}
code{font-size:.85em;background:var(--chip);padding:0 .25em;border-radius:3px;word-break:break-all}
.links{display:flex;flex-wrap:wrap;gap:.3em 1em;margin-top:.5em;font-size:.88rem}
details.low>summary{font-size:.84rem;color:var(--mut);cursor:pointer}
.empty{padding:2em 0;text-align:center;color:var(--mut)}
footer{margin-top:2em;font-size:.8rem;color:var(--mut)}
</style>
</head>
<body>
<main>
<header class="intro">
<h1>Local LLM coding-agent benchmark gallery</h1>
<p>An AI coding agent (<b>pi</b>) was given one prompt in an empty folder and left to work on its own. The models were open-weight ones running locally on a Mac Studio (M2 Ultra, 64 GB), plus a few cloud models for comparison. Two tasks:</p>
<blockquote><b>Astro blog:</b> &ldquo;Create a static site for my personal blog using Astro and populate it with 4-5 sample posts.&rdquo;<br>
<b>Tetris:</b> &ldquo;Create a playable Tetris game that runs in the web browser, using only plain HTML, CSS and JavaScript (no frameworks, libraries or build step).&rdquo;</blockquote>
<p><b>Scoring.</b> Each run gets an automated score out of 100 (does it install, build, have pages, links, content&hellip;), plus a code review by Claude Opus giving a verdict and 1&ndash;5 ratings for idiomatic use of the framework and overall quality. Caveat: the Tetris automated checks are coarse and can pass games a human would find broken, so lean on the review and the screenshots there. Runs were done in four batches: a model survey, environment tuning, a few probes, and a final confirmation of the best setup.</p>
<details class="legend"><summary>What the labels mean</summary>
<dl id="legend"></dl>
</details>
</header>

<details class="filters" id="filters" open><summary>Filters <span class="mut" id="nactive"></span></summary><div id="frows"></div></details>
<div class="bar">
<span id="count" aria-live="polite"></span>
<label>Sort <select id="sort">
<option value="score">Score (best first)</option>
<option value="review">Review rating (best first)</option>
<option value="fast">Time (fastest first)</option>
<option value="slow">Time (slowest first)</option>
<option value="new">Date (newest first)</option>
<option value="old">Date (oldest first)</option>
</select></label>
<label><input type="checkbox" id="group" checked> Group identical configurations</label>
<button id="clear" type="button">Clear filters</button>
</div>
<div id="out"></div>
<footer>Click a screenshot for full size. &ldquo;Session&rdquo; opens pi's full interactive session viewer. Review ratings: idiomatic / quality, each out of 5.</footer>
</main>
<script id="data" type="application/json">/*__DATA__*/</script>
<script>
(function(){
var D=JSON.parse(document.getElementById('data').textContent);
var runs=D.runs, dims=D.dims;
var harnessDesc={};D.harness.forEach(function(h){harnessDesc[h.label]=h.desc});
var dimHelp={};dims.forEach(function(d){dimHelp[d.key]=d.help});
var hiddenDefaults={cap:'No cap',sampling:'Model card',speed:'No MTP'};
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function enc(p){return p.split('/').map(encodeURIComponent).join('/')}
function ts(r){return r.started?Date.parse(r.started):0}
function fmt(v){return v==null?'–':v}

// ---- legend
(function(){
 var items=[
  ['Harness','What pi was given besides the model. Bare pi has only its 4 default tools; the other levels add tools and prompt guidance:'],
 ];
 var h='';
 h+='<dt>Harness</dt><dd><ul style="margin:0;padding-left:1.1em">'+D.harness.map(function(x){return '<li><b>'+esc(x.label)+'</b> – '+esc(x.desc)+'</li>'}).join('')+'</ul></dd>';
 h+='<dt>Thinking / cap</dt><dd>Whether the model\'s reasoning mode was on. A <b>cap</b> (4K or 8K) is a llama.cpp limit on reasoning tokens per response, so the model cannot think forever.</dd>';
 h+='<dt>MTP</dt><dd>Multi-token prediction (speculative decoding): roughly 2&times; faster generation, same model.</dd>';
 h+='<dt>Sampling</dt><dd>Model card = the model author\'s recommended settings; Defaults (untuned) = plain server defaults; General = general-purpose settings; Low temp = lower temperature; Non-thinking = instruct mode.</dd>';
 h+='<dt>Where</dt><dd>Local (Mac Studio) = llama.cpp on the machine above. Cloud (ClinePass) = hosted models, for comparison.</dd>';
 h+='<dt>Experiment</dt><dd>1 Model survey (bare/basic setups across models); 2 Environment tuning (harness variations on Qwen3.8 27B); 3 Probes (one-off tests of single ideas); 4 Final confirmation (3 repeats of the best setups).</dd>';
 h+='<dt>Review verdict</dt><dd>Claude Opus&rsquo;s overall verdict: clean &rarr; minor issues &rarr; significant issues &rarr; broken. Ratings are idiomatic / quality (1&ndash;5).</dd>';
 document.getElementById('legend').innerHTML=h;
})();

// ---- state
var st={f:{},sort:'score',group:true};
dims.forEach(function(d){st.f[d.key]=[]});
function readHash(){
 var p=new URLSearchParams(location.hash.replace(/^#/,''));
 dims.forEach(function(d){var v=p.get(d.key);st.f[d.key]=v?v.split('|').filter(function(x){return d.values.indexOf(x)>=0}):[]});
 if(p.get('sort'))st.sort=p.get('sort');
 if(p.has('group'))st.group=p.get('group')!=='0';
}
function writeHash(){
 var p=new URLSearchParams();
 dims.forEach(function(d){if(st.f[d.key].length)p.set(d.key,st.f[d.key].join('|'))});
 if(st.sort!=='score')p.set('sort',st.sort);
 if(!st.group)p.set('group','0');
 var s=p.toString();
 try{history.replaceState(null,'',s?'#'+s:location.pathname+location.search)}catch(e){}
}

// ---- filters UI
var counts={};
dims.forEach(function(d){counts[d.key]={};runs.forEach(function(r){var v=r.labels[d.key];counts[d.key][v]=(counts[d.key][v]||0)+1})});
function buildFilters(){
 var h='';
 dims.forEach(function(d){
  var vals=d.values.filter(function(v){return counts[d.key][v]});
  h+='<div class="frow"><span class="fl" title="'+esc(d.help)+'">'+esc(d.title)+'</span>';
  vals.forEach(function(v){
   var on=st.f[d.key].indexOf(v)>=0;
   var tip=d.key==='harness'?harnessDesc[v]:d.help;
   h+='<button type="button" class="chip" aria-pressed="'+on+'" data-d="'+d.key+'" data-v="'+esc(v)+'" title="'+esc(tip)+'">'+esc(v)+' <small>'+counts[d.key][v]+'</small></button>';
  });
  h+='</div>';
 });
 document.getElementById('frows').innerHTML=h;
}
document.getElementById('frows').addEventListener('click',function(e){
 var b=e.target.closest('button.chip');if(!b)return;
 var k=b.getAttribute('data-d'),v=b.getAttribute('data-v'),a=st.f[k],i=a.indexOf(v);
 if(i>=0)a.splice(i,1);else a.push(v);
 update(true);
});
document.getElementById('sort').addEventListener('change',function(e){st.sort=e.target.value;update(false)});
document.getElementById('group').addEventListener('change',function(e){st.group=e.target.checked;update(false)});
document.getElementById('clear').addEventListener('click',function(){dims.forEach(function(d){st.f[d.key]=[]});update(true)});
window.addEventListener('hashchange',function(){readHash();syncControls();update(true,true)});
function syncControls(){document.getElementById('sort').value=st.sort;document.getElementById('group').checked=st.group}

// ---- sorting
function metric(r){
 switch(st.sort){
  case 'score':return -(r.score==null?-1:r.score);
  case 'review':return -((r.idio==null||r.qual==null)?-1:(r.idio+r.qual)*1000+(r.score||0));
  case 'fast':return r.minutes==null?1e9:r.minutes;
  case 'slow':return -(r.minutes==null?-1:r.minutes);
  case 'new':return -ts(r);
  case 'old':return ts(r);
 }
 return 0;
}
function cmp(a,b){return metric(a)-metric(b)||a.title.localeCompare(b.title)||(a.repeat||0)-(b.repeat||0)}
function avg(a){return a.length?a.reduce(function(x,y){return x+y},0)/a.length:null}

// ---- rendering
function chipsFor(r,onlyNotable){
 var l=r.labels,h='';
 h+='<span class="chip h" title="'+esc(harnessDesc[l.harness]||'')+'">'+esc(l.harness)+'</span>';
 ['thinking','cap','speed','sampling','where','experiment'].forEach(function(k){
  if(hiddenDefaults[k]===l[k])return;
  h+='<span class="chip'+(k==='experiment'?' e':'')+'" title="'+esc(dimHelp[k])+'">'+esc(l[k])+'</span>';
 });
 return h;
}
function shotsHtml(r){
 var base='runs/'+enc(r.id)+'/';
 return '<div class="shots">'+r.shots.map(function(s){
  if(!s.full)return '<figure><div class="miss">no '+esc(s.cap)+' page found</div><figcaption>'+esc(s.cap)+'</figcaption></figure>';
  return '<figure><a href="'+base+enc(s.full)+'" target="_blank" rel="noopener"><img loading="lazy" decoding="async" src="'+base+enc(s.thumb)+'" alt="'+esc(s.cap)+' screenshot"></a><figcaption>'+esc(s.cap)+'</figcaption></figure>';
 }).join('')+'</div>';
}
function findingList(fs){
 return '<ul class="findings">'+fs.map(function(f){
  return '<li><span class="badge sev-'+esc(f.severity)+'">'+esc(f.severity)+'</span> <code>'+esc(f.file)+'</code> '+esc(f.issue)+'</li>';
 }).join('')+'</ul>';
}
function cardHtml(r,grouped){
 var base='runs/'+enc(r.id)+'/',h='<section class="run" id="'+esc(r.id)+'">';
 h+='<div class="head"><span class="score">'+(r.score==null?'–':Math.round(r.score))+'<small>/100</small></span>';
 if(r.verdict)h+='<span class="badge '+esc(r.verdict)+'">'+esc(r.verdict.replace(/_/g,' '))+'</span>';
 if(r.idio!=null)h+='<span class="mut" title="idiomatic / quality (1-5), by Claude Opus">review '+r.idio+' / '+r.qual+'</span>';
 if(r.timedOut)h+='<span class="badge to">timed out</span>';
 h+='<span class="mut" style="margin-left:auto;font-size:.82rem">run '+fmt(r.repeat)+(r.started?' · '+esc(r.started.slice(0,16).replace('T',' ')):'')+'</span></div>';
 if(!grouped){h+='<div class="rtitle">'+esc(r.title)+'</div><div class="chips">'+chipsFor(r)+'</div>'}
 h+=shotsHtml(r);
 var facts='<b>'+fmt(r.minutes)+'</b> min · <b>'+fmt(r.turns)+'</b> turns · <b>'+fmt(r.toolErrors)+'</b>/'+fmt(r.toolCalls)+' tool errors';
 if(r.labels.task==='Astro blog')facts+=' · broken links <b>'+fmt(r.brokenLinks)+'</b> · contrast failures <b>'+fmt(r.contrast)+'</b>';
 if(r.failed.length)facts+=' · failed checks: <b>'+esc(r.failed.join(', '))+'</b>';
 h+='<div class="facts">'+facts+'</div>';
 var rv=r.review;
 if(rv){
  if(rv.summary)h+='<p class="sum">'+esc(rv.summary)+'</p>';
  var maj=rv.findings.filter(function(f){return f.severity==='high'}).concat(rv.findings.filter(function(f){return f.severity==='medium'}));
  var low=rv.findings.filter(function(f){return f.severity!=='high'&&f.severity!=='medium'});
  if(maj.length)h+=findingList(maj);
  if(low.length)h+='<details class="low"><summary>'+low.length+' low-severity finding'+(low.length>1?'s':'')+'</summary>'+findingList(low)+'</details>';
  if(rv.process)h+='<p class="facts"><b>Process:</b> '+esc(rv.process)+'</p>';
 }else h+='<p class="facts">No code review for this run.</p>';
 var L=[];
 if(r.has.session)L.push('<a href="'+base+'session.html">session viewer</a>');
 if(r.has.transcript)L.push('<a href="'+base+'transcript.md">transcript</a>');
 L.push('<a href="'+base+'files.html">files ('+r.files+')</a>');
 if(r.has.check)L.push('<a href="'+base+'check.json">checks</a>');
 if(r.has.review)L.push('<a href="'+base+'review.json">review</a>');
 if(r.has.result)L.push('<a href="'+base+'result.json">result</a>');
 return h+'<div class="links">'+L.join('')+'</div></section>';
}
function update(rebuildFilters,skipHash){
 var list=runs.filter(function(r){return dims.every(function(d){var a=st.f[d.key];return !a.length||a.indexOf(r.labels[d.key])>=0})});
 var out=document.getElementById('out'),h='';
 var groups=[];
 if(st.group){
  var m={};list.forEach(function(r){(m[r.cfg]=m[r.cfg]||[]).push(r)});
  Object.keys(m).forEach(function(k){groups.push({runs:m[k].sort(function(a,b){return (a.repeat||0)-(b.repeat||0)||ts(a)-ts(b)}),key:avg(m[k].map(metric))})});
  groups.sort(function(a,b){return a.key-b.key||a.runs[0].title.localeCompare(b.runs[0].title)});
  groups.forEach(function(g){
   var r0=g.runs[0],sc=g.runs.map(function(r){return r.score}).filter(function(x){return x!=null}),mn=avg(g.runs.map(function(r){return r.minutes}).filter(function(x){return x!=null}));
   h+='<div class="group"><div class="ghead"><h2>'+esc(r0.title)+'</h2><span class="gs">'+g.runs.length+' run'+(g.runs.length>1?'s':'')+(sc.length?' · mean score <b>'+Math.round(avg(sc))+'</b>':'')+(mn!=null?' · mean '+mn.toFixed(1)+' min':'')+'</span></div><div class="chips">'+chipsFor(r0)+'</div></div><div class="grid">'+g.runs.map(function(r){return cardHtml(r,true)}).join('')+'</div>';
  });
 }else{
  list.sort(cmp);
  h='<div class="grid" style="margin-top:14px">'+list.map(function(r){return cardHtml(r,false)}).join('')+'</div>';
  groups=[];
  var seen={};list.forEach(function(r){seen[r.cfg]=1});groups={length:Object.keys(seen).length};
 }
 if(!list.length)h='<div class="empty">No runs match these filters.</div>';
 out.innerHTML=h;
 var ncfg=st.group?groups.length:groups.length;
 document.getElementById('count').textContent='Showing '+list.length+' of '+runs.length+' runs · '+ncfg+' configuration'+(ncfg===1?'':'s');
 var na=dims.reduce(function(n,d){return n+st.f[d.key].length},0);
 document.getElementById('nactive').textContent=na?'('+na+' active)':'';
 if(rebuildFilters)buildFilters();
 if(!skipHash)writeHash();
}
readHash();syncControls();
if(window.innerWidth<700)document.getElementById('filters').removeAttribute('open');
update(true,true);
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    sys.exit(main())
