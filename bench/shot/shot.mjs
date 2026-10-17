// Serves a built static site and screenshots it: the home page (desktop and
// phone) and one post page (desktop). Also checks that internal links on the
// home and blog-index pages resolve to real pages, and counts text that fails
// WCAG AA color contrast (axe-core) on each captured page.
// Usage: node shot.mjs <siteDir> <outDir>   -> PNGs + shots.json in outDir
import fs from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";
import path from "node:path";
import puppeteer from "puppeteer-core";

const AXE_SOURCE = fs.readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");

const [siteDir = "/site", outDir = "/shots"] = process.argv.slice(2);
const PORT = 4321;
const BASE = `http://127.0.0.1:${PORT}`;
const MAX_HEIGHT = 8000; // cap runaway layouts
const TYPES = {
  ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".mjs": "text/javascript",
  ".json": "application/json", ".xml": "application/xml", ".txt": "text/plain", ".svg": "image/svg+xml",
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
  ".avif": "image/avif", ".ico": "image/x-icon", ".woff": "font/woff", ".woff2": "font/woff2",
};

// Map a URL path to a file the way static hosts do (/a/ -> /a/index.html, /a -> /a.html).
function resolveFile(urlPath) {
  let clean;
  try {
    clean = decodeURIComponent(urlPath.split(/[?#]/)[0]);
  } catch {
    return null;
  }
  const p = path.join(siteDir, path.normalize(clean));
  if (!p.startsWith(siteDir)) return null;
  for (const c of [p, path.join(p, "index.html"), `${p}.html`]) {
    if (fs.existsSync(c) && fs.statSync(c).isFile()) return c;
  }
  return null;
}

const server = http.createServer((req, res) => {
  const file = resolveFile(req.url);
  if (!file) {
    const notFound = path.join(siteDir, "404.html");
    res.writeHead(404, { "Content-Type": TYPES[".html"] });
    res.end(fs.existsSync(notFound) ? fs.readFileSync(notFound) : "not found");
    return;
  }
  res.writeHead(200, { "Content-Type": TYPES[path.extname(file).toLowerCase()] ?? "application/octet-stream" });
  fs.createReadStream(file).pipe(res);
});
await new Promise((resolve) => server.listen(PORT, "127.0.0.1", resolve));

const browser = await puppeteer.launch({
  executablePath: "/usr/bin/chromium",
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});

async function capture(urlPath, file, { width, height, mobile = false }) {
  const page = await browser.newPage();
  await page.setViewport({ width, height, deviceScaleFactor: mobile ? 2 : 1, isMobile: mobile, hasTouch: mobile });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  let status;
  try {
    status = (await page.goto(BASE + urlPath, { waitUntil: "networkidle0", timeout: 30_000 }))?.status();
  } catch (e) {
    errors.push(String(e));
  }
  const fullHeight = await page.evaluate(() => document.documentElement.scrollHeight).catch(() => height);
  const links = await page
    .$$eval("a[href]", (as) => as.map((a) => a.href))
    .catch(() => []);
  const textLength = await page.evaluate(() => document.body?.innerText.trim().length ?? 0).catch(() => 0);
  let contrastFailures = null;
  let contrastExamples = [];
  try {
    await page.evaluate(AXE_SOURCE);
    const nodes = await page.evaluate(async () => {
      const r = await window.axe.run(document, { runOnly: ["color-contrast"], resultTypes: ["violations"] });
      return r.violations.flatMap((v) => v.nodes.map((n) => n.any[0]?.message ?? n.failureSummary));
    });
    contrastFailures = nodes.length;
    contrastExamples = nodes.slice(0, 3);
  } catch (e) {
    errors.push(`axe: ${e}`);
  }
  await page.screenshot({
    path: path.join(outDir, file),
    ...(fullHeight > MAX_HEIGHT
      ? { clip: { x: 0, y: 0, width, height: MAX_HEIGHT }, captureBeyondViewport: true }
      : { fullPage: true }),
  });
  await page.close();
  return { path: urlPath, file, status, height: fullHeight, textLength, contrastFailures, contrastExamples, errors, links };
}

const internal = (links) =>
  [...new Set(links.filter((h) => h.startsWith(BASE)).map((h) => new URL(h).pathname))];

const pages = [];
const home = await capture("/", "home-desktop.png", { width: 1280, height: 800 });
pages.push(home);
pages.push(await capture("/", "home-mobile.png", { width: 390, height: 844, mobile: true }));

// Links from the home page plus a blog index, if there is one.
let linked = internal(home.links);
const indexPath = ["/blog/", "/posts/"].find((p) => linked.includes(p) || resolveFile(p));
if (indexPath) {
  const index = await capture(indexPath, "index-desktop.png", { width: 1280, height: 800 });
  pages.push(index);
  linked = [...new Set([...linked, ...internal(index.links)])];
}
const broken = linked.filter((p) => !resolveFile(p));

// A post: an existing, two-plus-segment link under a blog-like path (else any deep link).
const deep = linked.filter((p) => resolveFile(p) && p.split("/").filter(Boolean).length >= 2);
const post = deep.find((p) => /\/(blog|posts?|articles?)\//.test(p)) ?? deep[0];
if (post) pages.push(await capture(post, "post-desktop.png", { width: 1280, height: 800 }));

await browser.close();
server.close();
for (const p of pages) delete p.links;
fs.writeFileSync(path.join(outDir, "shots.json"), JSON.stringify({ pages, links: { checked: linked.length, broken } }, null, 2));
console.log(JSON.stringify({ pages: pages.map((p) => p.file), broken: broken.length }));
