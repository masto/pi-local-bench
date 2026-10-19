// Passes when docs.txt holds an Astro docs URL and seen.txt holds the h1 read in the browser.
import fs from "node:fs";
import path from "node:path";

const dir = process.argv[2] ?? "/workspace";
const read = (f) => (fs.existsSync(path.join(dir, f)) ? fs.readFileSync(path.join(dir, f), "utf8").trim() : null);
const docs = read("docs.txt");
const seen = read("seen.txt");
const checks = [
  { id: "web_search", max: 50, points: /docs\.astro\.build/.test(docs ?? "") ? 50 : 0, detail: docs ?? "missing" },
  { id: "browser", max: 50, points: /probe page/i.test(seen ?? "") ? 50 : 0, detail: seen ?? "missing" },
];
console.log(JSON.stringify({ score: checks.reduce((s, c) => s + c.points, 0), max: 100, checks }));
