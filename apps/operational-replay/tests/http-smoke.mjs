import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:net";

const port = await new Promise((resolve, reject) => {
  const server = createServer();
  server.once("error", reject);
  server.listen(0, "127.0.0.1", () => {
    const address = server.address();
    assert(address && typeof address === "object");
    const selected = address.port;
    server.close((error) => (error ? reject(error) : resolve(selected)));
  });
});

const child = spawn(
  process.platform === "win32" ? "npm.cmd" : "npm",
  ["run", "start", "--", "--hostname", "127.0.0.1", "--port", String(port)],
  { stdio: ["ignore", "pipe", "pipe"] },
);

let output = "";
child.stdout.on("data", (chunk) => { output += chunk; });
child.stderr.on("data", (chunk) => { output += chunk; });

const deadline = Date.now() + 30_000;
let response;
try {
  while (Date.now() < deadline) {
    try {
      response = await fetch(`http://127.0.0.1:${port}/`);
      break;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }

  assert(response, `server did not become ready\n${output}`);
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<title>NXTektal Replay Story<\/title>/i);
  assert.match(html, /Operational replay/);
  assert.match(html, /Simulation results/);
  assert.match(html, /Presentation layer only/);
  process.stdout.write(`HTTP smoke passed on 127.0.0.1:${port}\n`);
} finally {
  child.kill("SIGTERM");
  await new Promise((resolve) => child.once("close", resolve));
}
