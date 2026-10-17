# pi local-model benchmark

Runs pi non-interactively on a task, across models and settings, and scores
the result automatically.

```sh
./bench.py run                         # everything in matrix.toml (2 repeats each)
./bench.py run --only gemma4 -n 1      # run names containing "gemma4", once each
./bench.py run --task smoke --only "qwen3.6 coding" -n 1 --no-review   # quick harness check
./bench.py report                      # table -> results/report.md, and results/gallery.html
open results/gallery.html              # screenshots + review per run

# Redo one stage for existing results (e.g. after changing a checker);
# --only filters by result directory name.
./bench.py rescore    # re-run the checker on saved workspaces, then screenshots
./bench.py shots      # screenshots only
./bench.py review     # review runs without a review (--redo for all)
```

Needs Docker running and the router started with `../start-llama-router.sh`
(so it has the presets from `../models.ini`). Don't use pi interactively during a
run: the harness loads and unloads models on the same router. The full matrix
is 9 configs × 2 repeats × up to 30-45 min, so run it overnight.

## What a run does

1. Loads the configuration's model on the router (unloading any others) and
   records the server's effective sampling settings and context size.
2. Starts a fresh `pi-bench` container (see `Dockerfile`) and runs
   `pi --mode json` with the task prompt as the only message, under a wall-clock
   timeout. The container has an empty home and workspace, and pi runs with
   skills, extensions, MCP, prompt templates, themes and AGENTS.md/CLAUDE.md
   discovery all disabled. Your `~/.agents`, `~/.pi`, credentials and files
   aren't visible to it. It does have network access (it needs npm).
3. Copies the task's checker in (only now, so the agent can't read it) and
   scores the workspace, then removes the container.
4. Serves the built site in a second container (`shot/`, headless Chromium) and
   screenshots the home page (desktop and phone) and a post. It also checks the
   home and blog-index links for 404s and counts text that fails WCAG AA color
   contrast (axe-core).
5. Has Claude (Opus 5.5, through the [Claude Code](https://claude.com/claude-code)
   `claude` CLI, logged in with your account) review the
   code and the transcript for things an expert would find strange: invented
   workarounds, outdated or mixed-version APIs, false claims, loops. It can
   read only that run's folder, and can fetch only docs.astro.build to check API
   claims. It starts in an empty directory (`.review-cwd/`), with no settings,
   MCP or skills, so nothing the agent wrote is loaded as configuration.
   Reviews run in the background while the next local run proceeds. Each costs
   roughly $0.30–0.70 at API list prices, against your Claude usage. Use
   `--no-review` to skip them.

Each run leaves `results/<time>_<config>_r<n>/` with:

| File | What |
|---|---|
| `result.json` | score, per-check points, settings, turns, tool calls/errors, tokens, thinking, timing, link/contrast facts, review verdict |
| `check.json` | the checker's detailed report (e.g. build errors) |
| `review.json` | the review: verdict, idiomatic/quality 1–5, findings, process notes |
| `shots/` | screenshots and `shots.json` (status, text length, contrast failures, broken links) |
| `transcript.md` | condensed session log: thinking, commands, outputs |
| `session.html` | pi's full session export |
| `workspace/` | what the agent produced (no node_modules or links) |
| `site/` | the clean build of it (when it built) |
| `events.jsonl` | pi's raw event stream |

## Scoring (`tasks/astro-blog/check.mjs`)

Rebuilds from clean (`npm install`, removes `dist/`, `npm run build`) and checks:

| Points | Check |
|---|---|
| 10 | a package.json depending on `astro` (anywhere up to 3 levels deep) |
| 5 | `npm install` succeeds |
| 25 | `npm run build` succeeds and produces `dist/` |
| 5 | `dist/index.html` exists |
| 10 | 4-5 Markdown/MDX posts with a frontmatter title, in `src/content/` or `src/pages/blog/` etc. (3 or 6-8 get half) |
| 15 | a built page exists for each post |
| 10 | each built post page actually shows the post's body text |
| 10 | the home page (or `/blog/`, `/posts/`) links to the posts |
| 10 | posts have at least 120 words of body |

The score answers "does it work?". The review answers "how did it get there?".
A site can score 100 because it leaned on a deprecated compatibility flag, and
the review will say so. Design is left to you and the gallery. The contrast
count only flags likely unreadable text.

Sampling is random, so one run per config is anecdote. Use 2-3 repeats before
drawing conclusions, more if scores are close.

## Adding things

- **A configuration:** add a `[[run]]` to `matrix.toml`. To vary sampling or
  context, add a section to `../models.ini` (dashes, not colons, in the name),
  restart the router, and point `model` at it.
- **A model:** download its GGUF into your models directory (or use `/llama` →
  Download in pi), optionally add a preset with its card's recommended sampling,
  restart. Results from earlier runs are kept; run just the new entry with
  `--only`.
- **A task:** create `tasks/<name>/task.toml` (`prompt`, `check`) and a checker
  that reads `/workspace` and prints `{"score": n, "max": 100, "checks": [...]}`.
  Then `./bench.py run --task <name>` or set `task` per `[[run]]`.

## Notes

- pi is pinned to 1.3.0 in the image (`PI_VERSION` in `bench.py` and the
  `Dockerfile`). Bump both together; results record the version.
- Headless pi never refreshes the llama.cpp extension's model catalog, so the
  harness declares the model in the container's `~/.pi/agent/models.json` the
  way that extension would (context size, thinking support).
- The container is limited to 8 GB of RAM. The model runs on the host, so this
  only affects npm/build inside the sandbox.
- To review some other task, edit `REVIEW_PROMPT` in `bench.py`. It's written
  generically, but its docs allowlist (`docs.astro.build`) is Astro-specific.
- Tested on macOS (Apple Silicon) with Docker Desktop, whose containers reach
  the host's `127.0.0.1:8080` as `host.docker.internal`. On Linux you'd need
  the router listening on an address containers can reach, plus
  `--add-host=host.docker.internal:host-gateway` on the `docker run` calls.
