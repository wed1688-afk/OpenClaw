/* Agent Office -- renders the Claude Code event ledger as a floor of clerks.
 *
 * The server hands us a snapshot: who is on the floor, which station they are
 * standing at, what they are doing, and the recent log.  Everything here is
 * presentation: sprites walk toward the station the snapshot puts them at, and
 * the panels mirror the same data as text.
 */
(function () {
  "use strict";

  // ---------------------------------------------------------------- layout
  var GRID = { w: 14, h: 11 };
  var TILE_W = 62, TILE_H = 31, TILE_Z = 26;

  var STATIONS = {
    records:     { anchor: [1.1, 1.1], stand: [2.5, 2.2] },
    drafting:    { anchor: [6.2, 0.5], stand: [6.7, 1.9] },
    server_room: { anchor: [11.0, 0.7], stand: [11.9, 2.7] },
    mailroom:    { anchor: [12.9, 4.3], stand: [12.4, 6.9] },
    war_room:    { anchor: [10.8, 8.3], stand: [10.2, 8.6] },
    reception:   { anchor: [0.7, 6.4], stand: [2.0, 6.6] },
    break_room:  { anchor: [0.8, 9.3], stand: [2.1, 9.3] },
    door:        { anchor: [-0.5, 3.8], stand: [0.5, 4.0] }
  };

  // Everything the page says for itself. Anything the office says about its
  // own work -- room names, activities, the log -- is worded by the server and
  // arrives in the snapshot already translated.
  var TEXT = {
    "zh-Hant": {
      sub: "Claude Code 正在做的事，畫成一整層樓的職員",
      chips: { headcount: "在場", busy: "忙碌", tools: "工具呼叫", errors: "出狀況", prompts: "工單", hires: "報到" },
      panels: { roster: "在場人員", tickets: "工單板", log: "活動紀錄" },
      conn: { live: "即時連線", connecting: "連線中…", offline: "已斷線" },
      recentre: "重新置中",
      empty: "辦公室現在沒有人。在 Claude Code 跑點東西，職員就會上工；或用 demo 模式看一場排演的班。",
      badge: { working: "工作中", waiting: "等簽名", blocked: "卡住了", arriving: "新人報到", gone: "下班中" },
      ticket: { open: "未結", done: "已交件", empty: "（沒有內容）" },
      puff: { done: "完成", arrive: "報到", error: "！" },
      busyTitle: function (n) { return "Agent Office — " + n + " 人忙碌中"; },
      clock: function (s) {
        if (s < 60) return s + " 秒";
        var m = Math.floor(s / 60);
        if (m < 60) return m + " 分 " + String(s % 60).padStart(2, "0") + " 秒";
        return Math.floor(m / 60) + " 時 " + String(m % 60).padStart(2, "0") + " 分";
      }
    },
    en: {
      sub: "what Claude Code is doing, as a floor of clerks",
      chips: { headcount: "on the floor", busy: "busy", tools: "tool calls", errors: "snags", prompts: "work orders", hires: "hires" },
      panels: { roster: "On the floor", tickets: "Job board", log: "Activity" },
      conn: { live: "live", connecting: "connecting", offline: "offline" },
      recentre: "Re-centre",
      empty: "The office is empty. Run anything in Claude Code and the clerks will clock in, or start the server with demo to watch a staged shift.",
      badge: { working: "working", waiting: "signature?", blocked: "snag", arriving: "new hire", gone: "clocking out" },
      ticket: { open: "open", done: "delivered", empty: "(no text)" },
      puff: { done: "done", arrive: "hired", error: "!" },
      busyTitle: function (n) { return "Agent Office — " + n + " busy"; },
      clock: function (s) {
        if (s < 60) return s + "s";
        var m = Math.floor(s / 60);
        if (m < 60) return m + "m " + String(s % 60).padStart(2, "0") + "s";
        return Math.floor(m / 60) + "h " + String(m % 60).padStart(2, "0") + "m";
      }
    }
  };

  // The language this page is asking for, forwarded to every API call so the
  // server words the snapshot to match the chrome.
  var LANG_PARAM = new URLSearchParams(location.search).get("lang");
  var locale = (LANG_PARAM && TEXT[LANG_PARAM]) ? LANG_PARAM
    : (TEXT[document.documentElement.lang] ? document.documentElement.lang : "zh-Hant");

  // Named `ui`, not `t`: `t` is the animation clock in the drawing code.
  function ui() { return TEXT[locale] || TEXT["zh-Hant"]; }

  function api(path) {
    return LANG_PARAM ? path + "?lang=" + encodeURIComponent(LANG_PARAM) : path;
  }

  var CANVAS_FONT = 'ui-sans-serif, system-ui, "PingFang TC", "Noto Sans TC", "Microsoft JhengHei", sans-serif';

  var ROLE_COLORS = {
    lead: "#f0b429",
    Explore: "#2dd4bf",
    Plan: "#a78bfa",
    "general-purpose": "#60a5fa",
    claude: "#60a5fa",
    "code-review": "#f472b6",
    fork: "#fb923c"
  };

  function roleColor(role) {
    if (ROLE_COLORS[role]) return ROLE_COLORS[role];
    var hash = 0;
    for (var i = 0; i < (role || "").length; i++) hash = (hash * 31 + role.charCodeAt(i)) % 360;
    return "hsl(" + hash + ", 62%, 62%)";
  }

  function deskSpot(index) {
    var col = index % 3, row = Math.floor(index / 3);
    return [4.4 + col * 2.2, 3.4 + row * 2.0];
  }

  // ---------------------------------------------------------------- canvas
  var canvas = document.getElementById("office");
  var ctx = canvas.getContext("2d");
  var camera = { scale: 1, x: 0, y: 0, userScale: 1, userX: 0, userY: 0 };
  var theme = {};

  function readTheme() {
    var css = getComputedStyle(document.documentElement);
    ["floor-a", "floor-b", "wall", "line", "ink", "ink-dim", "ink-faint", "bg", "bg-soft", "accent", "ok", "bad", "warn"]
      .forEach(function (name) { theme[name] = css.getPropertyValue("--" + name).trim(); });
  }

  function resize() {
    var dpr = window.devicePixelRatio || 1;
    var rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    fitCamera(rect.width, rect.height);
  }

  function fitCamera(width, height) {
    var spanX = (GRID.w + GRID.h) * TILE_W / 2;
    var spanY = (GRID.w + GRID.h) * TILE_H / 2 + TILE_Z * 3;
    var scale = Math.min((width - 48) / spanX, (height - 48) / spanY);
    camera.scale = Math.max(0.42, Math.min(1.5, scale));
    camera.x = width / 2 + (GRID.h - GRID.w) * TILE_W / 4 * camera.scale;
    camera.y = height / 2 - (GRID.w + GRID.h) * TILE_H / 4 * camera.scale + 26 * camera.scale;
  }

  function project(gx, gy, gz) {
    var s = camera.scale * camera.userScale;
    return {
      x: camera.x + camera.userX + (gx - gy) * (TILE_W / 2) * s,
      y: camera.y + camera.userY + (gx + gy) * (TILE_H / 2) * s - (gz || 0) * TILE_Z * s
    };
  }

  function quad(points, fill, stroke) {
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    for (var i = 1; i < points.length; i++) ctx.lineTo(points[i].x, points[i].y);
    ctx.closePath();
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 1; ctx.stroke(); }
  }

  function shade(hex, amount) {
    var m = /^#?([0-9a-f]{6})$/i.exec((hex || "").trim());
    if (!m) return hex || "#888";
    var num = parseInt(m[1], 16);
    var parts = [num >> 16 & 255, num >> 8 & 255, num & 255].map(function (v) {
      return Math.max(0, Math.min(255, Math.round(amount < 0 ? v * (1 + amount) : v + (255 - v) * amount)));
    });
    return "rgb(" + parts.join(",") + ")";
  }

  function box(gx, gy, w, d, h, color) {
    var top = [project(gx, gy, h), project(gx + w, gy, h), project(gx + w, gy + d, h), project(gx, gy + d, h)];
    var right = [project(gx + w, gy, h), project(gx + w, gy + d, h), project(gx + w, gy + d, 0), project(gx + w, gy, 0)];
    var left = [project(gx, gy + d, h), project(gx + w, gy + d, h), project(gx + w, gy + d, 0), project(gx, gy + d, 0)];
    quad(left, shade(color, -0.36));
    quad(right, shade(color, -0.18));
    quad(top, shade(color, 0.08));
    return { top: top, right: right, left: left };
  }

  function label(gx, gy, text, color, size, gz) {
    var p = project(gx, gy, gz || 0);
    ctx.fillStyle = color;
    ctx.font = "600 " + (size || 10) + "px " + CANVAS_FONT;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, p.x, p.y);
  }

  // ------------------------------------------------------------- furniture
  function drawFloor() {
    for (var gx = 0; gx < GRID.w; gx++) {
      for (var gy = 0; gy < GRID.h; gy++) {
        var tile = [project(gx, gy, 0), project(gx + 1, gy, 0), project(gx + 1, gy + 1, 0), project(gx, gy + 1, 0)];
        quad(tile, (gx + gy) % 2 ? theme["floor-a"] : theme["floor-b"]);
      }
    }
    // rugs under the working areas
    rug(0.2, 0.2, 3.4, 3.2, theme.accent, 0.05);
    rug(4.1, 3.0, 6.6, 6.4, theme.ok, 0.035);
    rug(9.8, 7.0, 3.6, 3.4, theme["ink-faint"], 0.05);

    var wallHeight = 2.6;
    quad([project(0, 0, wallHeight), project(GRID.w, 0, wallHeight), project(GRID.w, 0, 0), project(0, 0, 0)], theme.wall);
    quad([project(0, 0, wallHeight), project(0, GRID.h, wallHeight), project(0, GRID.h, 0), project(0, 0, 0)], shade(theme.wall, -0.2));
    for (var w = 1.2; w < GRID.w - 1; w += 3.1) {
      quad([project(w, 0, 2.1), project(w + 1.7, 0, 2.1), project(w + 1.7, 0, 1.15), project(w, 0, 1.15)],
        "rgba(120, 180, 240, 0.16)");
    }
    // the front door, cut into the left wall
    quad([project(0, 3.5, 1.9), project(0, 4.6, 1.9), project(0, 4.6, 0), project(0, 3.5, 0)], shade(theme.wall, -0.45));
    quad([project(0, 3.6, 1.8), project(0, 4.5, 1.8), project(0, 4.5, 0.05), project(0, 3.6, 0.05)], shade(theme.accent, -0.55));
  }

  function rug(gx, gy, w, d, color, alpha) {
    ctx.save();
    ctx.globalAlpha = alpha;
    quad([project(gx, gy, 0), project(gx + w, gy, 0), project(gx + w, gy + d, 0), project(gx, gy + d, 0)], color);
    ctx.restore();
  }

  function drawStation(name, occupants, t) {
    var anchor = STATIONS[name].anchor;
    var gx = anchor[0], gy = anchor[1];
    var hot = occupants > 0;
    var wood = "#6b4f3a", metal = "#4b5563", paint = "#3f4c5f";

    if (name === "records") {
      for (var i = 0; i < 3; i++) {
        var faces = box(gx + i * 0.95, gy, 0.78, 0.6, 1.25, metal);
        ctx.strokeStyle = "rgba(255,255,255,0.14)";
        ctx.lineWidth = 1;
        for (var drawer = 1; drawer <= 3; drawer++) {
          var z = 1.25 * drawer / 4;
          var a = project(gx + i * 0.95, gy + 0.6, z), b = project(gx + i * 0.95 + 0.78, gy + 0.6, z);
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        }
      }
    } else if (name === "drafting") {
      box(gx, gy, 2.3, 0.95, 0.72, wood);
      quad([project(gx + 0.2, gy + 0.15, 1.5), project(gx + 2.1, gy + 0.15, 1.5),
            project(gx + 2.1, gy + 0.8, 0.78), project(gx + 0.2, gy + 0.8, 0.78)], hot ? "#f8fafc" : "#cbd5e1");
    } else if (name === "server_room") {
      for (var r = 0; r < 2; r++) {
        box(gx + r * 0.95, gy, 0.8, 1.5, 2.0, "#273142");
        for (var led = 0; led < 8; led++) {
          var lz = 0.2 + led * 0.22;
          var shelf = [project(gx + r * 0.95 + 0.8, gy + 0.2, lz + 0.11),
                       project(gx + r * 0.95 + 0.8, gy + 1.3, lz + 0.11),
                       project(gx + r * 0.95 + 0.8, gy + 1.3, lz),
                       project(gx + r * 0.95 + 0.8, gy + 0.2, lz)];
          quad(shelf, "rgba(255,255,255,0.05)");
          var p = project(gx + r * 0.95 + 0.8, gy + 0.32, lz + 0.05);
          var on = hot ? (Math.sin(t * 6 + led * 1.7 + r) > -0.2) : (led % 3 === 0);
          ctx.fillStyle = on ? (led % 4 === 0 ? theme.accent : theme.ok) : "rgba(120,140,160,0.3)";
          ctx.beginPath(); ctx.arc(p.x, p.y, 1.5, 0, Math.PI * 2); ctx.fill();
        }
      }
    } else if (name === "mailroom") {
      box(gx, gy, 0.7, 2.2, 1.7, wood);
      for (var col = 0; col < 4; col++) {
        for (var row = 0; row < 3; row++) {
          var y0 = gy + 0.18 + col * 0.52, y1 = y0 + 0.36;
          var z0 = 0.3 + row * 0.45, z1 = z0 + 0.32;
          quad([project(gx + 0.7, y0, z1), project(gx + 0.7, y1, z1),
                project(gx + 0.7, y1, z0), project(gx + 0.7, y0, z0)], "rgba(18,12,8,0.55)");
          if (hot && (col + row) % 3 === 0) {
            quad([project(gx + 0.7, y0 + 0.05, z0 + 0.16), project(gx + 0.7, y1 - 0.05, z0 + 0.16),
                  project(gx + 0.7, y1 - 0.05, z0 + 0.04), project(gx + 0.7, y0 + 0.05, z0 + 0.04)], "#fef3c7");
          }
        }
      }
    } else if (name === "war_room") {
      for (var seat = 0; seat < 4; seat++) {
        var sx = gx + (seat % 2 ? 2.15 : -0.35), sy = gy + (seat < 2 ? 0.25 : 1.05);
        box(sx, sy, 0.4, 0.4, 0.44, "#2f3a4c");
      }
      box(gx, gy, 2.1, 1.7, 0.6, "#4a3b2c");
      quad([project(gx + 0.12, gy + 0.12, 0.62), project(gx + 1.98, gy + 0.12, 0.62),
            project(gx + 1.98, gy + 1.58, 0.62), project(gx + 0.12, gy + 1.58, 0.62)], "#6b5540");
      // whiteboard leaning against the wall side of the table
      quad([project(gx + 0.3, gy - 0.05, 1.75), project(gx + 1.8, gy - 0.05, 1.75),
            project(gx + 1.8, gy + 0.05, 0.75), project(gx + 0.3, gy + 0.05, 0.75)], hot ? "#f8fafc" : "#b6c2d2");
      if (hot) {
        ctx.strokeStyle = theme.accent;
        ctx.lineWidth = 1.5;
        for (var scribble = 0; scribble < 3; scribble++) {
          var a = project(gx + 0.5, gy - 0.02, 1.5 - scribble * 0.22);
          var b = project(gx + 1.0 + scribble * 0.2, gy - 0.02, 1.5 - scribble * 0.22);
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        }
      }
    } else if (name === "reception") {
      box(gx, gy, 0.75, 2.1, 1.05, wood);
      var bell = project(gx + 0.4, gy + 1.0, 1.05);
      ctx.fillStyle = hot ? theme.warn : "#94a3b8";
      ctx.beginPath(); ctx.arc(bell.x, bell.y - 3, 4, Math.PI, 0); ctx.fill();
    } else if (name === "break_room") {
      box(gx, gy, 0.62, 0.62, 1.05, "#475569");
      var cup = project(gx + 0.3, gy + 0.3, 1.05);
      ctx.fillStyle = "#e2e8f0";
      ctx.fillRect(cup.x - 3, cup.y - 7, 6, 6);
      if (hot) {
        ctx.strokeStyle = "rgba(226,232,240,0.5)";
        ctx.beginPath();
        ctx.moveTo(cup.x, cup.y - 9);
        ctx.quadraticCurveTo(cup.x + 5 * Math.sin(t * 3), cup.y - 16, cup.x, cup.y - 23);
        ctx.stroke();
      }
      box(gx + 1.15, gy + 0.1, 0.4, 0.4, 0.45, "#3f3f46");
      var leaf = project(gx + 1.35, gy + 0.3, 0.45);
      ctx.fillStyle = "#4ade80";
      ctx.beginPath(); ctx.ellipse(leaf.x, leaf.y - 8, 7, 11, 0, 0, Math.PI * 2); ctx.fill();
    }

  }

  var LABEL_Z = { records: 2.0, drafting: 2.2, server_room: 2.9, mailroom: 3.0, war_room: 0.15, reception: 1.9, break_room: 1.7 };
  var LABEL_SPAN = { mailroom: 0.8, reception: 1.05, war_room: 2.1, records: 1.3, server_room: 0.75, drafting: 0.1 };

  function drawStationLabels() {
    Object.keys(STATIONS).forEach(function (name) {
      if (name === "door") return;
      var anchor = STATIONS[name].anchor;
      var room = snapshot && snapshot.stations && snapshot.stations[name];
      if (!room) return;  // nothing to name until the first snapshot lands
      ctx.save();
      ctx.shadowColor = "rgba(0,0,0,0.55)";
      ctx.shadowBlur = 5;
      label(anchor[0] + 0.6, anchor[1] + (LABEL_SPAN[name] === undefined ? 0.4 : LABEL_SPAN[name]),
        room.label, room.occupants.length ? theme["ink-dim"] : theme["ink-faint"],
        10.5, LABEL_Z[name] || 1.5);
      ctx.restore();
    });
  }



  function drawDesk(index, sprite) {
    var spot = deskSpot(index);
    var gx = spot[0], gy = spot[1];
    ctx.save();
    if (!sprite) ctx.globalAlpha = 0.55;
    box(gx, gy, 1.5, 0.95, 0.6, "#5b4636");
    box(gx + 0.45, gy + 0.12, 0.62, 0.12, 0.52, "#1f2937");
    var screen = [project(gx + 0.45, gy + 0.12, 1.12), project(gx + 1.07, gy + 0.12, 1.12),
                  project(gx + 1.07, gy + 0.12, 0.58), project(gx + 0.45, gy + 0.12, 0.58)];
    var lit = sprite && sprite.status !== "gone";
    quad(screen, lit ? shade(sprite.color, -0.25) : "#263041");
    if (lit) {
      ctx.save();
      ctx.globalAlpha = 0.5;
      quad(screen, "rgba(255,255,255,0.07)");
      ctx.restore();
    }
    box(gx + 0.2, gy + 1.15, 0.45, 0.45, 0.45, "#334155"); // chair
    ctx.restore();
  }

  // ---------------------------------------------------------------- people
  function drawWorker(sprite, t) {
    var p = project(sprite.x, sprite.y, 0);
    var bob = sprite.walking ? Math.abs(Math.sin(t * 9)) * 3 : (sprite.status === "working" ? Math.sin(t * 3.4) * 1.2 : 0);
    ctx.save();
    ctx.globalAlpha = sprite.alpha;

    ctx.fillStyle = "rgba(0,0,0,0.28)";
    ctx.beginPath(); ctx.ellipse(p.x, p.y, 11, 5, 0, 0, Math.PI * 2); ctx.fill();

    var baseY = p.y - bob;
    if (sprite.status === "working") {
      ctx.strokeStyle = shade(sprite.color, 0.1);
      ctx.globalAlpha = sprite.alpha * (0.22 + 0.16 * Math.sin(t * 4));
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.ellipse(p.x, p.y, 17, 8, 0, 0, Math.PI * 2); ctx.stroke();
      ctx.globalAlpha = sprite.alpha;
    }

    // legs
    ctx.fillStyle = "#2b3442";
    ctx.fillRect(p.x - 5, baseY - 14, 4, 12);
    ctx.fillRect(p.x + 1, baseY - 14, 4, 12);
    // body
    ctx.fillStyle = sprite.color;
    roundRect(p.x - 8, baseY - 30, 16, 18, 5);
    ctx.fill();
    // arms
    ctx.fillStyle = shade(sprite.color, -0.2);
    var swing = sprite.walking ? Math.sin(t * 9) * 3 : (sprite.status === "working" ? Math.sin(t * 7) * 2.5 : 0);
    ctx.fillRect(p.x - 11, baseY - 28 + swing, 4, 13);
    ctx.fillRect(p.x + 7, baseY - 28 - swing, 4, 13);
    // head
    ctx.fillStyle = "#e8c9a6";
    ctx.beginPath(); ctx.arc(p.x, baseY - 36, 6.5, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = shade(sprite.color, -0.45);
    ctx.beginPath(); ctx.arc(p.x, baseY - 38.5, 6.5, Math.PI * 1.05, Math.PI * 1.95); ctx.fill();

    if (sprite.lead) {
      ctx.fillStyle = theme.accent;
      ctx.beginPath();
      ctx.moveTo(p.x - 6, baseY - 42);
      ctx.lineTo(p.x, baseY - 47);
      ctx.lineTo(p.x + 6, baseY - 42);
      ctx.closePath();
      ctx.fill();
    }

    carried(sprite, p.x, baseY, t);
    badge(sprite, p.x, baseY - 52, t);

    ctx.fillStyle = theme["ink-dim"];
    ctx.font = "600 10px " + CANVAS_FONT;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillText(sprite.name, p.x, p.y + 6);
    ctx.restore();
  }

  function carried(sprite, x, y, t) {
    if (sprite.status !== "working") return;
    if (sprite.station === "records") {
      ctx.fillStyle = "#e6d3a3";
      ctx.fillRect(x + 6, y - 26, 10, 8);
    } else if (sprite.station === "drafting") {
      ctx.strokeStyle = "#f8fafc";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(x + 8, y - 20);
      ctx.lineTo(x + 8 + Math.sin(t * 8) * 4, y - 26);
      ctx.stroke();
    } else if (sprite.station === "server_room") {
      ctx.fillStyle = "rgba(63,185,80,0.8)";
      ctx.fillRect(x + 7, y - 25, 9, 7);
    } else if (sprite.station === "mailroom") {
      ctx.fillStyle = "#fef3c7";
      ctx.fillRect(x + 6, y - 24, 11, 7);
      ctx.strokeStyle = "#b45309";
      ctx.beginPath(); ctx.moveTo(x + 6, y - 24); ctx.lineTo(x + 11.5, y - 20); ctx.lineTo(x + 17, y - 24); ctx.stroke();
    }
  }

  function badge(sprite, x, y, t) {
    var words = ui().badge;
    var text = "";
    if (sprite.status === "working") text = sprite.tool || words.working;
    else if (words[sprite.status]) text = words[sprite.status];
    if (!text) return;
    ctx.font = "600 10px " + CANVAS_FONT;
    var width = ctx.measureText(text).width + 12;
    var tone = sprite.status === "blocked" ? theme.bad : sprite.status === "waiting" ? theme.warn : shade(sprite.color, -0.1);
    ctx.globalAlpha = sprite.alpha * 0.95;
    ctx.fillStyle = "rgba(10,14,20,0.82)";
    roundRect(x - width / 2, y - 8 + Math.sin(t * 2.5) * 1.2, width, 16, 8);
    ctx.fill();
    ctx.strokeStyle = tone;
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.fillStyle = tone;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, x, y + Math.sin(t * 2.5) * 1.2);
  }

  function roundRect(x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  // ----------------------------------------------------------------- state
  var snapshot = null;
  var sprites = new Map();
  var puffs = [];
  var lastLogId = 0;
  var syncedAt = 0;

  function targetFor(worker, index) {
    // The bullpen is not one room: each clerk has their own desk in it.
    if (worker.station === "desk" && worker.status !== "gone") {
      var spot = deskSpot(worker.desk || 0);
      return [spot[0] + 0.2, spot[1] + 1.05];
    }
    // Anyone on their way out -- or standing somewhere this build does not
    // know how to draw yet -- heads for the door.
    var station = STATIONS[worker.station] || STATIONS.door;
    return [station.stand[0] + index * 0.15, station.stand[1] + index * 0.62];
  }

  function applySnapshot(next) {
    snapshot = next;
    syncedAt = Date.now() / 1000;
    var seen = new Set();
    var perStation = {};
    (next.workers || []).forEach(function (worker) {
      var index = perStation[worker.station] = (perStation[worker.station] === undefined ? 0 : perStation[worker.station] + 1);
      var spot = targetFor(worker, index);
      var sprite = sprites.get(worker.key);
      if (!sprite) {
        var entry = STATIONS.door.stand;
        sprite = {
          key: worker.key, x: entry[0], y: entry[1], alpha: 0,
          walking: false, color: roleColor(worker.role)
        };
        sprites.set(worker.key, sprite);
      }
      sprite.tx = spot[0];
      sprite.ty = spot[1];
      sprite.name = worker.name;
      sprite.role = worker.role;
      sprite.lead = worker.lead;
      sprite.status = worker.status;
      sprite.station = worker.station;
      sprite.tool = worker.tool;
      sprite.color = roleColor(worker.role);
      sprite.desk = worker.desk;
      sprite.fading = worker.status === "gone";
      seen.add(worker.key);
    });
    sprites.forEach(function (sprite, key) {
      if (!seen.has(key)) sprite.fading = true;
    });

    (next.log || []).forEach(function (entry) {
      if (entry.id <= lastLogId) return;
      var sprite = null;
      sprites.forEach(function (candidate) { if (candidate.name === entry.worker) sprite = candidate; });
      if (sprite && ui().puff[entry.kind]) {
        puffs.push({
          x: sprite.x, y: sprite.y, life: 1,
          text: ui().puff[entry.kind],
          color: entry.kind === "error" ? theme.bad : entry.kind === "arrive" ? theme.accent : theme.ok
        });
      }
    });
    if ((next.log || []).length) lastLogId = next.log[next.log.length - 1].id;

    renderPanels(next);
  }

  function step(dt, t) {
    sprites.forEach(function (sprite, key) {
      var dx = (sprite.tx === undefined ? sprite.x : sprite.tx) - sprite.x;
      var dy = (sprite.ty === undefined ? sprite.y : sprite.ty) - sprite.y;
      var dist = Math.hypot(dx, dy);
      var speed = 2.8 * dt;
      sprite.walking = dist > 0.06;
      if (sprite.walking) {
        var move = Math.min(dist, speed);
        sprite.x += dx / dist * move;
        sprite.y += dy / dist * move;
      }
      var wantAlpha = sprite.fading && !sprite.walking ? 0 : 1;
      sprite.alpha += (wantAlpha - sprite.alpha) * Math.min(1, dt * 3.2);
      if (sprite.fading && sprite.alpha < 0.02) sprites.delete(key);
    });
    puffs = puffs.filter(function (puff) {
      puff.life -= dt * 0.9;
      return puff.life > 0;
    });
  }

  function draw(t) {
    var rect = canvas.getBoundingClientRect();
    ctx.clearRect(0, 0, rect.width, rect.height);
    drawFloor();

    var items = [];
    Object.keys(STATIONS).forEach(function (name) {
      if (name === "door") return;
      var occupants = (snapshot && snapshot.stations && snapshot.stations[name])
        ? snapshot.stations[name].occupants.length : 0;
      items.push({ depth: STATIONS[name].anchor[0] + STATIONS[name].anchor[1], draw: function () { drawStation(name, occupants, t); } });
    });
    var used = {};
    var highest = 2;
    sprites.forEach(function (sprite) {
      if (sprite.desk === undefined) return;
      used[sprite.desk] = sprite;
      highest = Math.max(highest, sprite.desk);
    });
    var deskCount = Math.min(12, Math.max(3, highest + 3));
    for (var desk = 0; desk < deskCount; desk++) {
      (function (index) {
        var spot = deskSpot(index);
        items.push({ depth: spot[0] + spot[1], draw: function () { drawDesk(index, used[index]); } });
      })(desk);
    }
    sprites.forEach(function (sprite) {
      items.push({ depth: sprite.x + sprite.y + 0.4, draw: function () { drawWorker(sprite, t); } });
    });

    items.sort(function (a, b) { return a.depth - b.depth; });
    items.forEach(function (item) { item.draw(); });
    drawStationLabels();

    puffs.forEach(function (puff) {
      var p = project(puff.x, puff.y, 0);
      ctx.save();
      ctx.globalAlpha = Math.max(0, puff.life);
      ctx.fillStyle = puff.color;
      ctx.font = "700 11px " + CANVAS_FONT;
      ctx.textAlign = "center";
      ctx.fillText(puff.text, p.x, p.y - 58 - (1 - puff.life) * 26);
      ctx.restore();
    });
  }

  var lastFrame = performance.now();
  function frame(now) {
    var dt = Math.min(0.05, (now - lastFrame) / 1000);
    lastFrame = now;
    if (!document.hidden) {
      step(dt, now / 1000);
      draw(now / 1000);
    }
    requestAnimationFrame(frame);
  }

  // ---------------------------------------------------------------- panels
  var el = {
    sub: document.getElementById("t-sub"),
    rosterHeading: document.getElementById("t-roster"),
    ticketsHeading: document.getElementById("t-tickets"),
    logHeading: document.getElementById("t-log"),
    lang: document.getElementById("lang"),
    recentre: document.getElementById("reset-view"),
    stats: document.getElementById("stats"),
    roster: document.getElementById("roster"),
    rosterCount: document.getElementById("roster-count"),
    tickets: document.getElementById("tickets"),
    log: document.getElementById("log"),
    legend: document.getElementById("legend"),
    conn: document.getElementById("conn"),
    empty: document.getElementById("empty")
  };

  function clock(seconds) {
    return ui().clock(Math.max(0, Math.round(seconds)));
  }

  function timeOf(ts) {
    return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function chip(labelText, value, tone) {
    var li = document.createElement("span");
    li.className = "chip";
    if (tone) li.dataset.tone = tone;
    li.innerHTML = "<b></b><span></span>";
    li.querySelector("b").textContent = value;
    li.querySelector("span").textContent = labelText;
    return li;
  }

  function applyChrome() {
    var words = ui();
    document.documentElement.lang = locale;
    el.sub.textContent = words.sub;
    el.rosterHeading.textContent = words.panels.roster;
    el.ticketsHeading.textContent = words.panels.tickets;
    el.logHeading.textContent = words.panels.log;
    el.recentre.textContent = words.recentre;
    el.empty.textContent = words.empty;
    el.conn.textContent = words.conn[el.conn.dataset.state] || el.conn.dataset.state;
  }

  function syncLanguages(state) {
    if (state.locale && state.locale !== locale && TEXT[state.locale]) {
      locale = state.locale;
      applyChrome();
    }
    var options = state.locales || [];
    if (!options.length || el.lang.dataset.filled === String(options.length)) {
      el.lang.value = state.locale || locale;
      return;
    }
    el.lang.replaceChildren.apply(el.lang, options.map(function (item) {
      var option = document.createElement("option");
      option.value = item.code;
      option.textContent = item.name;
      return option;
    }));
    el.lang.dataset.filled = String(options.length);
    el.lang.value = state.locale || locale;
  }

  el.lang.addEventListener("change", function () {
    // A reload is the honest way to switch: the server words the whole
    // snapshot, so everything -- including history -- comes back translated.
    location.search = "?lang=" + encodeURIComponent(el.lang.value);
  });

  function renderPanels(state) {
    syncLanguages(state);
    var stats = state.stats || {};
    var words = ui();
    el.stats.replaceChildren(
      chip(words.chips.headcount, stats.headcount || 0),
      chip(words.chips.busy, stats.busy || 0, stats.busy ? "busy" : null),
      chip(words.chips.tools, stats.tool_calls || 0),
      chip(words.chips.errors, stats.errors || 0, stats.errors ? "bad" : null),
      chip(words.chips.prompts, stats.prompts || 0),
      chip(words.chips.hires, stats.hires || 0)
    );
    document.title = stats.busy ? words.busyTitle(stats.busy) : "Agent Office";

    var workers = state.workers || [];
    el.empty.hidden = workers.length > 0;
    if (!el.empty.hidden && !el.empty.textContent) el.empty.textContent = words.empty;
    el.rosterCount.textContent = workers.length ? workers.length : "";
    el.roster.replaceChildren.apply(el.roster, workers.map(function (worker) {
      var li = document.createElement("li");
      li.className = "who";
      li.dataset.status = worker.status;
      var pip = document.createElement("span");
      pip.className = "pip";
      pip.style.background = roleColor(worker.role);
      pip.textContent = worker.name.slice(0, 2);
      var mid = document.createElement("div");
      var name = document.createElement("div");
      name.className = "name";
      name.textContent = worker.name;
      var small = document.createElement("small");
      small.textContent = worker.title;
      name.appendChild(small);
      var doing = document.createElement("div");
      doing.className = "doing";
      doing.textContent = worker.activity || worker.status;
      doing.title = worker.activity || "";
      mid.append(name, doing);
      var time = document.createElement("span");
      time.className = "clock";
      time.dataset.since = worker.since || 0;
      time.textContent = clock(worker.busy_for || 0);
      li.append(pip, mid, time);
      return li;
    }));

    el.tickets.replaceChildren.apply(el.tickets, (state.tickets || []).slice(0, 8).map(function (ticket) {
      var li = document.createElement("li");
      li.dataset.status = ticket.status;
      var when = document.createElement("time");
      when.textContent = timeOf(ticket.ts) + " · " +
        (ticket.status === "open" ? words.ticket.open : words.ticket.done);
      var text = document.createElement("span");
      text.textContent = ticket.text || words.ticket.empty;
      li.append(when, text);
      return li;
    }));

    var entries = (state.log || []).slice(-60).reverse();
    el.log.replaceChildren.apply(el.log, entries.map(function (entry) {
      var li = document.createElement("li");
      li.dataset.kind = entry.kind;
      var when = document.createElement("time");
      when.textContent = timeOf(entry.ts);
      var actor = document.createElement("span");
      actor.className = "actor";
      actor.textContent = entry.worker;
      var what = document.createElement("span");
      what.className = "what";
      what.textContent = entry.text;
      li.append(when, actor, what);
      return li;
    }));

    var stations = state.stations || {};
    el.legend.replaceChildren.apply(el.legend, Object.keys(stations).map(function (name) {
      var li = document.createElement("li");
      li.dataset.hot = stations[name].occupants.length ? "1" : "0";
      li.innerHTML = "<b></b> <span></span>";
      li.querySelector("b").textContent = stations[name].label;
      li.querySelector("span").textContent = stations[name].blurb;
      li.title = stations[name].uses + " visits";
      return li;
    }));
  }

  setInterval(function () {
    if (!snapshot) return;
    var drift = Date.now() / 1000 - syncedAt;
    el.roster.querySelectorAll(".clock").forEach(function (node, index) {
      var worker = (snapshot.workers || [])[index];
      if (worker) node.textContent = clock((worker.busy_for || 0) + drift);
    });
  }, 1000);

  // ------------------------------------------------------------ connection
  function setConn(state) {
    el.conn.dataset.state = state;
    el.conn.textContent = ui().conn[state] || state;
  }

  var pollTimer = null;
  function poll() {
    fetch(api("/api/state"), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(applySnapshot)
      .catch(function () { setConn("offline"); });
  }

  function connect() {
    setConn("connecting");
    var source = new EventSource(api("/api/stream"));
    source.addEventListener("state", function (event) {
      setConn("live");
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      try { applySnapshot(JSON.parse(event.data)); } catch (err) { /* keep the last good frame */ }
    });
    source.onerror = function () {
      setConn("offline");
      source.close();
      if (!pollTimer) pollTimer = setInterval(poll, 3000);
      setTimeout(connect, 2500);
    };
  }

  // ---------------------------------------------------------- view gestures
  var drag = null;
  canvas.addEventListener("pointerdown", function (event) {
    drag = { x: event.clientX, y: event.clientY, ox: camera.userX, oy: camera.userY };
    canvas.classList.add("dragging");
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", function (event) {
    if (!drag) return;
    camera.userX = drag.ox + (event.clientX - drag.x);
    camera.userY = drag.oy + (event.clientY - drag.y);
  });
  ["pointerup", "pointercancel"].forEach(function (name) {
    canvas.addEventListener(name, function () { drag = null; canvas.classList.remove("dragging"); });
  });
  canvas.addEventListener("wheel", function (event) {
    event.preventDefault();
    camera.userScale = Math.max(0.5, Math.min(2.4, camera.userScale * (event.deltaY < 0 ? 1.1 : 0.9)));
  }, { passive: false });
  document.getElementById("reset-view").addEventListener("click", function () {
    camera.userScale = 1; camera.userX = 0; camera.userY = 0;
  });

  // ------------------------------------------------------------------ boot
  readTheme();
  applyChrome();
  resize();
  window.addEventListener("resize", resize);
  if (window.matchMedia) {
    var scheme = window.matchMedia("(prefers-color-scheme: dark)");
    if (scheme.addEventListener) scheme.addEventListener("change", readTheme);
    else if (scheme.addListener) scheme.addListener(readTheme);
  }
  poll();
  connect();
  requestAnimationFrame(frame);
})();
