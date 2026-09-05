/**
 * HTTP smoke for the static console export.
 *
 * The console builds to a static export (`out/`) that the local Python
 * Site Agent service serves same-origin. There is no Node production
 * server, so this smoke serves `out/` with a throwaway loopback static
 * server and asserts the exported page carries its operational markers
 * (title, disclaimer, unreachable-service guidance).
 *
 * Run `npm run build` first.
 */

import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, join, normalize, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const OUT_DIR = resolve(
  fileURLToPath(new URL(".", import.meta.url)),
  "..",
  "out",
);

const CONTENT_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".txt": "text/plain; charset=utf-8",
  ".woff2": "font/woff2",
};

export function resolveStaticPath(outDir, requestPath) {
  const cleaned = requestPath.split("?")[0];
  const relative = cleaned.replace(/^\/+/, "");
  const candidate = normalize(join(outDir, relative || "index.html"));
  if (candidate !== outDir && !candidate.startsWith(outDir + sep)) {
    return null;
  }
  return candidate;
}

export async function serveOnce(outDir, request, response) {
  let target = resolveStaticPath(outDir, request.url ?? "/");
  if (target === null) {
    response.writeHead(404).end("outside root");
    return;
  }
  try {
    const stats = await stat(target);
    if (stats.isDirectory()) {
      target = join(target, "index.html");
    }
    const body = await readFile(target);
    response
      .writeHead(200, {
        "Content-Type":
          CONTENT_TYPES[extname(target).toLowerCase()] ??
          "application/octet-stream",
      })
      .end(body);
  } catch {
    response.writeHead(404).end("not found");
  }
}

export async function runHttpSmoke() {
  const indexStats = await stat(join(OUT_DIR, "index.html")).catch(() => null);
  assert.ok(
    indexStats,
    "out/index.html is missing — run `npm run build` before the smoke",
  );

  const server = createServer((request, response) => {
    void serveOnce(OUT_DIR, request, response);
  });
  await new Promise((resolvePromise) =>
    server.listen(0, "127.0.0.1", resolvePromise),
  );
  const { port } = server.address();
  try {
    const response = await fetch(`http://127.0.0.1:${port}/`, {
      redirect: "error",
      signal: AbortSignal.timeout(5000),
    });
    assert.equal(response.status, 200);
    assert.match(
      response.headers.get("content-type") ?? "",
      /^text\/html\b/i,
    );
    const html = await response.text();
    assert.match(html, /<title>NXTektal Site Agent Console<\/title>/i);
    assert.match(html, /SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA/);
    assert.match(html, /Site Agent/);
    assert.match(html, /Manager Console/);

    const traversal = await fetch(
      `http://127.0.0.1:${port}/..%2fpackage.json`,
      { signal: AbortSignal.timeout(5000) },
    );
    assert.equal(traversal.status, 404);
    console.log(`HTTP smoke passed on 127.0.0.1:${port}`);
  } finally {
    await new Promise((resolvePromise) => server.close(resolvePromise));
  }
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (fileURLToPath(import.meta.url) === invokedPath) {
  runHttpSmoke().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
