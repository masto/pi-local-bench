// Passes when /workspace/hello.txt contains "hi".
import fs from "node:fs";
import path from "node:path";

const file = path.join(process.argv[2] ?? "/workspace", "hello.txt");
const text = fs.existsSync(file) ? fs.readFileSync(file, "utf8").trim() : null;
const ok = text?.toLowerCase() === "hi";
console.log(JSON.stringify({ score: ok ? 100 : 0, max: 100, checks: [{ id: "hello", max: 100, points: ok ? 100 : 0, detail: text ?? "missing" }] }));
