/* Prospecting CRM — small vanilla-JS layer: copy buttons, autosave, live
   search, row links, toasts. No dependencies. */

(function () {
  "use strict";

  // ---- toast ------------------------------------------------------------
  var toastEl = document.getElementById("toast");
  var toastTimer = null;

  function toast(message, isError) {
    if (!toastEl || !message) return;
    toastEl.textContent = message;
    toastEl.style.background = isError ? "#c0392b" : "";
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.hidden = true; }, 2600);
  }

  if (window.__initialToast) {
    toast(window.__initialToast);
    var url = new URL(window.location);
    url.searchParams.delete("toast");
    history.replaceState(null, "", url);
  }

  // ---- copy buttons (event delegation: works inside swapped tables) -----
  document.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-copy]");
    if (!btn) return;
    event.preventDefault();
    event.stopPropagation();
    navigator.clipboard.writeText(btn.getAttribute("data-copy")).then(
      function () { toast("Copied: " + btn.getAttribute("data-copy")); },
      function () { toast("Could not copy", true); }
    );
  });

  // ---- whole-row click navigation ---------------------------------------
  document.addEventListener("click", function (event) {
    var row = event.target.closest("tr.rowlink");
    if (!row) return;
    // Let real interactive elements win.
    if (event.target.closest("a, button, form, input, select, textarea")) return;
    window.location = row.getAttribute("data-href");
  });

  // ---- inline autosave ---------------------------------------------------
  function save(el) {
    if (el.__lastSaved === el.value) return;
    var entity = el.getAttribute("data-entity");
    var id = el.getAttribute("data-id");
    var field = el.getAttribute("data-field");
    fetch("/api/" + (entity === "company" ? "companies" : "prospects") + "/" + id, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ field: field, value: el.value })
    })
      .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
      .then(function (result) {
        if (result.ok && result.data.ok) {
          el.__lastSaved = el.value;
          el.classList.remove("save-error");
          el.classList.add("saved");
          setTimeout(function () { el.classList.remove("saved"); }, 900);
        } else {
          el.classList.add("save-error");
          toast(result.data.error || "Could not save", true);
        }
      })
      .catch(function () {
        el.classList.add("save-error");
        toast("Could not reach the app — is it still running?", true);
      });
  }

  document.querySelectorAll("[data-autosave]").forEach(function (el) {
    el.__lastSaved = el.value;
    var eventName = (el.tagName === "SELECT" || el.type === "date" || el.type === "number") ? "change" : "blur";
    el.addEventListener(eventName, function () { save(el); });
    if (el.tagName === "INPUT" && el.type !== "date") {
      el.addEventListener("keydown", function (event) {
        if (event.key === "Enter") { event.preventDefault(); el.blur(); }
      });
    }
  });

  // ---- kanban drag & drop -------------------------------------------------
  var dragged = null;

  document.addEventListener("dragstart", function (event) {
    var card = event.target.closest(".board-card");
    if (!card) return;
    dragged = card;
    card.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
  });

  document.addEventListener("dragend", function () {
    if (dragged) dragged.classList.remove("dragging");
    document.querySelectorAll(".board-cards.dropping").forEach(function (zone) {
      zone.classList.remove("dropping");
    });
  });

  document.addEventListener("dragover", function (event) {
    var zone = event.target.closest(".board-cards");
    if (!zone || !dragged) return;
    event.preventDefault();
    zone.classList.add("dropping");
  });

  document.addEventListener("dragleave", function (event) {
    var zone = event.target.closest(".board-cards");
    if (zone) zone.classList.remove("dropping");
  });

  document.addEventListener("drop", function (event) {
    var zone = event.target.closest(".board-cards");
    if (!zone || !dragged) return;
    event.preventDefault();
    zone.classList.remove("dropping");
    var newStatus = zone.getAttribute("data-status");
    var card = dragged;
    fetch("/api/prospects/" + card.getAttribute("data-id"), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ field: "status", value: newStatus })
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.ok) {
          zone.prepend(card);
          document.querySelectorAll(".board-col").forEach(function (col) {
            var counter = col.querySelector(".board-col-head .muted");
            if (counter) counter.textContent = col.querySelectorAll(".board-card").length;
          });
          toast("Moved to " + data.display);
        } else {
          toast(data.error || "Could not move card", true);
        }
      })
      .catch(function () { toast("Could not reach the app", true); });
  });

  // ---- live search on the prospects list ---------------------------------
  var searchInput = document.getElementById("search");
  var filtersForm = document.getElementById("filters");
  var results = document.getElementById("results");
  var searchTimer = null;

  if (searchInput && filtersForm && results) {
    searchInput.addEventListener("input", function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(function () {
        var params = new URLSearchParams(new FormData(filtersForm));
        params.set("partial", "1");
        fetch("/prospects?" + params.toString())
          .then(function (res) { return res.text(); })
          .then(function (html) {
            results.innerHTML = html;
            var count = results.querySelector("tbody");
            var counter = document.getElementById("result-count");
            if (count && counter) counter.textContent = count.getAttribute("data-count") || "";
            params.delete("partial");
            history.replaceState(null, "", "/prospects?" + params.toString());
          });
      }, 220);
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "/" && !event.target.closest("input, textarea, select")) {
        event.preventDefault();
        searchInput.focus();
        searchInput.select();
      }
    });
  }
})();
