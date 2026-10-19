# Recommended settings for the local models

These are applied by `models.ini` (llama.cpp router presets). Restart
`./start-llama-router.sh` after editing, then open `/llama` once in pi so it
re-reads each model's context size.

## Where sampling actually gets decided

pi does **not** send temperature/top-k/etc. to llama.cpp; it only toggles
thinking on/off. So the server decides, in this order:

1. `models.ini` values for that model (what we set),
2. defaults embedded in the GGUF (`general.sampling.*`; the unsloth files carry
   temp/top-k/top-p, usually the vendor's "general" values),
3. llama.cpp's built-in defaults (notably `min-p 0.05`, which neither Qwen nor
   Google recommend).

Check what a loaded model is really using:

```sh
curl -s 'http://127.0.0.1:8080/props?model=Qwen3.6-35B-A3B-UD-Q6_K_XL' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin)["default_generation_settings"]; print(d["n_ctx"], {k: d["params"][k] for k in ("temperature","top_k","top_p","min_p","presence_penalty")})'
```

(Alternative: pi's own `~/.pi/agent/models.json` supports `samplingParams` and
`samplingParamsByThinkingLevel` per model, merged into every request. Handy if
you want different sampling for thinking on vs. off; we haven't needed it.)

## Per model

| Model (id in pi) | Size | Sampling | Context | Thinking | Source |
|---|---|---|---|---|---|
| `Qwen3.6-35B-A3B-UD-Q6_K_XL` | 30G | temp 0.6, top-p 0.95, top-k 20, min-p 0, presence 0 | 128K | on by default (`/thinking off` to disable) | Qwen card: "thinking mode, precise coding (e.g. WebDev)" |
| `qwen3.6-35b-general` | same file | temp 1.0, top-p 0.95, top-k 20, min-p 0, presence 1.5 | 128K | on | Qwen card: "thinking mode, general tasks" (also what their agentic benchmarks used) |
| `qwen3.6-35b-baseline` | same file | GGUF defaults (temp 1.0, top-k 20, top-p 0.95) + llama.cpp min-p 0.05 | 64K | on | Control: what you get with no presets and the pi docs' 32K-64K context |
| `Qwen3.8-27B-UD-Q6_K_XL` | 25G | temp 1.0, top-p 0.95, top-k 20, min-p 0, presence 0 | 128K | on by default | Qwen card: "thinking mode". Dense model, roughly a third of Qwen3.6's speed |
| `gemma-4-26B-A4B-it-UD-Q6_K_XL` | 22G | temp 1.0, top-p 0.95, top-k 64, min-p 0 | 128K | configurable | Google card: "same for all use cases" |
| `Qwen3-Coder-Next-UD-IQ4_XS` | 38G | temp 1.0, top-p 0.95, top-k 40, min-p 0 | 128K | none (non-thinking model) | Qwen card |
| `qwen3-coder-next-t0.6` | same file | as above, temp 0.6 | 128K | none | Our variant, to separate model vs. temperature |
| `qwen3.8-27b-budget4k-mtp` | 25G + 2G draft | as `Qwen3.8-27B-UD-Q6_K_XL` | 128K | on, capped at 4K tokens per response | **Recommended default; loaded at startup.** Thinking cap plus MTP speculative decoding (about 2× generation speed). Best configuration in the benchmark |
| `qwen3.8-27b-budget4k` | same file | as `Qwen3.8-27B-UD-Q6_K_XL` | 128K | on, capped at 4K | The same without MTP |
| `qwen3.8-27b-budget` | same file | as `Qwen3.8-27B-UD-Q6_K_XL` | 128K | on, capped at 8K | Earlier, looser cap |
| `qwen3.8-27b-instruct` | same file | temp 0.7, top-p 0.80, top-k 20, min-p 0, presence 1.5 | 128K | use with `/thinking off` | Qwen card: "instruct (non-thinking) mode" |
| `qwen3.6-35b-budget4k` | same file as Qwen3.6 | as `Qwen3.6-35B-A3B-UD-Q6_K_XL` | 128K | on, capped at 4K | Qwen3.6 coding preset with the 4K cap |

Notes:

- **Context.** Qwen says Qwen3.6 should get at least 128K "to preserve thinking
  capabilities". Less is a plausible cause of it going in circles on multi-step
  tasks (pi compacts the conversation as it nears the limit). Qwen3.6 and
  Qwen3-Coder-Next use hybrid (mostly linear)
  attention, so long context is cheap: Qwen3.6 at 128K measured ~3 GB of
  memory beyond the weights. If a model fails to load, lower its `c`.
- **Thinking in pi.** For llama.cpp models pi only distinguishes `off` and
  `medium`; other levels are clamped. Thinking helps planning but costs time;
  the benchmark measures both.
- **Thinking cap.** Uncapped, Qwen3.8-27B sometimes spends a whole turn
  planning without calling a tool. `reasoning-budget` caps thinking per
  response; when it runs out, llama.cpp injects `reasoning-budget-message` and
  closes the thinking block, so the model has to act.
- **MTP.** Qwen3.8 ships a small multi-token-prediction head that drafts
  tokens for the main model to verify (`spec-type = draft-mtp`). The output
  distribution is unchanged; generation went from ~22 to ~46 tok/s on an M2
  Ultra. The draft file lives in `models-aux/` next to the models folder, so
  the router doesn't list it as a chat model. Compare presets with
  `bench/speedtest.py`.
- **Memory.** Sized for a 64 GB Apple Silicon Mac, one model at a time.
  Qwen3-Coder-Next (~38G + KV) is the largest; it stays under macOS's default
  GPU limit (~75% of RAM, ~48G), so no `sysctl iogpu.wired_limit_mb` change is
  needed. Qwen3.8-27B measured ~10 GB beyond its weights at 128K.
- **Prompt cache.** `ctx-checkpoints = 32` / `checkpoint-min-step = 0` (in
  `[*]`) let Qwen's hybrid layers reuse the cached prefix between agent turns
  instead of reprocessing the whole conversation; leave them on.
- **Variant names** in `models.ini` use dashes: llama.cpp treats `name:tag` as a
  Hugging Face quantization tag and mangles it.
