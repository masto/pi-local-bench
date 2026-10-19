// Static half of the Tetris score (20 points): an HTML entry page exists, and the
// game uses no frameworks, external libraries or build step. Reports `siteDir` and
// `entry` so the harness can serve the game to play.mjs (the remaining 80 points).
// Usage: node check.mjs /workspace   -> prints a JSON report on stdout.
import fs from "node:fs";
import path from "node:path";

const workspace = process.argv[2] ?? "/workspace";
const SKIP = new Set(["node_modules", ".git", "dist", "build", ".cache"]);
const checks = [];
const info = {};

function walk(dir, depth = 3, out = []) {
  if (depth < 0) return out;
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const e of entries) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) {
      if (!SKIP.has(e.name)) walk(p, depth - 1, out);
    } else if (e.isFile()) out.push(p);
  }
  return out;
}

const files = walk(workspace);
// The game's entry page: the shallowest index.html, else the shallowest .html file
// (the prompt doesn't name the file, e.g. tetris.html is fine).
const byDepth = (a, b) => a.split(path.sep).length - b.split(path.sep).length || a.localeCompare(b);
const htmls = files.filter((f) => /\.html?$/i.test(f)).sort(byDepth);
const index = htmls.find((f) => path.basename(f).toLowerCase() === "index.html") ?? htmls[0];
checks.push({ id: "html_entry", max: 10, points: index ? 10 : 0, detail: index ? path.relative(workspace, index) : "no HTML page" });
const siteDir = index ? path.dirname(index) : undefined;
const entry = index ? path.basename(index) : undefined;

// No frameworks/libraries/build: no external scripts or stylesheets, no bare-module
// imports, and no package.json dependencies.
const problems = [];
if (siteDir) {
  const html = fs.readFileSync(index, "utf8");
  for (const m of html.matchAll(/<script[^>]+src\s*=\s*["']([^"']+)["']/gi)) if (/^(https?:)?\/\//.test(m[1])) problems.push(`external script ${m[1]}`);
  for (const m of html.matchAll(/<link[^>]+href\s*=\s*["']((?:https?:)?\/\/[^"']+)["'][^>]*stylesheet|<link[^>]+stylesheet[^>]*href\s*=\s*["']((?:https?:)?\/\/[^"']+)["']/gi)) problems.push(`external stylesheet ${m[1] ?? m[2]}`);
  for (const f of files.filter((f) => /\.(m?js)$/.test(f))) {
    const src = fs.readFileSync(f, "utf8");
    for (const m of src.matchAll(/\bimport\s[^'"]*?from\s*["']([^"'./][^"']*)["']|\bimport\s*["']([^"'./][^"']*)["']/g)) problems.push(`bare import ${m[1] ?? m[2]} in ${path.relative(workspace, f)}`);
  }
}
for (const f of files.filter((f) => path.basename(f) === "package.json")) {
  try {
    const pkg = JSON.parse(fs.readFileSync(f, "utf8"));
    const deps = Object.keys({ ...pkg.dependencies, ...pkg.devDependencies });
    if (deps.length) problems.push(`package.json dependencies: ${deps.join(", ")}`);
  } catch {}
}
checks.push({ id: "plain_web", max: 10, points: siteDir && !problems.length ? 10 : 0, detail: problems.length ? problems.slice(0, 5).join("; ") : siteDir ? "ok" : "no game" });
info.jsFiles = files.filter((f) => /\.(m?js)$/.test(f)).map((f) => path.relative(workspace, f));

console.log(JSON.stringify({ score: checks.reduce((s, c) => s + c.points, 0), max: 20, siteDir, entry, checks, info }, null, 2));
