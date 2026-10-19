#!/bin/sh
# Start the llama.cpp router server for pi (https://pi.dev/docs/latest/llama-cpp).
# Models are discovered from $MODELS_DIR (default ~/models); load/unload them
# from pi with /llama.
#
# Per-model settings (context size, sampling, prompt-cache checkpoints) live in
# models.ini next to this script. Don't add -c or sampling flags here: command-
# line flags override every preset. Preset `model = <file>.gguf` and
# `model-draft = ...` lines with relative paths name files relative to
# $MODELS_DIR; they're expanded to absolute paths at startup.
# HF_TOKEN: only needed if you download a *gated* repo via /llama. It's read
#   from the hf CLI login so the token is never stored in this file.
set -eu

MODELS_DIR=${MODELS_DIR:-$HOME/models}
PRESETS=$(mktemp "${TMPDIR:-/tmp}/llama-models.XXXXXX")
sed -E "s|^(model(-draft)?) *= *([^/].*)$|\1 = $MODELS_DIR/\3|" "$(dirname "$0")/models.ini" > "$PRESETS"

if [ -z "${HF_TOKEN:-}" ] && command -v hf >/dev/null 2>&1; then
  HF_TOKEN=$(hf auth token 2>/dev/null | tail -1) && export HF_TOKEN
fi

exec llama-server \
  --models-dir "$MODELS_DIR" \
  --models-preset "$PRESETS" \
  --no-models-autoload \
  --jinja \
  --host 127.0.0.1 \
  --port 8080 \
  -ngl 999 \
  "$@"
