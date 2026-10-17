#!/usr/bin/env python3
"""Run pi benchmark tasks against local llama.cpp models, then score, screenshot
and review the results.

Each run gets a fresh Docker container (see Dockerfile): pi runs there with an
empty home and workspace, with skills, extensions, MCP, prompt templates and
context files disabled, talking to the host's llama.cpp router. After pi exits,
the task's checker is copied in and scores the workspace; the built site is then
screenshotted in a separate container (shot/), and Claude reviews the code.

  ./bench.py run                     # whole matrix.toml
  ./bench.py run --only gemma4 -n 1  # runs whose name contains "gemma4", once each
  ./bench.py run --task smoke --only "qwen3.6 coding" -n 1   # quick harness check
  ./bench.py report                  # table + results/gallery.html
  ./bench.py rescore|shots|review    # redo one stage for existing results
"""

import argparse
import concurrent.futures
import html
import json
import os
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

BENCH = Path(__file__).resolve().parent
RESULTS = BENCH / "results"
PI_VERSION = "1.3.0"
IMAGE = f"pi-bench:{PI_VERSION}"
SHOT_IMAGE = "pi-bench-shot:1"
REVIEW_MODEL = "claude-opus-5-5"
# The reviewer starts in this empty directory and gets the run folder via
# --add-dir, so nothing the agent wrote (e.g. a .claude/ with hooks) is loaded
# as project configuration.
REVIEW_CWD = BENCH / ".review-cwd"

# Everything pi would normally discover is disabled, including the built-in
# llama.cpp extension: headless pi never refreshes that extension's catalog from
# the server, so the model under test is declared in models.json instead.
PI_ISOLATION = ["-ne", "--no-mcp", "-ns", "-nc", "-np", "--no-themes", "-na"]
PROVIDER = "local"


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# --------------------------------------------------------------------- router


class Router:
    def __init__(self, url):
        self.url = url.rstrip("/")

    def _req(self, path, body=None, timeout=30):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    def models(self):
        return {m["id"]: m for m in self._req("/models")["data"]}

    def status(self, model_id):
        return self.models()[model_id]["status"]

    def props(self, model_id):
        return self._req("/props?model=" + urllib.parse.quote(model_id))

    def wait(self, model_id, want, timeout_s=600):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            st = self.status(model_id)
            if st["value"] == want:
                return
            if st.get("failed"):
                raise RuntimeError(f"{model_id} failed to {want[:-2]}: {st}")
            time.sleep(2)
        raise TimeoutError(f"{model_id} not {want} after {timeout_s}s")

    def ensure_only(self, model_id):
        """Unload every other model, then load model_id (variants of one file can't share memory)."""
        for mid, m in self.models().items():
            if mid != model_id and m["status"]["value"] != "unloaded":
                log(f"unloading {mid}")
                self._req("/models/unload", {"model": mid})
                self.wait(mid, "unloaded")
        if self.status(model_id)["value"] != "loaded":
            log(f"loading {model_id}")
            t0 = time.time()
            self._req("/models/load", {"model": model_id})
            self.wait(model_id, "loaded")
            log(f"loaded {model_id} in {time.time() - t0:.0f}s")


# -------------------------------------------------------------------- helpers


def docker(*args, **kw):
    return subprocess.run(["docker", *args], **kw)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def update_result(out, **fields):
    result = read_json(out / "result.json", {})
    result.update(fields)
    (out / "result.json").write_text(json.dumps(result, indent=2))
    return result


def export_dir(container, src, dest):
    """Copy a directory out of a container, keeping only plain files and dirs.

    The contents are model-generated, so links are dropped and paths are
    confined to dest (tarfile's "data" filter).
    """
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tempfile.TemporaryFile() as tmp:
        docker("exec", container, "tar", "-C", src, "--exclude=node_modules", "--exclude=.astro", "-cf", "-", ".",
               stdout=tmp, stderr=subprocess.DEVNULL)
        tmp.seek(0)
        try:
            with tarfile.open(fileobj=tmp) as tf:
                tf.extractall(dest, members=[m for m in tf.getmembers() if m.isfile() or m.isdir()], filter="data")
        except tarfile.TarError:
            pass  # empty or missing source


def import_dir(container, src, dest):
    """Copy a host directory into a container (without macOS ._ metadata files)."""
    pack = subprocess.Popen(["tar", "-C", str(src), "--no-mac-metadata", "--no-xattrs", "-cf", "-", "."],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env={**os.environ, "COPYFILE_DISABLE": "1"})
    docker("exec", "-i", container, "tar", "--warning=no-unknown-keyword", "-xf", "-", "-C", dest,
           stdin=pack.stdout, check=True)
    pack.wait()


def pi_models_json(model_id, props, container_url):
    """Declare the model the way pi's llama.cpp extension would (see its provider.js toPiModel)."""
    n_ctx = props["default_generation_settings"]["n_ctx"]
    reasoning = "enable_thinking" in (props.get("chat_template") or "")
    model = dict(id=model_id, name=model_id, reasoning=reasoning, input=["text"], contextWindow=n_ctx, maxTokens=n_ctx,
                 cost=dict(input=0, output=0, cacheRead=0, cacheWrite=0),
                 compat=dict(supportsStore=False, supportsDeveloperRole=False, supportsReasoningEffort=False,
                             supportsUsageInStreaming=True, supportsStrictMode=False, maxTokensField="max_tokens"))
    if reasoning:
        model["thinkingLevelMap"] = dict(off="off", minimal=None, low=None, medium="medium", high=None, xhigh=None)
        model["compat"]["thinkingFormat"] = "qwen-chat-template"
    return {"providers": {PROVIDER: dict(baseUrl=container_url + "/v1", api="openai-completions", apiKey="local",
                                         models=[model])}}


def load_task(name):
    task = tomllib.loads((BENCH / "tasks" / name / "task.toml").read_text())
    task["dir"] = BENCH / "tasks" / name
    return task


# --------------------------------------------------------------- run analysis


def read_events(path):
    for line in path.read_bytes().split(b"\n"):
        try:
            yield json.loads(line)
        except ValueError:
            continue


def summarize_events(path):
    """Pull agent-behaviour metrics out of pi's --mode json event stream."""
    m = dict(turns=0, model_calls=0, tool_calls=0, tool_errors=0, tools={}, output_tokens=0, peak_context=0,
             thinking_chars=0, compactions=0, retries=0, provider_errors=[], aborted=None, final_text="")
    for ev in read_events(path):
        t = ev.get("type")
        if t == "turn_end":
            m["turns"] += 1
        elif t == "tool_execution_end":
            m["tool_calls"] += 1
            m["tool_errors"] += bool(ev.get("isError"))
            m["tools"][ev.get("toolName")] = m["tools"].get(ev.get("toolName"), 0) + 1
        elif t == "compaction_start":
            m["compactions"] += 1
        elif t == "auto_retry_start":
            m["retries"] += 1
        elif t == "agent_settled":
            m["aborted"] = ev.get("aborted")
        elif t == "message_end" and ev.get("message", {}).get("role") == "assistant":
            msg = ev["message"]
            m["model_calls"] += 1
            u = msg.get("usage") or {}
            m["output_tokens"] += u.get("output", 0)
            m["peak_context"] = max(m["peak_context"], u.get("input", 0) + u.get("cacheRead", 0) + u.get("cacheWrite", 0))
            if msg.get("stopReason") == "error":
                m["provider_errors"].append(msg.get("errorMessage"))
            texts = []
            for block in msg.get("content") or []:
                if block.get("type") == "thinking":
                    m["thinking_chars"] += len(block.get("thinking", ""))
                elif block.get("type") == "text":
                    texts.append(block.get("text", ""))
            if texts:
                m["final_text"] = "\n".join(texts)[-2000:]
    return m


def clip(text, limit):
    text = text.strip()
    return text if len(text) <= limit else f"{text[:limit]}… [{len(text) - limit} more chars]"


def write_transcript(out):
    """A condensed, readable session log (for the reviewer and for humans)."""
    lines = []
    for ev in read_events(out / "events.jsonl"):
        msg = ev.get("message") or {}
        if ev.get("type") == "message_end" and msg.get("role") == "user":
            lines.append("## User\n\n" + "".join(b.get("text", "") for b in msg.get("content", [])) + "\n")
        elif ev.get("type") == "message_end" and msg.get("role") == "assistant":
            for b in msg.get("content") or []:
                if b.get("type") == "thinking" and b.get("thinking", "").strip():
                    lines.append(f"> **thinking:** {clip(b['thinking'], 600)}\n".replace("\n", "\n> ") + "\n")
                elif b.get("type") == "text" and b.get("text", "").strip():
                    lines.append(f"**assistant:** {b['text'].strip()}\n")
                elif b.get("type") == "toolCall":
                    args = b.get("arguments") or {}
                    shown = args.get("command") or args.get("path") or json.dumps(args)
                    lines.append(f"**→ {b.get('name')}:** `{clip(str(shown), 400)}`\n")
        elif ev.get("type") == "tool_execution_end":
            text = "".join(c.get("text", "") for c in (ev.get("result") or {}).get("content", []))
            flag = " (ERROR)" if ev.get("isError") else ""
            lines.append(f"<details><summary>{ev.get('toolName')} result{flag}</summary>\n\n```\n"
                         f"{clip(text, 1500)}\n```\n</details>\n")
    (out / "transcript.md").write_text("\n".join(lines))


# ------------------------------------------------------------------- stages


def score_in_container(container, out, task):
    """Run the task's checker in the container; export the built site if there is one."""
    docker("cp", str(task["dir"] / task["check"]), f"{container}:/tmp/check.mjs", check=True)
    chk = docker("exec", container, "node", "/tmp/check.mjs", "/workspace", capture_output=True, text=True)
    (out / "check-stderr.log").write_text(chk.stderr)
    try:
        check = json.loads(chk.stdout)
    except ValueError:
        check = dict(score=0, max=100, error="checker produced no JSON", stdout=chk.stdout[-2000:])
    (out / "check.json").write_text(json.dumps(check, indent=2))
    checks = {c["id"]: c["points"] for c in check.get("checks", [])}
    if check.get("projectDir") and checks.get("build"):
        export_dir(container, check["projectDir"] + "/dist", out / "site")
    elif (out / "site").exists():
        shutil.rmtree(out / "site")
    return update_result(out, score=check.get("score", 0), checks=checks)


def shoot(out):
    """Screenshot the exported site and record link/contrast/text facts."""
    shots = out / "shots"
    if shots.exists():
        shutil.rmtree(shots)
    if not (out / "site").is_dir():
        return update_result(out, shots=None)
    shots.mkdir()
    shots.chmod(0o777)  # written by the container's unprivileged user
    r = docker("run", "--rm", "-v", f"{out / 'site'}:/site:ro", "-v", f"{shots}:/shots", SHOT_IMAGE,
               "node", "/opt/shot/shot.mjs", "/site", "/shots", capture_output=True, text=True, timeout=300)
    data = read_json(shots / "shots.json")
    if not data:
        (shots / "error.log").write_text(r.stdout + r.stderr)
        return update_result(out, shots=None)
    pages = {p["file"]: p for p in data["pages"]}
    post = pages.get("post-desktop.png", {})
    return update_result(
        out,
        shots=list(pages),
        broken_links=len(data["links"]["broken"]),
        contrast_failures=sum(p.get("contrastFailures") or 0 for f, p in pages.items() if f != "home-mobile.png"),
        post_text_chars=post.get("textLength"),
    )


REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"enum": ["clean", "minor_issues", "significant_issues", "broken"]},
        "idiomatic": {"type": "integer", "minimum": 1, "maximum": 5,
                      "description": "How conventional the code is for the installed framework version (5 = what an expert would write)"},
        "quality": {"type": "integer", "minimum": 1, "maximum": 5, "description": "Overall code quality"},
        "summary": {"type": "string", "description": "At most 3 sentences"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"enum": ["high", "medium", "low"]},
                    "kind": {"enum": ["unnecessary_workaround", "outdated_api", "bug", "overengineering",
                                      "dead_code", "false_claim", "risky_action", "other"]},
                    "file": {"type": "string"},
                    "issue": {"type": "string"},
                },
                "required": ["severity", "kind", "file", "issue"],
            },
        },
        "process_notes": {"type": "string", "description": "Notable agent behaviour from the transcript, or empty"},
    },
    "required": ["verdict", "idiomatic", "quality", "summary", "findings", "process_notes"],
}

REVIEW_PROMPT = """\
You are reviewing work by a local LLM coding agent (model: {model}, thinking: {thinking}). It was given \
exactly this request, in an empty directory, with no further input:

> {prompt}

Everything is under {out} (read-only):
- workspace/ - what it produced (node_modules removed)
- transcript.md - condensed session log: its reasoning, commands, and their output
- check.json - automated checks from a clean install and build
- shots/shots.json - facts from the rendered site (broken links, color-contrast failures, text per page), if it built

Review it the way a senior developer would review a PR titled "{prompt}". Report what an expert would find \
strange or wrong, in particular:
- hand-rolled workarounds for problems that don't exist or that the framework already solves;
- outdated, mixed-version or invented APIs; code that can't work; dead code; leftover debug or scratch files;
- in the transcript: false claims about the tooling, loops, abandoned approaches left behind, risky commands.

Check the framework version in package.json and consult the official docs (WebFetch is allowed for \
docs.astro.build) before calling an API missing, deprecated or non-standard; do not flag current official \
APIs. Ignore visual design taste. Don't restate the automated checks except to explain the cause of a \
failure. Be specific (file paths, what's wrong, what the idiomatic version is) and concise.
"""


def review(out):
    result = read_json(out / "result.json", {})
    task = load_task(result["task"])
    prompt = REVIEW_PROMPT.format(model=result["model"], thinking=result["thinking"], prompt=task["prompt"], out=out)
    REVIEW_CWD.mkdir(exist_ok=True)
    cmd = ["claude", "-p", prompt, "--model", REVIEW_MODEL, "--output-format", "json",
           "--json-schema", json.dumps(REVIEW_SCHEMA), "--add-dir", str(out),
           "--tools", "Read,Grep,Glob,WebFetch", "--allowedTools", "Read", "Grep", "Glob", "WebFetch(domain:docs.astro.build)",
           "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence", "--setting-sources", ""]
    try:
        r = subprocess.run(cmd, cwd=REVIEW_CWD, capture_output=True, text=True, timeout=1800)
        raw = json.loads(r.stdout)
    except (subprocess.TimeoutExpired, ValueError) as e:
        (out / "review-error.log").write_text(repr(e))
        return update_result(out, review=None)
    (out / "review-raw.json").write_text(json.dumps(raw, indent=2))
    rev = raw.get("structured_output")
    if not rev:
        return update_result(out, review=None)
    (out / "review.json").write_text(json.dumps(rev, indent=2))
    return update_result(out, review=dict(verdict=rev["verdict"], idiomatic=rev["idiomatic"], quality=rev["quality"],
                                          findings=len(rev["findings"]), cost_usd=raw.get("total_cost_usd")))


def run_one(router, cfg, task, rep, container_url):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = "".join(c if c.isalnum() or c in ".-" else "-" for c in cfg["name"]).strip("-")
    out = RESULTS / f"{stamp}_{slug}_r{rep}"
    out.mkdir(parents=True)
    container = f"pi-bench-{stamp}-{rep}"
    timeout_s = int(cfg["timeout_min"] * 60)
    props = router.props(cfg["model"])
    gen = props.get("default_generation_settings", {})
    models_json = pi_models_json(cfg["model"], props, container_url)
    update_result(out, name=cfg["name"], model=cfg["model"], thinking=cfg["thinking"], task=cfg["task"], repeat=rep,
                  started=datetime.now().isoformat(timespec="seconds"), pi_version=PI_VERSION,
                  reasoning=models_json["providers"][PROVIDER]["models"][0]["reasoning"],
                  server=dict(n_ctx=gen.get("n_ctx"), build=props.get("build_info"),
                              sampling={k: gen.get("params", {}).get(k) for k in
                                        ("temperature", "top_k", "top_p", "min_p", "presence_penalty", "repeat_penalty")}))
    log(f"▶ {cfg['name']} #{rep} → {out.relative_to(BENCH)}")

    docker("run", "-d", "--rm", "--name", container, "--memory", "8g", IMAGE, check=True, stdout=subprocess.DEVNULL)
    try:
        docker("exec", "-i", container, "sh", "-c", "mkdir -p ~/.pi/agent && cat > ~/.pi/agent/models.json",
               input=json.dumps(models_json).encode(), check=True)
        cmd = ["timeout", "-k", "30", str(timeout_s), "pi", "--mode", "json", "--provider", PROVIDER,
               "--model", cfg["model"], "--thinking", cfg["thinking"], *PI_ISOLATION,
               "--session-dir", "/home/node/sessions", "--", task["prompt"]]
        t0 = time.time()
        with open(out / "events.jsonl", "wb") as ev, open(out / "pi-stderr.log", "wb") as err:
            rc = docker("exec", container, *cmd, stdout=ev, stderr=err).returncode
        elapsed = round(time.time() - t0, 1)
        update_result(out, agent_seconds=elapsed, agent_exit=rc, timed_out=rc in (124, 137))
        log(f"  agent finished in {elapsed / 60:.1f} min (exit {rc}{', TIMEOUT' if rc in (124, 137) else ''})")

        # Keep the transcript and the agent's files before scoring touches anything.
        docker("exec", container, "sh", "-c",
               "f=$(find /home/node/sessions -name '*.jsonl' | head -1); [ -n \"$f\" ] && cp \"$f\" /tmp/session.jsonl "
               "&& pi --export /tmp/session.jsonl /tmp/session.html", capture_output=True)
        for name in ("session.jsonl", "session.html"):
            docker("cp", f"{container}:/tmp/{name}", str(out / name), capture_output=True)
        export_dir(container, "/workspace", out / "workspace")

        log("  scoring")
        score_in_container(container, out, task)
    finally:
        docker("rm", "-f", container, capture_output=True)

    write_transcript(out)
    log("  screenshots")
    shoot(out)
    result = update_result(out, **summarize_events(out / "events.jsonl"))
    log(f"  score {result['score']}/100, {result['turns']} turns, {result['tool_calls']} tool calls "
        f"({result['tool_errors']} errors), {result['output_tokens']} output tokens"
        + (f", {result.get('broken_links')} broken links, {result.get('contrast_failures')} contrast failures"
           if result.get("shots") else ", no site to screenshot"))
    return out


def rescore(out):
    """Re-run the checker on a run's saved workspace in a fresh container."""
    result = read_json(out / "result.json", {})
    container = f"pi-bench-rescore-{out.name[:15]}"
    docker("run", "-d", "--rm", "--name", container, "--memory", "8g", IMAGE, check=True, stdout=subprocess.DEVNULL)
    try:
        import_dir(container, out / "workspace", "/workspace")
        return score_in_container(container, out, load_task(result["task"]))
    finally:
        docker("rm", "-f", container, capture_output=True)


# -------------------------------------------------------------------- commands


def preflight():
    if docker("info", capture_output=True).returncode != 0:
        sys.exit("Docker isn't running (start Docker Desktop).")
    for image, context in ((IMAGE, BENCH), (SHOT_IMAGE, BENCH / "shot")):
        if docker("image", "inspect", image, capture_output=True).returncode != 0:
            log(f"building {image}")
            docker("build", "-t", image, "--build-arg", f"PI_VERSION={PI_VERSION}", str(context), check=True)


def selected_runs(args):
    runs = sorted(p.parent for p in RESULTS.glob("*/result.json"))
    if args.only:
        wanted = [w.strip().lower() for w in args.only.split(",")]
        runs = [r for r in runs if any(w in r.name.lower() for w in wanted)]
    return runs


def start_review(pool, out):
    def job():
        rev = review(out).get("review")
        log(f"  review of {out.name}: {rev['verdict'] if rev else 'FAILED'}")
    return pool.submit(job)


def cmd_run(args):
    matrix = tomllib.loads(Path(args.matrix).read_text())
    defaults = matrix.get("defaults", {})
    runs = [{**defaults, **r} for r in matrix["run"]]
    if args.only:
        wanted = [w.strip().lower() for w in args.only.split(",")]
        runs = [r for r in runs if any(w in r["name"].lower() for w in wanted)]
    for r in runs:
        if args.repeats:
            r["repeats"] = args.repeats
        if args.task:
            r["task"] = args.task
    if not runs:
        sys.exit("No runs selected.")

    preflight()
    router = Router(args.router)
    try:
        available = router.models()
    except OSError as e:
        sys.exit(f"Can't reach the llama.cpp router at {router.url} ({e}). Start ../start-llama-router.sh.")
    missing = sorted({r["model"] for r in runs} - available.keys())
    if missing:
        sys.exit(f"Router doesn't list {missing}. Restart it so it rereads ../models.ini and ~/models.")
    container_url = args.router.replace("127.0.0.1", "host.docker.internal").replace("localhost", "host.docker.internal")

    total = sum(r["repeats"] for r in runs)
    log(f"{total} runs: " + ", ".join(f"{r['name']}×{r['repeats']}" for r in runs))
    done = 0
    # Reviews run on Claude in the cloud, so they overlap with the next local run.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        reviews = []
        for cfg in runs:
            task = load_task(cfg["task"])
            router.ensure_only(cfg["model"])
            for rep in range(1, cfg["repeats"] + 1):
                done += 1
                log(f"[{done}/{total}]")
                try:
                    out = run_one(router, cfg, task, rep, container_url)
                    if not args.no_review:
                        reviews.append(start_review(pool, out))
                except Exception as e:  # keep going; one broken run shouldn't sink an overnight batch
                    log(f"  run failed: {e!r}")
        if reviews:
            log(f"waiting for {sum(not f.done() for f in reviews)} reviews")
    cmd_report(args)


def cmd_stage(args):
    preflight()
    runs = selected_runs(args)
    if args.cmd == "review":
        runs = [r for r in runs if args.redo or not (r / "review.json").exists()]
        log(f"reviewing {len(runs)} runs")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            for out in runs:
                start_review(pool, out)
        return
    for out in runs:
        if args.cmd == "rescore":
            r = rescore(out)
            log(f"{out.name}: {r['score']}/100")
        if args.cmd in ("rescore", "shots"):
            r = shoot(out)
            log(f"{out.name}: shots {'ok' if r.get('shots') else 'none'}")
        write_transcript(out)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def fmt(x, spec=".0f", none="–"):
    return none if x is None else format(x, spec)


def cmd_report(args):
    runs = [read_json(p) | {"dir": p.parent.name} for p in sorted(RESULTS.glob("*/result.json"))]
    runs = [r for r in runs if "score" in r]
    if not runs:
        sys.exit("No results yet.")
    groups = {}
    for r in runs:
        groups.setdefault((r["task"], r["name"]), []).append(r)
    order = sorted(groups.items(), key=lambda kv: -mean([r["score"] for r in kv[1]]))

    lines = ["| task | config | n | score | min–max | posts render | review | broken links | contrast fails | minutes | turns | tool err |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for (task, name), rs in order:
        scores = [r["score"] for r in rs]
        verdicts = [r["review"]["verdict"] for r in rs if r.get("review")]
        lines.append(
            f"| {task} | {name} | {len(rs)} | **{mean(scores):.0f}** | {min(scores):.0f}–{max(scores):.0f} "
            f"| {sum(r.get('checks', {}).get('post_rendered', 0) > 0 for r in rs)}/{len(rs)} "
            f"| {', '.join(sorted(set(verdicts))) or '–'} "
            f"| {fmt(mean([r.get('broken_links') for r in rs]), '.1f')} "
            f"| {fmt(mean([r.get('contrast_failures') for r in rs]), '.0f')} "
            f"| {mean([r.get('agent_seconds', 0) for r in rs]) / 60:.1f} "
            f"| {mean([r.get('turns', 0) for r in rs]):.0f} "
            f"| {mean([r.get('tool_errors', 0) for r in rs]):.0f} |")
    table = "\n".join(lines)
    (RESULTS / "report.md").write_text(f"# Benchmark results ({datetime.now():%Y-%m-%d %H:%M})\n\n{table}\n")
    write_gallery(order)
    print(table)
    print(f"\nGallery: {RESULTS / 'gallery.html'}")


GALLERY_CSS = """
:root { --bg:#f6f6f4; --card:#fff; --text:#1d1d1b; --muted:#6b6b66; --line:#deded8;
        --good:#1f7a3a; --warn:#9a6500; --bad:#b3261e; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#161615; --card:#21211f; --text:#ecece8; --muted:#a3a39c; --line:#3a3a36;
          --good:#6fcf8a; --warn:#e8b45a; --bad:#f2827a; } }
* { box-sizing: border-box; }
body { margin:0; padding:24px 16px 64px; background:var(--bg); color:var(--text);
       font:15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
main { max-width:1400px; margin:0 auto; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:17px; margin:36px 0 12px; }
.muted { color:var(--muted); }
.run { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin-bottom:16px; }
.head { display:flex; flex-wrap:wrap; gap:8px 16px; align-items:baseline; margin-bottom:10px; }
.score { font-size:20px; font-weight:650; font-variant-numeric:tabular-nums; }
.badge { font-size:12px; font-weight:600; padding:2px 8px; border-radius:99px; border:1px solid currentColor; }
.clean { color:var(--good); } .minor_issues { color:var(--warn); }
.significant_issues, .broken { color:var(--bad); }
.shots { display:grid; grid-template-columns: 3fr 3fr 1.2fr; gap:10px; margin:10px 0; }
.shots a { display:block; border:1px solid var(--line); border-radius:6px; overflow:hidden; background:var(--bg); }
.shots img { display:block; width:100%; height:300px; object-fit:cover; object-position:top; }
.shots .cap { font-size:12px; color:var(--muted); padding:4px 8px; }
.shots .missing { display:flex; align-items:center; justify-content:center; min-height:120px; font-size:13px;
                  color:var(--muted); border:1px dashed var(--line); border-radius:6px; }
details summary { cursor:pointer; margin-top:6px; }
.facts { font-size:13px; color:var(--muted); }
.facts b { color:var(--text); font-weight:600; }
ul.findings { margin:8px 0 0; padding-left:18px; font-size:14px; }
ul.findings li { margin:3px 0; } code { font-size:12.5px; }
.links a { margin-right:12px; font-size:13px; }
@media (max-width: 760px) { .shots { grid-template-columns: 1fr 1fr; } .shots img { height:200px; } }
"""


SEVERITY_CLASS = {"high": "broken", "medium": "minor_issues", "low": "clean"}


def write_gallery(order):
    e = html.escape
    parts = [f"<!doctype html><html lang=en><head><meta charset=utf-8>"
             f"<meta name=viewport content='width=device-width, initial-scale=1'><title>Benchmark Gallery</title>"
             f"<style>{GALLERY_CSS}</style></head><body><main>"
             f"<h1>Benchmark gallery</h1><p class=muted>Generated {datetime.now():%Y-%m-%d %H:%M}. "
             f"Configs sorted by mean score; click a screenshot for full size.</p>"]
    for (task, name), rs in order:
        parts.append(f"<h2>{e(name)} <span class=muted>· {e(task)} · mean {mean([r['score'] for r in rs]):.0f}</span></h2>")
        for r in sorted(rs, key=lambda r: r["dir"]):
            d = r["dir"]
            rev = read_json(RESULTS / d / "review.json")
            verdict = (r.get("review") or {}).get("verdict")
            parts.append(f"<section class=run><div class=head><span class=score>{r['score']:.0f}</span>"
                         f"<span>run {r.get('repeat')}</span>"
                         + (f"<span class='badge {e(verdict)}'>{e(verdict.replace('_', ' '))}</span>" if verdict else "")
                         + f"<span class=muted>{e(d)}</span></div>")
            shots = r.get("shots") or []
            if shots:
                parts.append("<div class=shots>")
                for f, cap in (("home-desktop.png", "home"), ("post-desktop.png", "post"), ("home-mobile.png", "phone")):
                    if f in shots:
                        parts.append(f"<a href='{d}/shots/{f}'><img loading=lazy src='{d}/shots/{f}' alt='{cap}'>"
                                     f"<div class=cap>{cap}</div></a>")
                    else:
                        parts.append(f"<div class=missing>no {cap} page found</div>")
                parts.append("</div>")
            else:
                parts.append("<p class=muted>No site was built, so no screenshots.</p>")
            failed = [k for k, v in (r.get("checks") or {}).items() if not v]
            parts.append(
                f"<div class=facts><b>{(r.get('agent_seconds') or 0) / 60:.1f}</b> min · <b>{r.get('turns')}</b> turns · "
                f"<b>{r.get('tool_errors')}</b>/{r.get('tool_calls')} tool errors · "
                f"broken links <b>{fmt(r.get('broken_links'))}</b> · contrast failures <b>{fmt(r.get('contrast_failures'))}</b>"
                + (f" · failed checks: <b>{e(', '.join(failed))}</b>" if failed else "")
                + (" · <b>timed out</b>" if r.get("timed_out") else "") + "</div>")
            if rev:
                parts.append(f"<p>{e(rev['summary'])}</p>")
                def finding_list(fs):
                    return "<ul class=findings>" + "".join(
                        f"<li><span class='badge {SEVERITY_CLASS[f['severity']]}'>{e(f['severity'])}</span> "
                        f"<code>{e(f['file'])}</code> {e(f['issue'])}</li>" for f in fs) + "</ul>"
                major = [f for f in rev["findings"] if f["severity"] == "high"] + \
                        [f for f in rev["findings"] if f["severity"] == "medium"]
                minor = [f for f in rev["findings"] if f["severity"] == "low"]
                if major:
                    parts.append(finding_list(major))
                if minor:
                    parts.append(f"<details><summary class=facts>{len(minor)} low-severity findings</summary>"
                                 f"{finding_list(minor)}</details>")
                if rev.get("process_notes"):
                    parts.append(f"<p class=facts><b>Process:</b> {e(rev['process_notes'])}</p>")
            parts.append(f"<div class=links><a href='{d}/session.html'>session</a><a href='{d}/transcript.md'>transcript</a>"
                         f"<a href='{d}/workspace/'>files</a><a href='{d}/check.json'>checks</a>"
                         + (f"<a href='{d}/review.json'>review</a>" if rev else "") + "</div></section>")
    parts.append("</main></body></html>")
    (RESULTS / "gallery.html").write_text("".join(parts))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the matrix")
    r.add_argument("--matrix", default=str(BENCH / "matrix.toml"))
    r.add_argument("--only", help="comma-separated substrings of run names")
    r.add_argument("-n", "--repeats", type=int, help="override repeats for every run")
    r.add_argument("--task", help="override the task for every run (a directory under tasks/)")
    r.add_argument("--router", default="http://127.0.0.1:8080")
    r.add_argument("--no-review", action="store_true", help="skip the Claude code review")
    r.set_defaults(func=cmd_run)
    for name, desc in (("rescore", "re-run the checker (and screenshots) on saved workspaces"),
                       ("shots", "redo screenshots"), ("review", "review runs that don't have one yet")):
        s = sub.add_parser(name, help=desc)
        s.add_argument("--only", help="comma-separated substrings of result directory names")
        if name == "review":
            s.add_argument("--redo", action="store_true", help="also re-review runs that already have a review")
        s.set_defaults(func=cmd_stage)
    sub.add_parser("report", help="table + results/gallery.html").set_defaults(func=cmd_report)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
