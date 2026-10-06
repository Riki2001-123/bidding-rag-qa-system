import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
const output = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../dist/pages-deployment/site");
for (const route of ["demo", "login", "chat", "search", "dashboard"]) {
  await fs.mkdir(path.join(output, route), { recursive: true });
  await fs.copyFile(path.join(output, "index.html"), path.join(output, route, "index.html"));
}
await fs.copyFile(path.join(output, "index.html"), path.join(output, "404.html"));
await fs.writeFile(path.join(output, ".nojekyll"), "");
console.log("Prepared static Pages route entries in", output);
