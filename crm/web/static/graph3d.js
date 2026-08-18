/**
 * Offline 3D relationship graph for BDRs (Three.js r149, vendored).
 * Expects global THREE from /static/vendor/three.min.js
 */
(function () {
  "use strict";

  var COLORS = {
    person: 0x8fd94a,
    company: 0x2d6a4f,
    city: 0x40916c,
    region: 0x74c69d,
    source: 0x95a5a6,
    focus: 0xf4a261,
    edge: 0x52796f,
  };

  var TYPE_RADIUS = {
    person: 1.1,
    company: 1.8,
    city: 0.9,
    region: 1.3,
    source: 0.7,
  };

  function hashColor(type, focus) {
    if (focus) return COLORS.focus;
    return COLORS[type] || 0x888888;
  }

  function createLabelSprite(text) {
    var canvas = document.createElement("canvas");
    var ctx = canvas.getContext("2d");
    var font = "600 28px Segoe UI, system-ui, sans-serif";
    ctx.font = font;
    var pad = 16;
    var w = Math.ceil(ctx.measureText(text).width) + pad * 2;
    var h = 48;
    canvas.width = w;
    canvas.height = h;
    ctx.font = font;
    ctx.fillStyle = "rgba(22, 53, 40, 0.85)";
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(0, 0, w, h, 10);
    else ctx.rect(0, 0, w, h);
    ctx.fill();
    ctx.fillStyle = "#f4f7f2";
    ctx.textBaseline = "middle";
    ctx.fillText(text, pad, h / 2);
    var tex = new THREE.CanvasTexture(canvas);
    tex.minFilter = THREE.LinearFilter;
    var mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false });
    var sprite = new THREE.Sprite(mat);
    sprite.scale.set(w / 40, h / 40, 1);
    sprite.position.y = 2.2;
    return sprite;
  }

  function ForceGraph3D(container, options) {
    this.container = container;
    this.options = options || {};
    this.tooltip = container.querySelector("[data-graph-tooltip]") || this._makeTooltip();
    this.emptyEl = container.querySelector("[data-graph-empty]");
    this.nodes = [];
    this.edges = [];
    this.simNodes = [];
    this.simEdges = [];
    this.meshById = {};
    this.running = false;
    this._initThree();
    this._bindInput();
  }

  ForceGraph3D.prototype._makeTooltip = function () {
    var el = document.createElement("div");
    el.className = "graph3d-tooltip";
    el.hidden = true;
    this.container.appendChild(el);
    return el;
  };

  ForceGraph3D.prototype._initThree = function () {
    var w = this.container.clientWidth || 640;
    var h = this.container.clientHeight || 420;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0f241c);

    this.camera = new THREE.PerspectiveCamera(55, w / h, 0.1, 500);
    this.camera.position.set(0, 8, 28);

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setSize(w, h);
    this.container.insertBefore(this.renderer.domElement, this.container.firstChild);

    var ambient = new THREE.AmbientLight(0xffffff, 0.55);
    this.scene.add(ambient);
    var dir = new THREE.DirectionalLight(0xffffff, 0.65);
    dir.position.set(12, 20, 10);
    this.scene.add(dir);

    this.root = new THREE.Group();
    this.scene.add(this.root);

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this._drag = { active: false, moved: false, lx: 0, ly: 0 };
    this._theta = 0.35;
    this._phi = 0.45;
    this._radius = 32;
    this._updateCamera();
  };

  ForceGraph3D.prototype._updateCamera = function () {
    var r = this._radius;
    this.camera.position.x = r * Math.sin(this._phi) * Math.cos(this._theta);
    this.camera.position.y = r * Math.cos(this._phi);
    this.camera.position.z = r * Math.sin(this._phi) * Math.sin(this._theta);
    this.camera.lookAt(0, 0, 0);
  };

  ForceGraph3D.prototype._bindInput = function () {
    var self = this;
    var canvas = this.renderer.domElement;

    canvas.addEventListener("pointerdown", function (e) {
      self._drag.active = true;
      self._drag.moved = false;
      self._drag.lx = e.clientX;
      self._drag.ly = e.clientY;
      canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener("pointermove", function (e) {
      var rect = canvas.getBoundingClientRect();
      self.pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      self.pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
      self._hover();

      if (!self._drag.active) return;
      var dx = e.clientX - self._drag.lx;
      var dy = e.clientY - self._drag.ly;
      if (Math.abs(dx) + Math.abs(dy) > 3) self._drag.moved = true;
      self._drag.lx = e.clientX;
      self._drag.ly = e.clientY;
      self._theta += dx * 0.008;
      self._phi = Math.max(0.12, Math.min(Math.PI - 0.12, self._phi + dy * 0.008));
      self._updateCamera();
    });
    canvas.addEventListener("pointerup", function (e) {
      if (self._drag.active && !self._drag.moved) self._click();
      self._drag.active = false;
      try {
        canvas.releasePointerCapture(e.pointerId);
      } catch (err) {}
    });
    canvas.addEventListener(
      "wheel",
      function (e) {
        e.preventDefault();
        self._radius = Math.max(10, Math.min(80, self._radius + e.deltaY * 0.04));
        self._updateCamera();
      },
      { passive: false }
    );

    window.addEventListener("resize", function () {
      self.resize();
    });
  };

  ForceGraph3D.prototype.resize = function () {
    var w = this.container.clientWidth || 640;
    var h = this.container.clientHeight || 420;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
  };

  ForceGraph3D.prototype._pick = function () {
    this.raycaster.setFromCamera(this.pointer, this.camera);
    var meshes = Object.keys(this.meshById).map(function (id) {
      return this.meshById[id];
    }, this);
    var hits = this.raycaster.intersectObjects(meshes, false);
    return hits.length ? hits[0].object : null;
  };

  ForceGraph3D.prototype._hover = function () {
    var mesh = this._pick();
    if (!mesh || !mesh.userData.node) {
      this.tooltip.hidden = true;
      return;
    }
    var n = mesh.userData.node;
    var lines = ["<strong>" + escapeHtml(n.label) + "</strong>"];
    if (n.type === "person") {
      if (n.title) lines.push(escapeHtml(n.title));
      if (n.city || n.region) lines.push(escapeHtml([n.city, n.region].filter(Boolean).join(", ")));
      if (n.icp_score != null) lines.push("ICP " + n.icp_score);
    } else if (n.type === "company" && n.domain) {
      lines.push(escapeHtml(n.domain));
    } else if (n.type === "source") {
      lines.push("Evidence host");
    }
    lines.push('<span class="muted">' + escapeHtml(n.type) + "</span>");
    this.tooltip.innerHTML = lines.join("<br>");
    this.tooltip.hidden = false;
  };

  ForceGraph3D.prototype._click = function () {
    var mesh = this._pick();
    if (!mesh || !mesh.userData.node) return;
    var n = mesh.userData.node;
    if (n.href) {
      window.location.href = n.href;
    }
  };

  ForceGraph3D.prototype.clear = function () {
    while (this.root.children.length) {
      var child = this.root.children[0];
      this.root.remove(child);
      if (child.geometry) child.geometry.dispose();
      if (child.material) {
        if (child.material.map) child.material.map.dispose();
        child.material.dispose();
      }
    }
    this.meshById = {};
    this.simNodes = [];
    this.simEdges = [];
  };

  ForceGraph3D.prototype.loadData = function (data) {
    this.clear();
    var nodes = (data && data.nodes) || [];
    var edges = (data && data.edges) || [];
    var focusId = data && data.focus_id;

    if (this.emptyEl) {
      var empty = !nodes.length || (data.meta && data.meta.empty);
      this.emptyEl.hidden = !empty;
      this.renderer.domElement.style.opacity = empty ? "0.25" : "1";
    }
    if (!nodes.length) {
      this._startLoop();
      return;
    }

    var self = this;
    var idIndex = {};
    nodes.forEach(function (n, i) {
      idIndex[n.id] = i;
      var angle = (i / Math.max(nodes.length, 1)) * Math.PI * 2;
      var ring = n.type === "company" ? 0 : n.type === "person" ? 8 : n.type === "region" ? 14 : 11;
      self.simNodes.push({
        id: n.id,
        node: n,
        x: Math.cos(angle) * ring + (Math.random() - 0.5),
        y: (Math.random() - 0.5) * 4,
        z: Math.sin(angle) * ring + (Math.random() - 0.5),
        vx: 0,
        vy: 0,
        vz: 0,
      });
    });

    edges.forEach(function (e) {
      if (idIndex[e.source] == null || idIndex[e.target] == null) return;
      self.simEdges.push({
        source: idIndex[e.source],
        target: idIndex[e.target],
        type: e.type,
      });
    });

    // Build meshes
    this.simNodes.forEach(function (sn) {
      var n = sn.node;
      var r = TYPE_RADIUS[n.type] || 1;
      if (n.type === "person" && n.icp_score) {
        r += Math.min(0.8, (n.icp_score / 100) * 0.8);
      }
      var geo = new THREE.SphereGeometry(r, 20, 16);
      var mat = new THREE.MeshStandardMaterial({
        color: hashColor(n.type, n.focus || n.id === focusId),
        roughness: 0.45,
        metalness: 0.15,
      });
      var mesh = new THREE.Mesh(geo, mat);
      mesh.userData.node = n;
      mesh.position.set(sn.x, sn.y, sn.z);
      var label = createLabelSprite(truncate(n.label, 28));
      mesh.add(label);
      self.root.add(mesh);
      self.meshById[n.id] = mesh;
    });

    // Edge lines
    var positions = [];
    this.simEdges.forEach(function (e) {
      var a = self.simNodes[e.source];
      var b = self.simNodes[e.target];
      positions.push(a.x, a.y, a.z, b.x, b.y, b.z);
    });
    var lineGeo = new THREE.BufferGeometry();
    lineGeo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    var lineMat = new THREE.LineBasicMaterial({
      color: COLORS.edge,
      transparent: true,
      opacity: 0.45,
    });
    this.edgeLines = new THREE.LineSegments(lineGeo, lineMat);
    this.root.add(this.edgeLines);

    this._tick = 0;
    this._startLoop();
  };

  ForceGraph3D.prototype._stepPhysics = function () {
    var nodes = this.simNodes;
    var edges = this.simEdges;
    var i, j, a, b, dx, dy, dz, dist, f;
    var repulsion = 28;
    var spring = 0.045;
    var rest = 7;
    var damping = 0.86;
    var centerPull = 0.012;

    for (i = 0; i < nodes.length; i++) {
      a = nodes[i];
      for (j = i + 1; j < nodes.length; j++) {
        b = nodes[j];
        dx = a.x - b.x;
        dy = a.y - b.y;
        dz = a.z - b.z;
        dist = Math.sqrt(dx * dx + dy * dy + dz * dz) + 0.05;
        f = repulsion / (dist * dist);
        dx = (dx / dist) * f;
        dy = (dy / dist) * f;
        dz = (dz / dist) * f;
        a.vx += dx;
        a.vy += dy;
        a.vz += dz;
        b.vx -= dx;
        b.vy -= dy;
        b.vz -= dz;
      }
    }

    for (i = 0; i < edges.length; i++) {
      var e = edges[i];
      a = nodes[e.source];
      b = nodes[e.target];
      dx = b.x - a.x;
      dy = b.y - a.y;
      dz = b.z - a.z;
      dist = Math.sqrt(dx * dx + dy * dy + dz * dz) + 0.05;
      var ideal = e.type === "colleague_of" ? 5.5 : e.type === "works_at" ? 6.5 : rest;
      f = (dist - ideal) * spring;
      dx = (dx / dist) * f;
      dy = (dy / dist) * f;
      dz = (dz / dist) * f;
      a.vx += dx;
      a.vy += dy;
      a.vz += dz;
      b.vx -= dx;
      b.vy -= dy;
      b.vz -= dz;
    }

    for (i = 0; i < nodes.length; i++) {
      a = nodes[i];
      a.vx -= a.x * centerPull;
      a.vy -= a.y * centerPull;
      a.vz -= a.z * centerPull;
      a.vx *= damping;
      a.vy *= damping;
      a.vz *= damping;
      a.x += a.vx;
      a.y += a.vy;
      a.z += a.vz;
      var mesh = this.meshById[a.id];
      if (mesh) mesh.position.set(a.x, a.y, a.z);
    }

    if (this.edgeLines) {
      var pos = this.edgeLines.geometry.attributes.position.array;
      var k = 0;
      for (i = 0; i < edges.length; i++) {
        a = nodes[edges[i].source];
        b = nodes[edges[i].target];
        pos[k++] = a.x;
        pos[k++] = a.y;
        pos[k++] = a.z;
        pos[k++] = b.x;
        pos[k++] = b.y;
        pos[k++] = b.z;
      }
      this.edgeLines.geometry.attributes.position.needsUpdate = true;
    }
  };

  ForceGraph3D.prototype._startLoop = function () {
    if (this.running) return;
    this.running = true;
    var self = this;
    function frame() {
      if (!self.running) return;
      requestAnimationFrame(frame);
      self._tick = (self._tick || 0) + 1;
      if (self._tick < 220) self._stepPhysics();
      self.renderer.render(self.scene, self.camera);
    }
    frame();
  };

  ForceGraph3D.prototype.destroy = function () {
    this.running = false;
    this.clear();
    if (this.renderer) {
      this.renderer.dispose();
      if (this.renderer.domElement && this.renderer.domElement.parentNode) {
        this.renderer.domElement.parentNode.removeChild(this.renderer.domElement);
      }
    }
  };

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function truncate(s, n) {
    s = String(s || "");
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
  }

  function fetchGraph(params) {
    var q = new URLSearchParams(params || {});
    return fetch("/api/graph?" + q.toString()).then(function (r) {
      if (!r.ok) throw new Error("graph request failed");
      return r.json();
    });
  }

  function mountFromElement(el) {
    if (!el || typeof THREE === "undefined") return null;
    var graph = new ForceGraph3D(el);
    var params = {};
    if (el.dataset.scope) params.scope = el.dataset.scope;
    if (el.dataset.companyId) params.company_id = el.dataset.companyId;
    if (el.dataset.focusProspectId) params.focus_prospect_id = el.dataset.focusProspectId;
    if (el.dataset.region) params.region = el.dataset.region;
    if (el.dataset.status) params.status = el.dataset.status;

    fetchGraph(params)
      .then(function (data) {
        graph.loadData(data);
      })
      .catch(function () {
        if (graph.emptyEl) {
          graph.emptyEl.hidden = false;
          graph.emptyEl.textContent = "Could not load network graph.";
        }
      });

    el._graph3d = graph;
    return graph;
  }

  function initAll() {
    document.querySelectorAll("[data-graph3d]").forEach(mountFromElement);
  }

  window.Graph3D = {
    ForceGraph3D: ForceGraph3D,
    fetchGraph: fetchGraph,
    mount: mountFromElement,
    initAll: initAll,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }
})();
