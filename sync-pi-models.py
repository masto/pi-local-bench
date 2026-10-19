#!/usr/bin/env python3
"""Mirror the llama.cpp router's models into ~/.pi/agent/models.json as a plain
OpenAI-compatible provider named "local".

pi's built-in llama.cpp provider isn't visible to background subagents (they run
in a separate process that can't load pi's hidden built-in extensions), so they
need the models declared here as well. Each model then shows up twice in /model:
llama.cpp/<id> (built-in) and local/<id> (this file); either works in the
foreground.

Run after changing models.ini or ~/models (with the router running):
  ./sync-pi-models.py
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

ROUTER = "http://127.0.0.1:8080"
PROVIDER = "local"
MODELS_JSON = Path.home() / ".pi" / "agent" / "models.json"
HEADER_BYTES = 32 << 20  # the chat template lives in the GGUF header


def arg(args, *flags):
    for i, a in enumerate(args[:-1]):
        if a in flags:
            return args[i + 1]
    return None


def supports_thinking(gguf_path):
    """Same test pi's llama.cpp extension uses: the chat template mentions enable_thinking."""
    try:
        with open(gguf_path, "rb") as f:
            return b"enable_thinking" in f.read(HEADER_BYTES)
    except OSError:
        return False


def main():
    with urllib.request.urlopen(ROUTER + "/models", timeout=10) as r:
        catalog = json.load(r)["data"]
    models = []
    for m in catalog:
        args = m["status"].get("args") or []
        path = arg(args, "--model", "-m")
        n_ctx = int(arg(args, "--ctx-size", "-c") or 131072)
        model = dict(id=m["id"], name=m["id"], input=["text"], contextWindow=n_ctx, maxTokens=n_ctx,
                     cost=dict(input=0, output=0, cacheRead=0, cacheWrite=0),
                     compat=dict(supportsStore=False, supportsDeveloperRole=False, supportsReasoningEffort=False,
                                 supportsUsageInStreaming=True, supportsStrictMode=False, maxTokensField="max_tokens"))
        model["reasoning"] = bool(path) and supports_thinking(path)
        if model["reasoning"]:
            # Mirrors pi's llama.cpp extension: thinking is on/off via the chat template.
            model["thinkingLevelMap"] = dict(off="off", minimal=None, low=None, medium="medium", high=None, xhigh=None)
            model["compat"]["thinkingFormat"] = "qwen-chat-template"
        models.append(model)

    config = json.loads(MODELS_JSON.read_text()) if MODELS_JSON.exists() else {}
    config.setdefault("providers", {})[PROVIDER] = dict(
        name="llama.cpp (static)", baseUrl=ROUTER + "/v1", api="openai-completions", apiKey="local", models=models)
    MODELS_JSON.write_text(json.dumps(config, indent=2) + "\n")
    for m in models:
        print(f"  {PROVIDER}/{m['id']:<34} ctx {m['contextWindow']:>7}  thinking {'yes' if m['reasoning'] else 'no'}")
    print(f"wrote {len(models)} models to {MODELS_JSON}")


if __name__ == "__main__":
    sys.exit(main())
