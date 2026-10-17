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
import { compile, optimize } from "@tailwindcss/node";
import { Scanner } from "@tailwindcss/oxide";
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
// Use Tailwind's compiler and scanner directly: this build never watches
// files, so it does not need the CLI's filesystem watcher dependency tree.
const cssInput = join(here, "src/styles.css");
const compiler = await compile(readFileSync(cssInput, "utf8"), {
  base: dirname(cssInput),
  from: cssInput,
  onDependency() {},
});
const sources = compiler.root === "none" ? [] : [
  compiler.root === null
    ? { base: here, pattern: "**/*", negated: false }
    : { ...compiler.root, negated: false },
];
const scanner = new Scanner({ sources: [...sources, ...compiler.sources] });
const compiledCss = optimize(compiler.build(scanner.scan()), {
  file: join(out, "runtime.css"),
  minify: true,
}).code;
const inter = readFileSync(join(here, "..", "static", "fonts", "inter.woff2")).toString("base64");
const css = compiledCss.replace("__INTER_WOFF2__", `data:font/woff2;base64,${inter}`);
writeFileSync(join(out, "runtime.css"), css);

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
