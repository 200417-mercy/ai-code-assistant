// Budget configuration UI (Phase 10).
(function () {
  "use strict";

  var form = document.getElementById("budget-form");
  if (!form) return;

  var scopeEl = document.getElementById("budget-scope");
  var workspaceField = document.getElementById("budget-workspace-field");
  var workspaceEl = document.getElementById("budget-workspace");
  var errorEl = document.getElementById("budget-error");
  var rowsEl = document.getElementById("budget-rows");
  var tokenInput = document.getElementById("budget-tokens");
  var costInput = document.getElementById("budget-cost");
  var thresholdInput = document.getElementById("budget-threshold");

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
  }

  function showError(message) {
    if (!errorEl) return;
    errorEl.hidden = !message;
    errorEl.textContent = message || "";
  }

  function syncScope() {
    if (!workspaceField) return;
    workspaceField.hidden = scopeEl.value !== "workspace";
  }

  function existingRow() {
    if (!rowsEl) return null;
    var scope = scopeEl.value;
    var scopeId = scope === "workspace" ? workspaceEl.value : "";
    var rows = rowsEl.querySelectorAll("tr[data-scope]");
    for (var i = 0; i < rows.length; i += 1) {
      if (
        rows[i].dataset.scope === scope &&
        String(rows[i].dataset.scopeId) === String(scopeId)
      ) {
        return rows[i];
      }
    }
    return null;
  }

  // Prefill the form with the existing budget for the selected scope so the
  // UI edits a configuration instead of silently replacing unknown values.
  function syncForm() {
    var row = existingRow();
    if (!row) {
      tokenInput.value = "";
      costInput.value = "";
      thresholdInput.value = "0.8";
      return;
    }
    tokenInput.value = row.dataset.tokenLimit || "";
    costInput.value = row.dataset.costLimit || "";
    thresholdInput.value = row.dataset.warnThreshold || "0.8";
  }

  scopeEl.addEventListener("change", function () {
    syncScope();
    syncForm();
  });
  if (workspaceEl) workspaceEl.addEventListener("change", syncForm);
  syncScope();
  syncForm();

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    showError("");
    var tokens = tokenInput.value.trim();
    var cost = costInput.value.trim();
    if (!tokens && !cost) {
      showError("Set a token limit, a cost limit, or both.");
      return;
    }
    var payload = {
      scope: scopeEl.value,
      token_limit: tokens || null,
      cost_limit: cost || null,
      warn_threshold: thresholdInput.value || null,
    };
    if (scopeEl.value === "workspace") {
      payload.workspace_id = workspaceEl.value;
    }
    fetch("/billing/api/budgets", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
      },
      body: JSON.stringify(payload),
    })
      .then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok) throw new Error(data.error || "Could not save the budget.");
          return data;
        });
      })
      .then(function () {
        window.location.reload();
      })
      .catch(function (error) {
        showError(error.message);
      });
  });

  if (rowsEl) {
    rowsEl.addEventListener("click", function (event) {
      var button = event.target.closest(".budget-delete");
      if (!button) return;
      if (!window.confirm("Delete this budget?")) return;
      var id = button.dataset.budgetId;
      fetch("/billing/api/budgets/" + id, {
        method: "DELETE",
        headers: { "X-CSRFToken": csrfToken() },
      })
        .then(function (response) {
          if (!response.ok) throw new Error("Could not delete the budget.");
          window.location.reload();
        })
        .catch(function (error) {
          showError(error.message);
        });
    });
  }
})();
