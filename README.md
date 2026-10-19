# pi local-model bench

Run the [pi](https://pi.dev) coding agent against local models served by
llama.cpp, and compare models and settings on real agentic tasks. Each run is
scored automatically, screenshotted, and reviewed by Claude.

It includes:

- **`start-llama-router.sh` + `models.ini`:** a llama.cpp router setup with
  per-model presets (sampling, context) taken from each model's card. This is
  useful on its own for everyday pi use; see [MODEL-SETTINGS.md](MODEL-SETTINGS.md).
  pi doesn't send sampling parameters, so without presets you get the GGUF's
  or llama.cpp's defaults.
- **`bench/`:** a harness that runs pi headless in a fresh, isolated Docker
  container per run (no personal config), then scores the result, screenshots
  the site, and has Claude review the code and transcript. The output is a
  table plus an HTML gallery. Runs can use bare pi or a "batteries" profile
  with web search, a browser, code diagnostics and working-practices guidance,
  and local models or cloud models (via ClinePass). See
  [bench/README.md](bench/README.md).
- **`pi-extensions/bash-default-timeout.ts`:** gives pi's bash tool a default
  timeout, so a dev server started in the foreground can't hang a session.
  Copy it into `~/.pi/agent/extensions/` for everyday use.
- **`sync-pi-models.py`:** mirrors the router's models into
  `~/.pi/agent/models.json`, which pi's background subagents need.

The bundled tasks are "Create a static site for my personal blog using Astro
and populate it with 4-5 sample posts" and a Tetris browser game, which is
scored by playing it. Adding tasks is a directory with a prompt and a checker
script.

## Requirements

- An Apple Silicon Mac with 64 GB of RAM (the presets are sized for it; on
  smaller machines, pick smaller quants and contexts)
- [llama.cpp](https://github.com/ggml-org/llama.cpp) with router support
  (`brew install llama.cpp`; tested with 0.7.0)
- Docker Desktop
- Python 3.11+ (stdlib only)
- Optional, for downloads: the Hugging Face CLI (`brew install hf`)
- Optional, for code reviews: [Claude Code](https://claude.com/claude-code)
  (`claude`), logged in. Skip reviews with `--no-review`.

pi itself is only needed for interactive use; the harness runs a pinned pi
inside its container.

## Setup

Download the models used by `models.ini` and `bench/matrix.toml` (about 120 GB)
into `~/models`, or set `MODELS_DIR`:

```sh
mkdir -p ~/models && cd ~/models
hf download unsloth/Qwen3.6-35B-A3B-GGUF   Qwen3.6-35B-A3B-UD-Q6_K_XL.gguf    --local-dir .
hf download unsloth/Qwen3.8-27B-GGUF       Qwen3.8-27B-UD-Q6_K_XL.gguf        --local-dir .
hf download unsloth/gemma-4-26B-A4B-it-GGUF gemma-4-26B-A4B-it-UD-Q6_K_XL.gguf --local-dir .
hf download unsloth/Qwen3-Coder-Next-GGUF  Qwen3-Coder-Next-UD-IQ4_XS.gguf    --local-dir .

# Qwen3.8's MTP draft head, for the recommended preset. It goes next to the
# models folder, not in it, so the router doesn't list it as a chat model.
mkdir -p ../models-aux
hf download unsloth/Qwen3.8-27B-GGUF MTP/mtp-Qwen3.8-27B-Q4_0.gguf --local-dir /tmp/mtp
mv /tmp/mtp/MTP/mtp-Qwen3.8-27B-Q4_0.gguf ../models-aux/
```

Start the router and leave it running. It loads the recommended preset,
`qwen3.8-27b-budget4k-mtp` (Qwen3.8-27B with a 4K thinking cap and MTP), at
startup; remove `load-on-startup` from `models.ini` to start empty.

```sh
./start-llama-router.sh
```

For interactive use, run `pi`, then `/login llama.cpp` (URL
`http://127.0.0.1:8080`, no key), `/llama` to load a model, and `/model` to
select it.

## Benchmark

```sh
bench/bench.py run --task smoke --only "qwen3.6 coding" -n 1 --no-review   # 1-minute harness check
bench/bench.py run --only "· final ·"   # one experiment from matrix.toml (several hours)
bench/bench.py report               # table, plus bench/results/gallery.html
```

The first run builds three Docker images: the agent sandbox (pinned pi), the
batteries sandbox built on it, and a headless-Chromium screenshotter. Details, scoring, outputs and how to add models
or tasks are in [bench/README.md](bench/README.md).
