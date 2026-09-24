/* Afterword dashboard enhancements. Everything works without this file. */
(function () {
  "use strict";
  document.addEventListener("change", function (event) {
    var box = event.target;
    if (!box.matches || !box.matches("[data-select-all]")) return;
    var form = box.form || document.getElementById("bulk");
    if (!form) return;
    form.querySelectorAll('input[name="id"]').forEach(function (item) {
      item.checked = box.checked;
    });
  });

  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("button[data-confirm]");
    if (button && !window.confirm(button.getAttribute("data-confirm"))) {
      event.preventDefault();
    }
  });

  document.addEventListener("submit", function (event) {
    var form = event.target;
    var submitter = event.submitter;
    if (!submitter || submitter.name !== "bulk") return;
    var action = form.querySelector('select[name="action"]');
    var chosen = form.querySelectorAll('input[name="id"]:checked').length;
    if (action && action.value === "delete" && chosen &&
        !window.confirm("Delete " + chosen + " comment" + (chosen === 1 ? "" : "s") + " permanently?")) {
      event.preventDefault();
    }
  });
})();
