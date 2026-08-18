/**
 * BDR company spider map — 2D, company-centered cards (no WebGL).
 * Offline-only; no CDN.
 */
(function () {
  "use strict";

  var ROLE_CLASS = {
    economic_buyer: "role-economic",
    influencer: "role-influencer",
    coach: "role-coach",
    contact: "role-contact",
    unknown: "role-unknown",
  };

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fetchGraph(params) {
    var q = new URLSearchParams(params || {});
    return fetch("/api/graph?" + q.toString()).then(function (r) {
      if (!r.ok) throw new Error("graph request failed");
      return r.json();
    });
  }

  function renderGaps(el, gaps) {
    if (!el) return;
    if (!gaps || !gaps.length) {
      el.innerHTML =
        '<p class="network-gaps-ok">Coverage looks solid for a first dial — still confirm phone before calling.</p>';
      return;
    }
    var html = "<ul class=\"network-gaps-list\">";
    gaps.forEach(function (g) {
      html +=
        '<li class="gap-' +
        escapeHtml(g.severity || "medium") +
        '"><strong>' +
        escapeHtml((g.severity || "").toUpperCase()) +
        "</strong> " +
        escapeHtml(g.text) +
        "</li>";
    });
    html += "</ul>";
    el.innerHTML = html;
  }

  function personCardHtml(p) {
    var roleClass = ROLE_CLASS[p.role] || "role-unknown";
    var photo = p.photo_url
      ? '<img class="spider-avatar" src="' +
        escapeHtml(p.photo_url) +
        '" alt="">'
      : '<span class="spider-avatar spider-avatar-empty">' +
        escapeHtml((p.label || "?").slice(0, 1)) +
        "</span>";
    var flags = [];
    if (p.phone) flags.push("phone");
    if (p.linkedin_url) flags.push("LinkedIn");
    if (p.city) flags.push(p.city);
    var meta = [p.title || "No title", flags.join(" · ")].filter(Boolean).join(" · ");
    return (
      '<a class="spider-card person-card ' +
      roleClass +
      (p.focus ? " is-focus" : "") +
      (p.closed ? " is-closed" : "") +
      '" href="' +
      escapeHtml(p.href || "#") +
      '" data-prospect-id="' +
      escapeHtml(p.prospect_id) +
      '">' +
      photo +
      '<span class="spider-card-body">' +
      '<span class="spider-card-name">' +
      escapeHtml(p.label) +
      "</span>" +
      '<span class="spider-card-role">' +
      escapeHtml(p.role_label || "") +
      (p.icp_score != null ? " · ICP " + p.icp_score : "") +
      "</span>" +
      '<span class="spider-card-meta">' +
      escapeHtml(meta) +
      "</span>" +
      "</span></a>"
    );
  }

  function companyCardHtml(company) {
    if (!company) return "";
    var bits = [company.domain, company.region, company.size_band].filter(Boolean);
    return (
      '<div class="spider-card company-card">' +
      '<span class="spider-card-name">' +
      escapeHtml(company.name) +
      "</span>" +
      '<span class="spider-card-meta">' +
      escapeHtml(bits.join(" · ") || "Company") +
      "</span></div>"
    );
  }

  function layoutPositions(n, cx, cy, radius) {
    var out = [];
    if (n <= 0) return out;
    if (n === 1) {
      out.push({ x: cx, y: cy - radius * 0.55 });
      return out;
    }
    var start = -Math.PI / 2;
    for (var i = 0; i < n; i++) {
      var a = start + (i / n) * Math.PI * 2;
      out.push({ x: cx + Math.cos(a) * radius, y: cy + Math.sin(a) * radius });
    }
    return out;
  }

  function renderSpider(container, data) {
    var stage = container.querySelector("[data-spider-stage]") || container;
    var gapsEl = container.querySelector("[data-spider-gaps]");
    var emptyEl = container.querySelector("[data-spider-empty]");
    var people = (data && data.people) || [];
    var company = data && data.company;

    renderGaps(gapsEl, (data && data.gaps) || []);

    if (!company || !people.length) {
      if (emptyEl) emptyEl.hidden = false;
      stage.innerHTML = "";
      if (emptyEl && !people.length) {
        emptyEl.textContent =
          "No contacts at this company yet — add a colleague or run the enricher.";
      }
      return;
    }
    if (emptyEl) emptyEl.hidden = true;

    var w = Math.max(stage.clientWidth || container.clientWidth || 640, 480);
    var h = Math.max(420, Math.min(560, 280 + people.length * 28));
    var cx = w / 2;
    var cy = h / 2;
    var radius = Math.min(w, h) * 0.32;
    var positions = layoutPositions(people.length, cx, cy, radius);

    var svgLines = "";
    people.forEach(function (p, i) {
      var pos = positions[i];
      svgLines +=
        '<line x1="' +
        cx +
        '" y1="' +
        cy +
        '" x2="' +
        pos.x +
        '" y2="' +
        pos.y +
        '" class="spider-edge"/>';
    });

    var cards = people
      .map(function (p, i) {
        var pos = positions[i];
        return (
          '<div class="spider-node" style="left:' +
          pos.x +
          "px;top:" +
          pos.y +
          'px">' +
          personCardHtml(p) +
          "</div>"
        );
      })
      .join("");

    stage.style.height = h + "px";
    stage.innerHTML =
      '<svg class="spider-svg" width="' +
      w +
      '" height="' +
      h +
      '" viewBox="0 0 ' +
      w +
      " " +
      h +
      '">' +
      svgLines +
      "</svg>" +
      '<div class="spider-node spider-center" style="left:' +
      cx +
      "px;top:" +
      cy +
      'px">' +
      companyCardHtml(company) +
      "</div>" +
      cards;

    var chips = container.querySelector("[data-spider-chips]");
    if (chips) {
      var cityBits = (data.cities || []).map(function (c) {
        return '<span class="chip">' + escapeHtml(c) + "</span>";
      });
      var regionBits = (data.regions || []).map(function (r) {
        return '<span class="chip chip-region">' + escapeHtml(r) + "</span>";
      });
      chips.innerHTML =
        cityBits.concat(regionBits).join("") ||
        '<span class="muted small">No city/region on file</span>';
    }
  }

  function renderAtlas(container, data) {
    var list = container.querySelector("[data-atlas-list]") || container;
    var emptyEl = container.querySelector("[data-spider-empty]");
    var accounts = (data && data.accounts) || [];
    if (!accounts.length) {
      if (emptyEl) {
        emptyEl.hidden = false;
        emptyEl.textContent = "No accounts match these filters.";
      }
      list.innerHTML = "";
      return;
    }
    if (emptyEl) emptyEl.hidden = true;

    list.innerHTML = accounts
      .map(function (a) {
        var people = (a.people || [])
          .map(function (p) {
            return (
              '<li><a href="' +
              escapeHtml(p.href) +
              '">' +
              escapeHtml(p.name) +
              "</a> " +
              '<span class="muted small">' +
              escapeHtml(p.title || "") +
              "</span></li>"
            );
          })
          .join("");
        var gap =
          a.top_gap
            ? '<p class="atlas-gap">' + escapeHtml(a.top_gap) + "</p>"
            : '<p class="atlas-gap ok">No major coverage gaps flagged</p>';
        return (
          '<article class="atlas-account" data-company-id="' +
          escapeHtml(a.company_id) +
          '">' +
          "<header>" +
          "<h3>" +
          escapeHtml(a.name) +
          "</h3>" +
          '<span class="muted small">' +
          escapeHtml(a.region || "No region") +
          " · " +
          a.person_count +
          " contact" +
          (a.person_count === 1 ? "" : "s") +
          (a.gap_count ? " · " + a.gap_count + " gap" + (a.gap_count === 1 ? "" : "s") : "") +
          "</span></header>" +
          gap +
          "<ul class=\"atlas-people\">" +
          people +
          "</ul>" +
          '<button type="button" class="btn btn-sm" data-open-spider="' +
          escapeHtml(a.company_id) +
          '">Open spider map</button>' +
          '<div class="atlas-spider" data-atlas-spider hidden></div>' +
          "</article>"
        );
      })
      .join("");

    list.querySelectorAll("[data-open-spider]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var article = btn.closest(".atlas-account");
        var holder = article.querySelector("[data-atlas-spider]");
        var cid = btn.getAttribute("data-open-spider");
        if (!holder) return;
        if (!holder.hidden && holder.dataset.loaded === cid) {
          holder.hidden = true;
          btn.textContent = "Open spider map";
          return;
        }
        holder.hidden = false;
        btn.textContent = "Hide spider map";
        holder.innerHTML =
          '<div class="network-spider" data-spider-root>' +
          '<div class="spider-stage" data-spider-stage></div>' +
          '<div class="spider-empty" data-spider-empty hidden></div>' +
          "</div>";
        fetchGraph({ company_id: cid })
          .then(function (g) {
            holder.dataset.loaded = cid;
            renderSpider(holder.querySelector("[data-spider-root]"), g);
          })
          .catch(function () {
            holder.innerHTML = '<p class="muted">Could not load map.</p>';
          });
      });
    });
  }

  function mount(el) {
    if (!el) return null;
    var params = {};
    if (el.dataset.scope) params.scope = el.dataset.scope;
    if (el.dataset.companyId) params.company_id = el.dataset.companyId;
    if (el.dataset.focusProspectId) params.focus_prospect_id = el.dataset.focusProspectId;
    if (el.dataset.region) params.region = el.dataset.region;
    if (el.dataset.status) params.status = el.dataset.status;

    fetchGraph(params)
      .then(function (data) {
        if ((data.layout || params.scope) === "atlas" || params.scope === "atlas") {
          renderAtlas(el, data);
        } else {
          renderSpider(el, data);
        }
      })
      .catch(function () {
        var emptyEl = el.querySelector("[data-spider-empty]");
        if (emptyEl) {
          emptyEl.hidden = false;
          emptyEl.textContent = "Could not load network map.";
        }
      });
    return el;
  }

  function initAll() {
    document.querySelectorAll("[data-network-map]").forEach(mount);
  }

  window.NetworkMap = {
    mount: mount,
    fetchGraph: fetchGraph,
    renderSpider: renderSpider,
    renderAtlas: renderAtlas,
    initAll: initAll,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }
})();
