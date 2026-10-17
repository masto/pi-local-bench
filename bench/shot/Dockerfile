# Screenshots a built site: serves it from a read-only mount and captures pages
# with headless Chromium. Kept separate from the agent image so the agent
# doesn't get a browser it wouldn't normally have.
FROM node:24-bookworm-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends chromium ca-certificates \
       fonts-dejavu-core fonts-liberation fonts-noto-core fonts-noto-color-emoji \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/shot
RUN npm init -y >/dev/null && npm install --ignore-scripts --no-audit --no-fund puppeteer-core@25.12.0 axe-core@4.13.0
COPY shot.mjs .

USER node
