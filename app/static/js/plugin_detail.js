// AI Code Assistant — plugin detail page (#417)
// Renders trust/compatibility badges and wires the owner-only install, update,
// enable, disable, and uninstall controls. The backend is authoritative.

(function () {
  "use strict";

  var PLUGIN = window.AICA_PLUGIN_DETAIL || null;
  var root = document.querySelector(".plugin-detail");
  if (!PLUGIN || !root) return;

  var WORKSPACE_ID = root.dataset.workspaceId;
  var PLUGIN_ID = root.dataset.pluginId;

  function getCsrf() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) return meta.content;
    var input = document.querySelector('input[name="csrf_token"]');
    return input ? input.value : "";
  }

  function api(url, options) {
    options = options || {};
    options.headers = Object.assign({}, options.headers || {}, {
      "X-CSRFToken": getCsrf(),
    });
    return fetch(url, options).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) {
          throw new Error(data && data.error ? data.error : "Request failed (" + response.status + ").");
        }
        return data;
      });
    });
  }

  function showMessage(message, category) {
    var el = document.getElementById("plugin-detail-message");
    if (!el) return;
    el.hidden = false;
    el.className = "flash flash-" + (category || "info");
    el.textContent = message;
  }

  function basePath() {
    return "/plugins/api/workspaces/" + WORKSPACE_ID + "/plugins/" + encodeURIComponent(PLUGIN_ID);
  }

  function run(path, options, success) {
    api(path, options)
      .then(function () {
        if (success) success();
        else window.location.reload();
      })
      .catch(function (error) { showMessage(error.message, "error"); });
  }

  function install() {
    run(basePath() + "/install", { method: "POST" });
  }

  function setEnabled(enabled) {
    run(basePath() + (enabled ? "/enable" : "/disable"), { method: "POST" });
  }

  function uninstall() {
    if (!window.confirm("Uninstall " + PLUGIN.name + " from this workspace?")) return;
    api(basePath() + "/uninstall", { method: "POST" })
      .then(function () { window.location.href = "/plugins/"; })
      .catch(function (error) { showMessage(error.message, "error"); });
  }

  function saveUpdate() {
    var textarea = document.getElementById("plugin-update-manifest");
    var manifest;
    try {
      manifest = JSON.parse(textarea.value);
    } catch (e) {
      showMessage("Manifest is not valid JSON.", "error");
      return;
    }
    run(basePath() + "/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ manifest: manifest }),
    });
  }

  function updateFromCatalog(version) {
    if (!version) return;
    api(basePath() + "/update-from-catalog", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version: version }),
    })
      .then(function () { window.location.reload(); })
      .catch(function (error) { showMessage(error.message, "error"); });
  }

  function rollback(version) {
    var label = version ? "v" + version : "the previous version";
    if (!window.confirm("Roll back " + PLUGIN.name + " to " + label + "?")) return;
    api(basePath() + "/rollback", { method: "POST" })
      .then(function () { window.location.reload(); })
      .catch(function (error) { showMessage(error.message, "error"); });
  }

  function checkUpdates() {
    api(
      "/plugins/api/workspaces/" + WORKSPACE_ID + "/plugins/check-updates",
      { method: "POST" }
    )
      .then(function (data) {
        var count = data && typeof data.created === "number" ? data.created : 0;
        showMessage(
          count > 0
            ? count + " update notification(s) sent to the workspace owner."
            : "No new plugin updates to notify.",
          "info"
        );
      })
      .catch(function (error) { showMessage(error.message, "error"); });
  }

  function bind(id, handler) {
    var el = document.getElementById(id);
    if (el) el.addEventListener("click", handler);
  }

  bind("plugin-detail-install", install);
  bind("plugin-detail-enable", function () { setEnabled(true); });
  bind("plugin-detail-disable", function () { setEnabled(false); });
  bind("plugin-detail-uninstall", uninstall);
  bind("plugin-detail-update-save", saveUpdate);
  bind("plugin-detail-update-toggle", function () {
    var box = document.getElementById("plugin-update-box");
    if (box) box.hidden = !box.hidden;
  });
  bind("plugin-detail-update-available", function () {
    updateFromCatalog(this.dataset.version);
  });
  bind("plugin-detail-rollback", function () {
    rollback(this.dataset.version);
  });
  bind("plugin-detail-check-updates", checkUpdates);

  var badges = document.getElementById("plugin-detail-badges");
  if (badges && window.AICAPluginBadges && typeof window.AICAPluginBadges.render === "function") {
    badges.innerHTML = window.AICAPluginBadges.render(PLUGIN);
  }
})();
