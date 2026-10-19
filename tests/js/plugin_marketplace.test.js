/**
 * Unit tests for the shared marketplace helpers (#418, #421).
 *
 * The lockfile viewer must render populated and empty states without exposing
 * secrets, and every marketplace surface must expose explicit, friendly empty,
 * error and timeout states with a retry affordance.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

const MODULE_PATH = resolve(process.cwd(), "app/static/js/plugin_marketplace.js");
const HASH = "sha256:" + "a".repeat(64);

let marketplace;

beforeAll(() => {
  window.eval(readFileSync(MODULE_PATH, "utf8"));
  marketplace = window.AICAPluginMarketplace;
  if (!marketplace) {
    throw new Error("plugin_marketplace.js did not expose window.AICAPluginMarketplace");
  }
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function mount(html) {
  const container = document.createElement("div");
  container.innerHTML = html;
  document.body.appendChild(container);
  return container;
}

describe("testing surface", () => {
  it("publishes the marketplace helpers", () => {
    for (const name of [
      "bindRetry",
      "errorKind",
      "errorStateHtml",
      "escapeHtml",
      "fetchJson",
      "renderLockfile",
      "safeSource",
      "stateHtml",
    ]) {
      expect(typeof marketplace[name]).toBe("function");
    }
  });
});

describe("lockfile viewer", () => {
  it("renders locked plugins, versions and hashes", () => {
    const container = mount(
      marketplace.renderLockfile({
        lockfile_version: 1,
        plugins: [
          {
            plugin_id: "alpha-plugin",
            version: "1.2.3",
            integrity: HASH,
            source: "https://plugins.example.com/alpha-1.2.3.zip",
            dependencies: ["base-plugin>=1.0.0"],
          },
        ],
      })
    );

    expect(container.querySelector(".plugin-lockfile-table")).not.toBeNull();
    expect(container.textContent).toContain("alpha-plugin");
    expect(container.textContent).toContain("v1.2.3");
    expect(container.textContent).toContain(HASH);
    expect(container.textContent).toContain("https://plugins.example.com/alpha-1.2.3.zip");
    expect(container.textContent).toContain("base-plugin>=1.0.0");
    expect(container.textContent).toContain("lockfile version 1");
  });

  it("renders a friendly empty state", () => {
    const container = mount(marketplace.renderLockfile({ plugins: [] }));

    expect(container.querySelector(".marketplace-state-empty")).not.toBeNull();
    expect(container.querySelector(".plugin-lockfile-table")).toBeNull();
    expect(container.textContent).toContain("No plugins pinned yet");
    expect(container.textContent).toContain("Install a plugin from the catalog");
  });

  it("treats malformed data as empty rather than throwing", () => {
    const container = mount(marketplace.renderLockfile(null));

    expect(container.querySelector(".marketplace-state-empty")).not.toBeNull();
  });

  it("never exposes credentials embedded in a source URL", () => {
    const container = mount(
      marketplace.renderLockfile({
        plugins: [
          {
            plugin_id: "secret-plugin",
            version: "1.0.0",
            integrity: HASH,
            source: "https://api-user:super-secret@plugins.example.com/p.zip",
            dependencies: [],
          },
        ],
      })
    );

    expect(container.textContent).toContain("plugins.example.com");
    expect(container.textContent).not.toContain("api-user");
    expect(container.textContent).not.toContain("super-secret");
  });

  it("escapes plugin-controlled values", () => {
    const container = mount(
      marketplace.renderLockfile({
        plugins: [
          {
            plugin_id: '<img src=x onerror="alert(1)">',
            version: "1.0.0",
            integrity: HASH,
            source: "https://plugins.example.com/p.zip",
            dependencies: ["<script>alert(1)</script>"],
          },
        ],
      })
    );

    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("<img src=x onerror=\"alert(1)\">");
  });
});

describe("empty, error and timeout states", () => {
  it("renders an explicit empty state without a retry", () => {
    const container = mount(
      marketplace.stateHtml("empty", {
        title: "No plugins available yet",
        message: "An owner can install a plugin.",
      })
    );

    expect(container.querySelector(".marketplace-state-empty")).not.toBeNull();
    expect(container.textContent).toContain("No plugins available yet");
    expect(container.querySelector(".marketplace-retry")).toBeNull();
  });

  it("renders a friendly error state with retry", () => {
    const container = mount(
      marketplace.errorStateHtml(new Error("boom"), { title: "Could not load plugins" })
    );

    expect(container.querySelector(".marketplace-state-error")).not.toBeNull();
    expect(container.textContent).toContain("Could not load plugins");
    expect(container.textContent).toContain("Please try again");
    expect(container.querySelector(".marketplace-retry").textContent).toContain("Retry");
  });

  it("renders a distinct timeout state with retry", () => {
    const error = new Error("The request timed out.");
    error.code = "timeout";
    const container = mount(marketplace.errorStateHtml(error));

    expect(container.querySelector(".marketplace-state-timeout")).not.toBeNull();
    expect(container.textContent).toContain("taking longer than expected");
    expect(container.textContent).toContain("did not respond in time");
    expect(container.querySelector(".marketplace-retry")).not.toBeNull();
  });

  it("can suppress the retry affordance", () => {
    const container = mount(marketplace.errorStateHtml(new Error("x"), { retry: false }));

    expect(container.querySelector(".marketplace-retry")).toBeNull();
  });

  it("binds the retry handler", () => {
    const container = mount(marketplace.errorStateHtml(new Error("x")));
    const handler = vi.fn();

    marketplace.bindRetry(container, handler);
    container.querySelector(".marketplace-retry").click();

    expect(handler).toHaveBeenCalledTimes(1);
  });
});

describe("timeout-bounded fetch", () => {
  it("returns parsed JSON on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ plugins: [] }),
      })
    );

    await expect(marketplace.fetchJson("/plugins/api")).resolves.toEqual({ plugins: [] });
  });

  it("turns an aborted request into a timeout error", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (url, options) =>
          new Promise((resolve, reject) => {
            options.signal.addEventListener("abort", () => {
              const error = new Error("Aborted");
              error.name = "AbortError";
              reject(error);
            });
          })
      )
    );

    const request = marketplace.fetchJson("/plugins/api", { timeoutMs: 50 });
    const assertion = expect(request).rejects.toMatchObject({ code: "timeout" });

    await vi.advanceTimersByTimeAsync(60);
    await assertion;
  });

  it("surfaces the server error message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        json: () => Promise.resolve({ error: "Marketplace unavailable" }),
      })
    );

    await expect(marketplace.fetchJson("/plugins/api")).rejects.toMatchObject({
      code: "http",
      status: 503,
      message: "Marketplace unavailable",
    });
  });
});
