# "Batteries" sandbox: the minimal pi-bench image plus the packages that give pi
# Claude-Code-like capabilities: web search/fetch, task tracking, code
# diagnostics, a browser, and pi's grep/find/ls tools. Versions are pinned to the
# ones probed on the host on 2026-10-17 (the host uses Chrome for Testing; this
# image uses Debian Chromium); bump deliberately.
FROM pi-bench:1.3.0

USER root

# agent-browser CLI (bundles its linux-arm64 binary) driving Debian's Chromium:
# agent-browser won't install Chrome for Testing on Linux ARM64.
ARG AGENT_BROWSER_VERSION=0.39.1
RUN apt-get update \
  && apt-get install -y --no-install-recommends chromium fonts-dejavu-core fonts-liberation fonts-noto-core \
  && rm -rf /var/lib/apt/lists/* \
  && npm install -g --no-audit --no-fund agent-browser@${AGENT_BROWSER_VERSION} \
  && node "$(npm root -g)/agent-browser/scripts/postinstall.js"
# Chromium's own sandbox needs privileges Docker doesn't grant; the container is the sandbox.
ENV AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium \
    AGENT_BROWSER_ARGS=--no-sandbox

USER node

RUN pi install npm:pi-web-access@0.42.0 \
  && pi install npm:@tintinweb/pi-tasks@0.9.1 \
  && pi install npm:pi-lens@4.4.3 \
  && pi install npm:pi-agent-browser-native@0.9.6 \
  # ast-grep's install script only links its bundled binary (reviewed 2026-10-17).
  && npm --prefix ~/.pi/agent/npm install-scripts approve @ast-grep/cli \
  && npm --prefix ~/.pi/agent/npm rebuild @ast-grep/cli \
  && node -e 'const f=process.env.HOME+"/.pi/agent/settings.json",fs=require("fs"),s=JSON.parse(fs.readFileSync(f));s.defaultTools=["+grep","+find","+ls"];fs.writeFileSync(f,JSON.stringify(s,null,2))' \
  # Keyless DuckDuckGo search: Ceramic's index lacked docs sites (e.g. docs.astro.build).
  && printf '{\n  "provider": "duckduckgo"\n}\n' > ~/.pi/agent/web-search.json
