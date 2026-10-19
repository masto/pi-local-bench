#!/usr/bin/env python3
"""Compare generation speed of llama.cpp router presets on the same request.

  ./speedtest.py qwen3.8-27b-budget4k qwen3.8-27b-budget4k-mtp
  ./speedtest.py -n 3 --max-tokens 1500 <preset> [<preset> ...]

Loads each preset in turn (unloading the others), sends the same code-writing
prompt with thinking off, and reports prompt and generation tokens/s, plus the
speculative-decoding acceptance rate when the server reports one.
"""

import argparse
import json
import statistics
import sys
import time
import urllib.request

PROMPT = ("Write a complete, self-contained JavaScript module implementing a Tetris game engine: "
          "the seven tetrominoes with all rotation states, a 10x20 board, collision detection, "
          "wall kicks, line clearing with scoring, levels and gravity speed, and a hard drop. "
          "Output only the code.")


def req(router, path, body=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(router + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.load(resp)


def status(router, model):
    return {m["id"]: m["status"]["value"] for m in req(router, "/models")["data"]}[model]


def load_only(router, model):
    for mid, st in {m["id"]: m["status"]["value"] for m in req(router, "/models")["data"]}.items():
        if mid != model and st != "unloaded":
            req(router, "/models/unload", {"model": mid})
            while status(router, mid) != "unloaded":
                time.sleep(1)
    if status(router, model) != "loaded":
        req(router, "/models/load", {"model": model})
        while (s := status(router, model)) != "loaded":
            if s == "failed":
                sys.exit(f"{model} failed to load")
            time.sleep(2)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("presets", nargs="+")
    p.add_argument("-n", "--trials", type=int, default=2)
    p.add_argument("--max-tokens", type=int, default=1200)
    p.add_argument("--router", default="http://127.0.0.1:8080")
    args = p.parse_args()

    for model in args.presets:
        print(f"loading {model}…", flush=True)
        load_only(args.router, model)
        gen, pp, acc = [], [], []
        for i in range(args.trials):
            r = req(args.router, "/v1/chat/completions", {
                "model": model, "messages": [{"role": "user", "content": PROMPT}],
                "max_tokens": args.max_tokens, "chat_template_kwargs": {"enable_thinking": False},
                "cache_prompt": False})
            t = r.get("timings", {})
            gen.append(t.get("predicted_per_second", 0))
            pp.append(t.get("prompt_per_second", 0))
            if t.get("draft_n"):
                acc.append(t.get("draft_n_accepted", 0) / t["draft_n"])
            print(f"  trial {i + 1}: {t.get('predicted_n')} tokens at {gen[-1]:.1f} tok/s"
                  + (f", draft acceptance {acc[-1]:.0%}" if t.get("draft_n") else ""), flush=True)
        print(f"{model}: generation {statistics.mean(gen):.1f} tok/s, prompt {statistics.mean(pp):.0f} tok/s"
              + (f", draft acceptance {statistics.mean(acc):.0%}" if acc else "") + "\n", flush=True)


if __name__ == "__main__":
    main()
