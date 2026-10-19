# Sandbox for benchmark runs: pi runs here as the unprivileged `node` user with
# an empty home (no ~/.pi config, skills, MCP servers, or credentials) and an
# empty /workspace. bench.py writes a models.json pointing at the host's
# llama.cpp router (via host.docker.internal) before each run.
FROM node:24-bookworm-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends bash ca-certificates curl git ripgrep procps \
  && rm -rf /var/lib/apt/lists/*

# Pin pi so results stay comparable; bump deliberately (and note it in results).
ARG PI_VERSION=1.3.0
RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent@${PI_VERSION}

ENV PI_SKIP_VERSION_CHECK=1 \
    PI_TELEMETRY=0

RUN mkdir -p /workspace /home/node/sessions && chown node:node /workspace /home/node/sessions

USER node
WORKDIR /workspace

# ClinePass provider for cloud-model runs. Loaded only when a run uses it: the
# minimal profile disables extensions and bench.py adds it back with -e.
RUN pi install npm:pi-clinepass-provider@1.6.0

CMD ["sleep", "infinity"]
