#!/usr/bin/env python3
"""Follow a pi session event log (pi --mode json output) in human-readable form.

  ./watch.py                     # newest run in results/, follow until it finishes
  ./watch.py --next              # ...then keep going with each new run (watch a whole batch)
  ./watch.py results/<run>       # a specific run (directory or events.jsonl)
  ./watch.py -n results/<run>    # print the whole log and exit, no following
  ./watch.py --thinking off      # hide thinking (or "tail" to show only the last lines of each block)

Thinking is dim, the model's text is plain, tool calls are bold with their key
argument, results are indented and truncated, errors are red, and pi-lens
STOP/warning lines are highlighted.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"

USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def style(code):
    return (lambda s: f"\033[{code}m{s}\033[0m") if USE_COLOR else (lambda s: s)


dim, bold, red, green, yellow, cyan, magenta = (style(c) for c in ("2", "1", "31", "32", "33", "36", "35"))


def out(s="", end="\n"):
    sys.stdout.write(s + end)
    sys.stdout.flush()


def short(text, width):
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 1] + "…"


def key_arg(name, args):
    """The one argument worth showing for a tool call."""
    args = args or {}
    for k in ("command", "path", "url", "query", "subject", "taskId", "agent"):
        if args.get(k):
            v = args[k]
            if k == "path" and name == "write":
                v = f"{v}  ({len(args.get('content') or '')} chars)"
            if k == "path" and name == "edit":
                v = f"{v}  ({len(args.get('edits') or []) or 1} edit(s))"
            return str(v)
    if name == "web_search" and args.get("queries"):
        return " | ".join(args["queries"])
    if name.startswith("agent_browser") and args.get("args"):
        parts = list(map(str, args["args"]))
        while "--session" in parts:  # long generated session ids hide the actual command
            i = parts.index("--session")
            del parts[i : i + 2]
        script = args.get("stdin") or args.get("code") or args.get("script")
        return " ".join(parts) + (f"  ⟨{' '.join(str(script).split())}⟩" if script else "")
    if name == "TaskUpdate":
        return " ".join(f"{k}={v}" for k, v in args.items() if k in ("taskId", "status"))
    return json.dumps(args)[:200]


class Renderer:
    def __init__(self, thinking, max_lines, width):
        self.thinking, self.max_lines, self.width = thinking, max_lines, width
        self.turn = 0
        self.mode = None  # "thinking" or "text" while a block is streaming
        self.think_buf = ""
        self.done = False
        self.live = False  # set once we've caught up with the file; only then are wall-clock times meaningful

    def close_block(self):
        if self.mode == "thinking" and self.thinking == "tail" and self.think_buf.strip():
            lines = [l for l in self.think_buf.strip().splitlines() if l.strip()]
            out(dim(f"  💭 …{len(self.think_buf):,} chars; last lines:"))
            for l in lines[-4:]:
                out(dim("     " + short(l, self.width)))
        elif self.mode:
            out()
        self.mode, self.think_buf = None, ""

    def stream(self, kind, delta):
        if kind != self.mode:
            self.close_block()
            self.mode = kind
            if kind == "thinking" and self.thinking == "full":
                out(dim("💭 "), end="")
            elif kind == "text":
                out(cyan("💬 "), end="")
        if kind == "thinking":
            if self.thinking == "full":
                out(dim(delta), end="")
            elif self.thinking == "tail":
                self.think_buf += delta
        else:
            out(delta, end="")

    def event(self, ev):
        t = ev.get("type")
        if t == "session":
            out(dim(f"session {ev.get('id', '')[:8]} in {ev.get('cwd')}"))
        elif t == "turn_start":
            self.close_block()
            self.turn += 1
            out(dim(f"── turn {self.turn} {f'{datetime.now():%H:%M:%S} ' if self.live else ''}" + "─" * 40))
        elif t == "message_update":
            a = ev.get("assistantMessageEvent") or {}
            if a.get("type") == "thinking_delta" and self.thinking != "off":
                self.stream("thinking", a.get("delta", ""))
            elif a.get("type") == "text_delta":
                self.stream("text", a.get("delta", ""))
        elif t == "message_end" and (ev.get("message") or {}).get("role") == "assistant":
            self.close_block()
            msg = ev["message"]
            u = msg.get("usage") or {}
            ctx = u.get("input", 0) + u.get("cacheRead", 0) + u.get("cacheWrite", 0)
            if msg.get("stopReason") == "error":
                out(red(f"✗ model error: {msg.get('errorMessage')}"))
            out(dim(f"   [context {ctx / 1000:.1f}k, +{u.get('output', 0)} out, stop: {msg.get('stopReason')}]"))
        elif t == "tool_execution_start":
            self.close_block()
            name = ev.get("toolName", "?")
            out(bold(f"▶ {name}") + "  " + short(key_arg(name, ev.get("args")), self.width))
        elif t == "tool_execution_end":
            self.tool_result(ev)
        elif t == "compaction_start":
            out(magenta(f"⟳ compaction ({ev.get('reason')})"))
        elif t == "auto_retry_start":
            out(yellow(f"↻ retry {ev.get('attempt')}/{ev.get('maxAttempts')}: {ev.get('errorMessage')}"))
        elif t == "agent_settled":
            self.close_block()
            out(bold(green("■ agent finished")) + (red(" (aborted)") if ev.get("aborted") else ""))
            self.done = True

    def tool_result(self, ev):
        text = "".join(c.get("text", "") for c in (ev.get("result") or {}).get("content", []) if c.get("type") == "text")
        lines = [l for l in text.rstrip().splitlines() if l.strip()]
        err = ev.get("isError")
        mark = red("  ✗") if err else green("  ✓")
        ms = ev.get("durationMs") or 0
        head = mark + (dim(f" {ms / 1000:.1f}s") if ms >= 1000 else "")
        # pi-lens appends its findings to write/edit results; always show those lines.
        lens = [l for l in lines if any(s in l for s in ("🔴", "🟡", "STOP", "Pi-lens", "warning(s)"))]
        body = lines if err else [l for l in lines if l not in lens]
        shown = body[: self.max_lines]
        out(head + (f" {short(shown[0], self.width)}" if shown else ""))
        for l in shown[1:]:
            out(("    " + (red if err else dim)(short(l, self.width))))
        if len(body) > len(shown):
            out(dim(f"    … {len(body) - len(shown)} more lines"))
        for l in lens[:6]:
            out("    " + (red if ("🔴" in l or "STOP" in l) else yellow)(short(l.strip(), self.width)))


def latest_run():
    runs = sorted((p for p in RESULTS.glob("*/events.jsonl")), key=lambda p: p.stat().st_mtime)
    return runs[-1] if runs else None


def resolve(target):
    if not target:
        return latest_run()
    p = Path(target)
    return p / "events.jsonl" if p.is_dir() else p


def follow(path, renderer, keep_following):
    """Render events as they're appended; returns when the agent finishes (or on EOF without following)."""
    buf = b""
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if chunk:
                buf += chunk
                *lines, buf = buf.split(b"\n")  # keep a partial last line for the next read
                for line in lines:
                    try:
                        renderer.event(json.loads(line))
                    except ValueError:
                        pass
                if renderer.done:
                    return
                continue
            if not keep_following:
                renderer.close_block()
                return
            renderer.live = True
            time.sleep(0.25)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("target", nargs="?", help="run directory or events.jsonl (default: newest run)")
    p.add_argument("-n", "--no-follow", action="store_true", help="print what's there and exit")
    p.add_argument("--next", action="store_true", help="after a run finishes, follow the next new run")
    p.add_argument("--thinking", choices=("full", "tail", "off"), default="full")
    p.add_argument("--lines", type=int, default=8, help="max result lines per tool call (default 8)")
    p.add_argument("--width", type=int, default=max(60, os.get_terminal_size().columns - 6) if sys.stdout.isatty() else 160)
    args = p.parse_args()

    path = resolve(args.target)
    while path is not None:
        if not path.exists():
            sys.exit(f"no such log: {path}")
        out(bold(f"═══ {path.parent.name} ═══"))
        renderer = Renderer(args.thinking, args.lines, args.width)
        try:
            follow(path, renderer, not args.no_follow)
        except KeyboardInterrupt:
            out()
            return
        if not (args.next and renderer.done):
            return
        out(dim("waiting for the next run…"))
        try:
            while (nxt := latest_run()) in (None, path):
                time.sleep(2)
        except KeyboardInterrupt:
            return
        path = nxt


if __name__ == "__main__":
    main()
