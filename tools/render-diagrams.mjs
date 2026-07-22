import { mkdirSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const diagramRoot = path.join(projectRoot, "docs", "diagrams");
const sourceRoot = path.join(diagramRoot, "src");
const outputRoot = path.join(diagramRoot, "output");
const configPath = path.join(diagramRoot, "mermaid-config.json");
const puppeteerConfigPath = path.join(diagramRoot, "puppeteer-config.json");
const mmdcEntry = path.join(
  projectRoot,
  "node_modules",
  "@mermaid-js",
  "mermaid-cli",
  "src",
  "cli.js",
);

const diagrams = [
  { name: "use-case-diagram", width: 2800 },
  { name: "system-flowchart", width: 3400 },
  { name: "system-architecture", width: 2800 },
  { name: "core-erd", width: 3000 },
  { name: "full-technical-erd", width: 4400 },
];

mkdirSync(outputRoot, { recursive: true });

function render(name, extension, width) {
  const input = path.join(sourceRoot, `${name}.mmd`);
  const output = path.join(outputRoot, `${name}.${extension}`);
  const args = [
    "-i", input,
    "-o", output,
    "-c", configPath,
    "-p", puppeteerConfigPath,
    "-b", "#FFF9E8",
    "-w", String(width),
  ];
  if (extension === "png") {
    args.push("-s", "2");
  }
  const result = spawnSync(process.execPath, [mmdcEntry, ...args], {
    cwd: projectRoot,
    stdio: "inherit",
    shell: false,
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(`Mermaid rendering failed for ${name}.${extension}`);
  }
}

for (const diagram of diagrams) {
  render(diagram.name, "svg", diagram.width);
  render(diagram.name, "png", diagram.width);
}

console.log(`Rendered ${diagrams.length} diagrams to ${outputRoot}`);
