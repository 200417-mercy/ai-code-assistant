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

  scopeEl.addEventListener("change", syncScope);
  syncScope();

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    showError("");
    var tokens = document.getElementById("budget-tokens").value.trim();
    var cost = document.getElementById("budget-cost").value.trim();
    if (!tokens && !cost) {
      showError("Set a token limit, a cost limit, or both.");
      return;
    }
    var payload = {
      scope: scopeEl.value,
      token_limit: tokens || null,
      cost_limit: cost || null,
      warn_threshold: document.getElementById("budget-threshold").value || null,
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
