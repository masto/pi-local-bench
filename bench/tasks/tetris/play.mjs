// Gameplay half of the Tetris score (80 points), run in the screenshot container
// (headless Chromium via puppeteer-core). Serves the game, then checks that it
// loads cleanly, has a playfield, has gravity (the board changes on its own),
// responds to the arrow keys and space, and shows a score. "Board state" is the
// canvas pixels when there is a canvas, otherwise the page's DOM.
// Usage: node play.mjs <siteDir> <outDir>   -> prints a JSON report on stdout.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import puppeteer from "puppeteer-core";

const [siteDir = "/site", outDir = "/shots"] = process.argv.slice(2);
const PORT = 4322;
const TYPES = { ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml", ".wav": "audio/wav", ".mp3": "audio/mpeg" };
const checks = [];
const info = { pageErrors: [] };
const add = (id, max, ok, detail) => checks.push({ id, max, points: Math.round(max * (ok === true ? 1 : ok || 0) * 10) / 10, detail });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const server = http.createServer((req, res) => {
  const rel = path.normalize(decodeURIComponent(req.url.split(/[?#]/)[0]));
  let file = path.join(siteDir, rel);
  if (!file.startsWith(siteDir)) return res.writeHead(403).end();
  if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, "index.html");
  // Browsers request /favicon.ico on their own; don't count a missing one as a page error.
  if (!fs.existsSync(file) && rel === "/favicon.ico") return res.writeHead(204).end();
  if (!fs.existsSync(file)) return res.writeHead(404).end("not found");
  res.writeHead(200, { "Content-Type": TYPES[path.extname(file).toLowerCase()] ?? "application/octet-stream" });
  fs.createReadStream(file).pipe(res);
});
await new Promise((r) => server.listen(PORT, "127.0.0.1", r));

const browser = await puppeteer.launch({ executablePath: "/usr/bin/chromium", args: ["--no-sandbox", "--disable-dev-shm-usage", "--autoplay-policy=no-user-gesture-required"] });
const page = await browser.newPage();
await page.setViewport({ width: 1100, height: 850 });
page.on("pageerror", (e) => info.pageErrors.push(String(e).slice(0, 300)));
page.on("console", (m) => m.type() === "error" && info.pageErrors.push(`console: ${m.text().slice(0, 300)}`));

const state = () =>
  page.evaluate(() => {
    const canvases = [...document.querySelectorAll("canvas")].filter((c) => c.width * c.height >= 2000);
    if (canvases.length) return canvases.map((c) => { try { return c.toDataURL(); } catch { return ""; } }).join("|");
    return document.body ? document.body.innerHTML : "";
  });
const press = async (key) => { await page.keyboard.press(key); await sleep(60); };

try {
  let status;
  try {
    status = (await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil: "networkidle0", timeout: 20_000 }))?.status();
  } catch (e) {
    info.pageErrors.push(`load: ${e}`);
  }
  await sleep(500);
  const loadErrors = [...info.pageErrors];
  add("loads_clean", 15, status === 200 && loadErrors.length === 0, status === 200 ? (loadErrors.length ? loadErrors.slice(0, 3).join(" | ") : "ok") : `HTTP ${status}`);

  const field = await page.evaluate(() => {
    const big = [...document.querySelectorAll("canvas")].filter((c) => c.width * c.height >= 10_000);
    if (big.length) return { kind: "canvas", size: `${big[0].width}x${big[0].height}` };
    const cells = Math.max(0, ...[...document.querySelectorAll("*")].map((el) => el.children.length));
    return cells >= 100 ? { kind: "dom-grid", size: `${cells} cells` } : null;
  });
  add("playfield", 10, !!field, field ? `${field.kind} ${field.size}` : "no canvas or grid found");

  // Gravity: the board changes with no input. If not, try the usual ways to start.
  const changesAlone = async () => { const a = await state(); await sleep(2500); return a !== (await state()); };
  let gravity = await changesAlone();
  info.startedVia = gravity ? "autostart" : null;
  for (const start of ["Enter", " ", "click-button", "click-canvas"]) {
    if (gravity) break;
    if (start === "click-button") {
      const clicked = await page.evaluate(() => {
        const b = [...document.querySelectorAll("button, [role=button], a, div")].find((el) => /^(start|play|new game|start game|play again)\b/i.test(el.textContent.trim()) && el.offsetParent);
        b?.click();
        return !!b;
      });
      if (!clicked) continue;
    } else if (start === "click-canvas") {
      const c = await page.$("canvas");
      if (!c) continue;
      await c.click();
    } else await press(start);
    await sleep(300);
    gravity = await changesAlone();
    if (gravity) info.startedVia = start;
  }
  add("gravity", 20, gravity, gravity ? `board changes on its own (started via ${info.startedVia})` : "board never changed on its own");
  await page.screenshot({ path: path.join(outDir, "play-start.png") });

  // Controls: a key "responds" when the board changes right after it but not in the
  // same short window without input. Gravity ticks can collide, so take the best of 3.
  const keys = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "rotate", ArrowDown: "soft drop", " ": "hard drop" };
  const responsive = [];
  for (const [key, label] of Object.entries(keys)) {
    for (let i = 0; i < 3; i++) {
      const a = await state(); await sleep(60); const b = await state();
      if (a !== b) continue; // a gravity tick landed in the baseline window; retry
      await press(key);
      if ((await state()) !== b) { responsive.push(label); break; }
    }
  }
  add("controls", 20, Math.min(responsive.length, 4) / 4, responsive.length ? `responds to: ${responsive.join(", ")}` : "no key changed the board");

  // Play on: hard-drop a few pieces, then look for a score/lines/level readout.
  for (let i = 0; i < 12; i++) { await press(" "); await sleep(120); }
  const scoreText = await page.evaluate(() => (document.body?.innerText.match(/\b(score|lines?|level)\b[^\n]{0,20}/gi) || []).slice(0, 3));
  // Canvas-only games often draw the score; fall back to scripts that track one.
  const tracksScore = scoreText.length > 0 || (await page.evaluate(() => [...document.scripts].some((s) => /\bscore\b/i.test(s.textContent))));
  add("score_display", 15, scoreText.length ? 1 : tracksScore ? 0.5 : 0, scoreText.length ? scoreText.join(" · ") : tracksScore ? "score tracked in code, not visible as text (maybe drawn on canvas)" : "no score found");
  await page.screenshot({ path: path.join(outDir, "play-later.png") });
  info.errorsDuringPlay = info.pageErrors.slice(loadErrors.length).slice(0, 5);
} catch (e) {
  // Usually the game froze the page (e.g. an endless loop), so the browser stopped answering.
  // Keep the checks that finished and fail the rest with the reason.
  const reason = /timed out/i.test(String(e)) ? "page stopped responding (likely an endless loop in the game)" : String(e).slice(0, 200);
  for (const [id, max] of [["loads_clean", 15], ["playfield", 10], ["gravity", 20], ["controls", 20], ["score_display", 15]])
    if (!checks.some((c) => c.id === id)) add(id, max, false, reason);
  info.crash = reason;
} finally {
  await browser.close();
  server.close();
}
console.log(JSON.stringify({ score: checks.reduce((s, c) => s + c.points, 0), max: 80, checks, info }));
