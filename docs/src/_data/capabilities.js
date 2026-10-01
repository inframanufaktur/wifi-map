import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dataDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(dataDirectory, "../../..");
const raw = execFileSync(path.join(projectRoot, "scripts/cargo"), ["run", "--quiet", "--locked", "--", "capabilities"], {
  cwd: projectRoot,
  encoding: "utf8",
});

export default JSON.parse(raw);
