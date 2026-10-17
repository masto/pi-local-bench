// Scores an "Astro blog with 4-5 posts" attempt. Runs inside the sandbox after
// the agent exits (it's copied in only then, so the agent never sees it), and
// rebuilds from scratch so a stale or hand-written dist/ can't pass.
// Usage: node check.mjs /workspace   -> prints a JSON report on stdout.
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const workspace = process.argv[2] ?? "/workspace";
const SKIP = new Set(["node_modules", ".git", ".astro", "dist", ".cache", ".npm"]);
// Points per check (total 100).
const WEIGHTS = { astro_project: 10, install: 5, build: 25, index_page: 5, post_sources: 10, post_pages: 15, post_rendered: 10, index_links: 10, post_content: 10 };
const checks = [];
const info = {};

// Record a check; `fraction` (0-1) is scaled by the check's weight.
function add(id, fraction, detail) {
  checks.push({ id, max: WEIGHTS[id], points: Math.round(WEIGHTS[id] * fraction * 10) / 10, detail });
}

function walk(dir, { depth = 8, skip = SKIP } = {}, out = []) {
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
      if (!skip.has(e.name)) walk(p, { depth: depth - 1, skip }, out);
    } else if (e.isFile()) out.push(p);
  }
  return out;
}

function run(cmd, args, cwd, timeoutMs) {
  const r = spawnSync(cmd, args, { cwd, encoding: "utf8", timeout: timeoutMs, maxBuffer: 64 << 20 });
  const tail = (s) => (s ?? "").split("\n").slice(-25).join("\n");
  return { ok: r.status === 0, out: tail(r.stdout) + tail(r.stderr) };
}

function readJson(p) {
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch {
    return undefined;
  }
}

function splitFrontmatter(text) {
  const m = text.match(/^---\s*\n([\s\S]*?)\n---\s*\n?([\s\S]*)$/);
  return m ? { fm: m[1], body: m[2] } : { fm: "", body: text };
}

function report(projectDir) {
  const score = checks.reduce((s, c) => s + c.points, 0);
  console.log(JSON.stringify({ score: Math.round(score * 10) / 10, max: 100, projectDir, checks, info }, null, 2));
}

// --- 1. Locate the Astro project (shallowest package.json that depends on astro).
const pkgs = walk(workspace, { depth: 3 })
  .filter((p) => path.basename(p) === "package.json")
  .sort((a, b) => a.split(path.sep).length - b.split(path.sep).length);
let projectDir;
for (const p of pkgs) {
  const pkg = readJson(p);
  const deps = { ...pkg?.dependencies, ...pkg?.devDependencies };
  if (deps.astro) {
    projectDir = path.dirname(p);
    info.astroVersion = deps.astro;
    info.scripts = pkg.scripts ?? {};
    break;
  }
}
add("astro_project", projectDir ? 1 : 0, projectDir ?? "no package.json with an astro dependency");
if (!projectDir) {
  for (const id of Object.keys(WEIGHTS).slice(1)) add(id, 0, "skipped: no project");
  report(projectDir);
  process.exit(0);
}

// --- 2. Install and build from clean.
const install = run("npm", ["install", "--no-audit", "--no-fund"], projectDir, 10 * 60_000);
add("install", install.ok ? 1 : 0, install.ok ? "ok" : install.out);

fs.rmSync(path.join(projectDir, "dist"), { recursive: true, force: true });
const build = info.scripts.build
  ? run("npm", ["run", "build"], projectDir, 10 * 60_000)
  : run("npx", ["--no-install", "astro", "build"], projectDir, 10 * 60_000);
const dist = path.join(projectDir, "dist");
const built = build.ok && fs.existsSync(dist);
add("build", built ? 1 : 0, built ? "ok" : build.out);

const htmlFiles = built ? walk(dist, { skip: new Set() }).filter((f) => f.endsWith(".html")) : [];
const indexHtml = path.join(dist, "index.html");
add("index_page", fs.existsSync(indexHtml) ? 1 : 0, fs.existsSync(indexHtml) ? "ok" : "no dist/index.html");
info.htmlPages = htmlFiles.map((f) => path.relative(dist, f));

// --- 3. Posts: Markdown/MDX with a frontmatter title, in a content collection
// (src/content/) or a blog-like pages directory (so src/pages/about.md isn't a post).
const POST_DIR = /^src\/(content|pages\/(blog|posts?|articles?))\//;
const posts = walk(path.join(projectDir, "src"))
  .filter((f) => /\.mdx?$/.test(f) && POST_DIR.test(path.relative(projectDir, f)))
  .map((f) => {
    const { fm, body } = splitFrontmatter(fs.readFileSync(f, "utf8"));
    const prose = body.replace(/```[\s\S]*?```/g, " ");
    // A distinctive run of plain words from the body, to find in the built page.
    const sample = prose
      .split("\n")
      .map((l) => l.trim())
      .find((l) => /^[A-Za-z]/.test(l) && l.split(/\s+/).length >= 6)
      ?.replace(/[*_`[\]()]/g, "")
      .split(/\s+/)
      .slice(0, 6)
      .join(" ");
    return {
      file: path.relative(projectDir, f),
      slug: path.basename(f).replace(/\.mdx?$/, "").toLowerCase(),
      hasTitle: /^title\s*:/m.test(fm),
      words: prose.split(/\s+/).filter(Boolean).length,
      sample,
    };
  })
  .filter((p) => p.hasTitle);
info.posts = posts;
const n = posts.length;
add("post_sources", n >= 4 && n <= 5 ? 1 : n === 3 || (n >= 6 && n <= 8) ? 0.5 : n > 0 ? 0.25 : 0, `${n} posts with frontmatter titles (want 4-5)`);

// A post counts as built when some generated page's path contains its slug.
for (const p of posts) p.page = htmlFiles.find((f) => path.relative(dist, f).toLowerCase().includes(p.slug));
const builtPosts = posts.filter((p) => p.page);
const want = Math.max(1, Math.min(n, 4));
add("post_pages", n ? Math.min(builtPosts.length, want) / want : 0, `${builtPosts.length}/${n} posts have a built page`);

// ...and is rendered when that page contains text from the post's body. Compare
// letters and digits only: Markdown rendering curls quotes and escapes entities.
const norm = (s) => s.toLowerCase().replace(/&[a-z#0-9]+;/g, " ").replace(/[^a-z0-9]+/g, " ").trim();
const pageText = (f) => norm(fs.readFileSync(f, "utf8").replace(/<[^>]+>/g, " "));
const rendered = builtPosts.filter((p) => p.sample && pageText(p.page).includes(norm(p.sample)));
add("post_rendered", builtPosts.length ? rendered.length / builtPosts.length : 0, `${rendered.length}/${builtPosts.length} built posts show their body text`);
for (const p of posts) if (p.page) p.page = path.relative(dist, p.page);

// The home page (or a blog index) should link to the posts.
const indexPages = [indexHtml, path.join(dist, "blog", "index.html"), path.join(dist, "posts", "index.html")].filter((f) => fs.existsSync(f));
const indexText = indexPages.map((f) => fs.readFileSync(f, "utf8")).join("\n");
const hrefs = [...indexText.matchAll(/href\s*=\s*["']([^"']+)["']/g)].map((m) => m[1].toLowerCase());
const linked = builtPosts.filter((p) => hrefs.some((h) => h.includes(p.slug)));
const wantLinks = Math.max(1, Math.min(builtPosts.length, 4));
add("index_links", builtPosts.length ? Math.min(linked.length, wantLinks) / wantLinks : 0, `${linked.length} posts linked from ${indexPages.map((f) => path.relative(dist, f)).join(", ") || "no index page"}`);

// Sample posts should read like posts, not stubs.
const substantial = posts.filter((p) => p.words >= 120).length;
add("post_content", n ? Math.min(substantial / Math.min(Math.max(n, 4), 5), 1) : 0, `${substantial} posts with >=120 words of body`);

report(projectDir);
