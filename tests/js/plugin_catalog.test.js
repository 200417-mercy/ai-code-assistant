/**
 * Unit tests for the marketplace catalog browser (#364).
 *
 * The renderer must expose trust/compatibility/dependency/artifact metadata,
 * escape catalog text, show owner-only actions and render bounded pagination.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

const FILES = [
  "app/static/js/plugin_marketplace.js",
  "app/static/js/plugin_badges.js",
  "app/static/js/plugin_catalog.js",
];
const HASH = "sha256:" + "a".repeat(64);

let catalog;

beforeAll(() => {
  for (const file of FILES) {
    window.eval(readFileSync(resolve(process.cwd(), file), "utf8"));
  }
  catalog = window.AICAPluginCatalog;
  if (!catalog) {
    throw new Error("plugin_catalog.js did not expose window.AICAPluginCatalog");
  }
});

function mount(html) {
  const container = document.createElement("div");
  container.innerHTML = html;
  document.body.appendChild(container);
  return container;
}

function entry(overrides) {
  return {
    id: "alpha",
    plugin_id: "alpha",
    name: "Alpha Plugin",
    version: "1.0.0",
    latest_version: "1.0.0",
    is_latest: true,
    description: "Adds alpha support",
    author: "Acme",
    capabilities: ["PROJECT_READ"],
    permissions: ["read:project:files"],
    dependencies: ["base>=1.0"],
    compatibility: ">=0.8.0",
    compatible_with_app: true,
    app_version: "1.0.0",
    trust_state: "Trusted",
    trust_publisher: "acme",
    source: "https://plugins.example.com/alpha-1.0.0.zip",
    integrity: HASH,
    artifact_sha256: HASH,
    installed: false,
    installed_version: null,
    enabled: false,
    update_available: false,
    rollback_available: false,
    can_manage: false,
    actions: { install: false, update: false, rollback: false },
    ...overrides,
  };
}

describe("catalog renderer", () => {
  it("renders metadata, badges, dependencies and artifact integrity", () => {
    const container = mount(
      catalog.renderCatalog({ plugins: [entry()] }, { canManage: false })
    );

    expect(container.querySelector(".plugin-catalog-card")).not.toBeNull();
    expect(container.textContent).toContain("Alpha Plugin");
    expect(container.textContent).toContain("v1.0.0");
    expect(container.textContent).toContain("Acme");
    expect(container.textContent).toContain("read:project:files");
    expect(container.textContent).toContain("base>=1.0");
    expect(container.textContent).toContain(HASH);
    expect(container.textContent).toContain("plugins.example.com");
    expect(container.textContent).toContain("Trusted publisher");
    expect(container.textContent).toContain("Compatible with app 1.0.0");
  });

  it("shows install/update/rollback only to managers", () => {
    const ownerEntry = entry({
      can_manage: true,
      installed: true,
      installed_version: "0.9.0",
      update_available: true,
      update_version: "1.0.0",
      rollback_available: true,
      rollback_version: "0.8.0",
      actions: { install: false, update: true, rollback: true },
    });

    const member = mount(catalog.renderCatalog({ plugins: [ownerEntry] }, { canManage: false }));
    expect(member.querySelector("[data-catalog-action]")).toBeNull();

    const owner = mount(catalog.renderCatalog({ plugins: [ownerEntry] }, { canManage: true }));
    expect(owner.querySelector('[data-catalog-action="update"]')).not.toBeNull();
    expect(owner.querySelector('[data-catalog-action="rollback"]')).not.toBeNull();
    expect(owner.textContent).toContain("Update available: v1.0.0");
    expect(owner.textContent).toContain("Rollback target: v0.8.0");
  });

  it("escapes catalog-controlled text", () => {
    const container = mount(
      catalog.renderCatalog({
        plugins: [
          entry({
            name: '<img src=x onerror="alert(1)">',
            description: "<script>alert(2)</script>",
            author: "<b>bad</b>",
            dependencies: ['<script>alert(3)</script>'],
          }),
        ],
      })
    );

    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror="alert(1)">');
    expect(container.textContent).toContain("<script>alert(2)</script>");
  });

  it("renders an explicit empty state", () => {
    const container = mount(catalog.renderCatalog({ plugins: [] }));

    expect(container.querySelector(".marketplace-state-empty")).not.toBeNull();
    expect(container.textContent).toContain("No catalog entries available");
  });

  it("renders bounded pagination controls", () => {
    const html = catalog.paginationHtml({
      page: 2,
      pages: 3,
      total: 12,
      has_prev: true,
      has_next: true,
    });
    const container = mount(html);

    expect(container.textContent).toContain("Page 2 of 3");
    expect(container.textContent).toContain("12 entries");
    const buttons = container.querySelectorAll("[data-catalog-page]");
    expect(buttons).toHaveLength(2);
    expect(buttons[0].disabled).toBe(false);
    expect(buttons[1].disabled).toBe(false);
  });

  it("disables pagination at the bounds", () => {
    const container = mount(
      catalog.paginationHtml({
        page: 1,
        pages: 1,
        total: 1,
        has_prev: false,
        has_next: false,
      })
    );
    const buttons = container.querySelectorAll("[data-catalog-page]");

    expect(buttons[0].disabled).toBe(true);
    expect(buttons[1].disabled).toBe(true);
  });
});
