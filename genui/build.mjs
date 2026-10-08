/* Builds the Generative UI runtime into ../static/genui/:
 *   runtime.js          React + components + Motion + Radix, one IIFE
 *   runtime.js.LEGAL.txt  the licences of everything bundled
 *   runtime.css         Tailwind, compiled from the classes in src/, with Inter inlined
 *   catalog.json        components, actions and icons, for app.py
 *
 *   cd genui && npm install && npm run build
 *
 * The output is committed, so the server never needs Node.
 */
import { build } from "esbuild";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { ACTIONS, CATALOG } from "./src/catalog.js";

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, "..", "static", "genui");
mkdirSync(out, { recursive: true });

// 1. The registry and the catalogue must name the same components.
const registry = [...readFileSync(join(here, "src/components/index.js"), "utf8").matchAll(/^\s+(\w+): \w+\.\w+,$/gm)].map((m) => m[1]);
const catalog = Object.keys(CATALOG);
const missing = catalog.filter((n) => !registry.includes(n));
const extra = registry.filter((n) => !catalog.includes(n));
if (missing.length || extra.length) {
  console.error("Registry and catalogue disagree.", { inCatalogNotRegistry: missing, inRegistryNotCatalog: extra });
  process.exit(1);
}

// 2. CSS: Tailwind over src/, then the font inlined (the frame may not
//    fetch anything).
const cssTmp = join(out, "runtime.tmp.css");
execFileSync(process.execPath, [join(here, "node_modules/@tailwindcss/cli/dist/index.mjs"), "-i", join(here, "src/styles.css"), "-o", cssTmp, "--minify"], { stdio: "inherit", cwd: here });
const inter = readFileSync(join(here, "..", "static", "fonts", "inter.woff2")).toString("base64");
const css = readFileSync(cssTmp, "utf8").replace("__INTER_WOFF2__", `data:font/woff2;base64,${inter}`);
writeFileSync(join(out, "runtime.css"), css);
execFileSync(process.execPath, ["-e", `require("fs").unlinkSync(${JSON.stringify(cssTmp)})`]);

// 3. JS.
await build({
  entryPoints: [join(here, "src/index.jsx")],
  outfile: join(out, "runtime.js"),
  bundle: true,
  format: "iife",
  minify: true,
  target: ["es2020"],
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"production"' },
  legalComments: "linked",
  logLevel: "warning",
});

// 4. What app.py needs to validate specs and brief the model.
const icons = JSON.parse(readFileSync(join(here, "src/icon-names.json"), "utf8"));
writeFileSync(join(out, "catalog.json"), JSON.stringify({ components: CATALOG, actions: ACTIONS, icons }, null, 1) + "\n");

for (const f of ["runtime.js", "runtime.css", "catalog.json"]) {
  const buf = readFileSync(join(out, f));
  console.log(`${f.padEnd(14)} ${(statSync(join(out, f)).size / 1024).toFixed(0).padStart(6)} KB   gzip ${(gzipSync(buf).length / 1024).toFixed(0).padStart(5)} KB`);
}
