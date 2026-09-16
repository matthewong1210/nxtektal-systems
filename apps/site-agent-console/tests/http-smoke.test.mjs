import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, sep } from "node:path";
import { describe, expect, it } from "vitest";

import { resolveStaticPath, serveOnce } from "./http-smoke.mjs";

describe("smoke static server helpers", () => {
  it("resolves request paths inside the export root only", () => {
    const root = join(sep, "srv", "out");
    expect(resolveStaticPath(root, "/")).toBe(join(root, "index.html"));
    expect(resolveStaticPath(root, "/assets/app.js")).toBe(
      join(root, "assets", "app.js"),
    );
    expect(resolveStaticPath(root, "/../secret.txt")).toBeNull();
    expect(resolveStaticPath(root, "/a/../../secret.txt")).toBeNull();
  });

  it("serves files and 404s for absent paths", async () => {
    const root = await mkdtemp(join(tmpdir(), "console-smoke-"));
    await writeFile(join(root, "index.html"), "<title>t</title>");
    const responses = [];
    const fakeResponse = () => {
      const record = { status: null, body: null };
      responses.push(record);
      return {
        writeHead(status) {
          record.status = status;
          return this;
        },
        end(body) {
          record.body = body;
        },
      };
    };
    await serveOnce(root, { url: "/" }, fakeResponse());
    await serveOnce(root, { url: "/missing.js" }, fakeResponse());
    expect(responses[0].status).toBe(200);
    expect(String(responses[0].body)).toContain("<title>t</title>");
    expect(responses[1].status).toBe(404);
  });
});
