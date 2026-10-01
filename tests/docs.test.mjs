import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const pages = ["index.html", "quick-start/index.html", "walk-tui/index.html", "tui-controls/index.html", "evaluate/index.html", "cli-reference/index.html", "data-troubleshooting/index.html", "design-language/index.html", "development/index.html"];
const extract = () => execFileSync(path.join(root, "scripts/cargo"), ["run", "--quiet", "--locked", "--", "capabilities"], { cwd: root, encoding: "utf8" });
const doc = JSON.parse(extract());

test("capabilities are deterministic, portable, and expose the Rust command tree", () => {
  assert.equal(extract(), extract());
  assert.equal(doc.package.name, "wifimap");
  assert.equal(doc.package.rust_requires, "1.88");
  assert.equal(doc.package.python_requires, undefined);
  assert.ok(!extract().includes(root));
  for (const command of ["scan", "walk", "eval", "export", "benchmark set", "benchmark show", "benchmark clear", "locations add", "rooms add", "spots add", "aps name"]) {
    assert.ok(doc.cli.commands.some(c => c.path.join(" ") === command), command);
  }
  assert.ok(!doc.cli.commands.some(c => c.path.includes("capabilities")));
  const scan = doc.cli.commands.find(c => c.path.join(" ") === "scan");
  assert.equal(scan.options.find(o => o.flags.includes("--location")).required, true);
  assert.equal(scan.options.find(o => o.flags.includes("--no-speedtest")).kind, "flag");
});

test("all documentation pages render without unresolved templates or stale runtime requirements", () => {
  for (const page of pages) {
    const html = readFileSync(path.join(root, "docs/_site", page), "utf8");
    assert.ok(html.includes("<html") && html.includes("<main"), page);
    assert.ok(!/\{[{%]/u.test(html), `${page}: unresolved template`);
    assert.ok(!html.includes("pip install") && !html.includes("Python &gt;="), `${page}: stale runtime requirement`);
    assert.ok(html.includes(`v${doc.package.version}`), `${page}: version`);
    assert.equal((html.match(/aria-current="page"/gu) ?? []).length, 1, `${page}: active navigation`);
    assert.ok(html.includes("assets/site.css") && html.includes("assets/site.js"), `${page}: assets`);
    const ids = [...html.matchAll(/\bid="([^"]+)"/gu)].map(m => m[1]);
    assert.equal(new Set(ids).size, ids.length, `${page}: duplicate element ID`);
  }
});

test("internal documentation links and local assets resolve", () => {
  for (const page of pages) {
    const filename = path.join(root, "docs/_site", page);
    const html = readFileSync(filename, "utf8");
    for (const [, value] of html.matchAll(/\b(?:href|src)="([^"]+)"/gu)) {
      if (/^(?:https?:|mailto:|data:)/u.test(value)) continue;
      const [link, anchor] = value.split("#");
      const resolved = link.startsWith("/") ? path.join(root, "docs/_site", decodeURIComponent(link)) : path.resolve(path.dirname(filename), decodeURIComponent(link));
      const target = !link ? filename : (link.endsWith("/") ? path.join(resolved, "index.html") : resolved);
      assert.ok(existsSync(target), `${page}: ${value}`);
      if (anchor && target.endsWith(".html")) {
        assert.ok(readFileSync(target, "utf8").includes(`id="${decodeURIComponent(anchor)}"`), `${page}: missing anchor ${value}`);
      }
    }
  }
});

test("generated CLI reference includes actual options and walk controls", () => {
  const cli = readFileSync(path.join(root, "docs/_site/cli-reference/index.html"), "utf8");
  for (const flag of ["--db", "--demo", "--compare-to", "--no-speedtest", "--room-floor", "--force"]) assert.ok(cli.includes(flag), flag);
  const walk = readFileSync(path.join(root, "docs/_site/walk-tui/index.html"), "utf8");
  for (const control of doc.walk.controls) assert.ok(walk.includes(`<kbd>${control.key}</kbd>`), control.key);
  const controls = readFileSync(path.join(root, "docs/_site/tui-controls/index.html"), "utf8");
  for (const control of doc.walk.controls) assert.ok(controls.includes(`<kbd>${control.key}</kbd>`), `controls: ${control.key}`);
});
