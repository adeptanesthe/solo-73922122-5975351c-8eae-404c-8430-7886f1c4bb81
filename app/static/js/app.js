/* 笔顺订正工作台 frontend (vanilla JS, canvas 2D). */
"use strict";

const GRID = 1000;
const MAX_STROKES = 10;
const MAX_POINTS = 64;
const PLAY_MS = 1700;

const PALETTE = [
  "#d1495b", "#2e86ab", "#1f8a4c", "#e08e0b", "#7e22ce",
  "#0f9d8f", "#c2410c", "#3b5bdb", "#a61e4d", "#5c940d",
];

const state = {
  chars: [],
  charId: null,
  model: [],
  strokes: [], // {uid, points:[[x,y]], preserved, color}
  uidCursor: 1,
  colorCursor: 0,
  threshold: 180,
  plan: null,
  planStale: false,
  everPlanned: false,
  busy: false,
  planReqId: 0,
  pb: 0,
  playing: false,
  lastTick: 0,
  draft: null, // in-progress raw points
  drawing: false,
  highlightUid: null,
};

const $ = (id) => document.getElementById(id);
const canvas = $("board");
const ctx = canvas.getContext("2d");

/* ----------------------------- utilities ----------------------------- */

function toast(msg) {
  let el = document.querySelector(".toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), 2200);
}

function dist(a, b) {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

function arcResample(points, count) {
  if (points.length === 1) return Array.from({ length: count }, () => [...points[0]]);
  const total = points.reduce((s, p, i) => (i ? s + dist(points[i - 1], p) : 0), 0);
  if (total <= 1e-9) return Array.from({ length: count }, () => [...points[0]]);
  const interval = total / (count - 1);
  const out = [[...points[0]]];
  let accum = 0, idx = 0, cur = points[0];
  while (out.length < count - 1 && idx < points.length - 1) {
    const nxt = points[idx + 1];
    const seg = dist(cur, nxt);
    if (seg <= 1e-12) { idx += 1; cur = nxt; continue; }
    if (accum + seg >= interval) {
      const t = (interval - accum) / seg;
      const np = [cur[0] + (nxt[0] - cur[0]) * t, cur[1] + (nxt[1] - cur[1]) * t];
      out.push(np); cur = np; accum = 0;
    } else { accum += seg; cur = nxt; idx += 1; }
  }
  while (out.length < count) out.push([...points[points.length - 1]]);
  out[count - 1] = [...points[points.length - 1]];
  return out.slice(0, count);
}

function finalizePoints(raw) {
  // Thin close samples, then ALWAYS arc-length normalise to a dense fixed
  // count (<=64).  Dense, evenly spaced points make the server's exact
  // discrete Frechet robust even for a sparse down/move/up gesture that only
  // produced two raw endpoints.
  const thin = [];
  for (const p of raw) {
    if (!thin.length || dist(thin[thin.length - 1], p) >= 5) thin.push(p);
  }
  const lastRaw = raw[raw.length - 1];
  if (thin.length && dist(thin[thin.length - 1], lastRaw) > 0.5) thin.push(lastRaw);
  if (thin.length === 0) return [];
  if (thin.length === 1) return [thin[0], [thin[0][0] + 0.01, thin[0][1]]];
  return arcResample(thin, 48);
}

// Must match backend apply_move: pop frm then insert at resulting index to.
function applyMove(arr, frm, to) {
  const [item] = arr.splice(frm, 1);
  arr.splice(to, 0, item);
  return arr;
}

function centroid(points) {
  let x = 0, y = 0;
  for (const p of points) { x += p[0]; y += p[1]; }
  return [x / points.length, y / points.length];
}

// Nudge order badges apart when several strokes start at the same corner
// (e.g. the vertical and the heng-zhe of 口 share their first point).
function resolveBadgePositions(strokes) {
  const MIN_D = 66;
  const pos = strokes.map((s) => {
    let [x, y] = s.points[0];
    return [Math.max(46, Math.min(GRID - 46, x)), Math.max(46, Math.min(GRID - 46, y))];
  });
  for (let iter = 0; iter < 60; iter++) {
    for (let i = 0; i < pos.length; i++) {
      for (let j = i + 1; j < pos.length; j++) {
        let dx = pos[j][0] - pos[i][0];
        let dy = pos[j][1] - pos[i][1];
        let d = Math.hypot(dx, dy);
        if (d >= MIN_D) continue;
        if (d < 0.01) {
          const ang = i * 2.399 + j * 1.31;
          dx = Math.cos(ang); dy = Math.sin(ang); d = 1;
        }
        const push = (MIN_D - d) / 2;
        const nx = dx / d, ny = dy / d;
        pos[i][0] -= nx * push; pos[i][1] -= ny * push;
        pos[j][0] += nx * push; pos[j][1] += ny * push;
      }
    }
    for (const p of pos) {
      p[0] = Math.max(46, Math.min(GRID - 46, p[0]));
      p[1] = Math.max(46, Math.min(GRID - 46, p[1]));
    }
  }
  return pos;
}

/* ----------------------------- API ----------------------------- */

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

async function requestPlan() {
  const reqId = ++state.planReqId;
  state.busy = true;
  updatePlanButtons();
  let data;
  try {
    const res = await fetch("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        character_id: state.charId,
        threshold: Number(state.threshold),
        strokes: state.strokes.map((s) => ({
          uid: s.uid, points: s.points, preserved: !!s.preserved,
        })),
      }),
    });
    data = await res.json();
  } catch (e) {
    if (reqId !== state.planReqId) return;
    toast("求解请求失败：" + e.message);
    state.busy = false;
    updatePlanButtons();
    return;
  }
  // A newer request superseded this one (rapid edits / toggles).
  if (reqId !== state.planReqId) return;
  state.busy = false;
  state.everPlanned = true;
  state.plan = data;
  state.planStale = false;
  state.pb = 0;
  state.playing = false;
  renderPlanPanel();
  renderStepList();
  updatePlanButtons();
}

/* ------------------------- character picker ------------------------- */

async function initCharacters() {
  const idx = await fetchJSON("/api/characters");
  state.chars = idx.characters;
  const picker = $("charPicker");
  picker.innerHTML = "";
  for (const c of state.chars) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "char-btn";
    b.dataset.id = c.id;
    b.title = `${c.char} · ${c.stroke_count} 笔 · ${c.meaning || ""}`;
    b.textContent = c.char;
    b.addEventListener("click", () => selectCharacter(c.id, true));
    picker.appendChild(b);
  }
  await selectCharacter(state.chars.find((c) => c.id === "mu") ? "mu" : state.chars[0].id, false);
}

async function selectCharacter(id, confirmChange) {
  if (confirmChange && state.strokes.length &&
      !window.confirm("切换范字会清空当前笔迹与建议，确定吗？")) {
    return;
  }
  const data = await fetchJSON(`/api/characters/${id}`);
  state.charId = id;
  state.model = data.strokes;
  state.strokes = [];
  state.plan = null;
  state.planStale = false;
  state.everPlanned = false;
  state.pb = 0;
  state.playing = false;
  $("adoptLog").innerHTML = "";
  document.querySelectorAll(".char-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.id === id));
  renderStrokeList();
  renderPlanPanel();
  renderStepList();
  updatePlanButtons();
}

/* --------------------------- canvas sizing --------------------------- */

function resizeCanvas() {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const px = Math.max(1, Math.round(rect.width * dpr));
  if (canvas.width !== px || canvas.height !== px) {
    canvas.width = px;
    canvas.height = px;
  }
}
window.addEventListener("resize", resizeCanvas);

function toGrid(e) {
  const r = canvas.getBoundingClientRect();
  return [
    ((e.clientX - r.left) / r.width) * GRID,
    ((e.clientY - r.top) / r.height) * GRID,
  ];
}

/* ----------------------------- drawing ----------------------------- */

function strokePath(c, pts) {
  c.beginPath();
  c.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) c.lineTo(pts[i][0], pts[i][1]);
}

function drawGrid(c) {
  c.save();
  c.fillStyle = "#fffdf8";
  c.fillRect(0, 0, GRID, GRID);

  // faint regular grid thirds
  c.strokeStyle = "#efe7d6";
  c.lineWidth = 1.4;
  for (let i = 1; i < 3; i++) {
    const p = (GRID / 3) * i;
    c.beginPath(); c.moveTo(p, 0); c.lineTo(p, GRID); c.stroke();
    c.beginPath(); c.moveTo(0, p); c.lineTo(GRID, p); c.stroke();
  }
  // rice grid diagonals + center cross
  c.strokeStyle = "rgba(199,92,84,0.35)";
  c.lineWidth = 2;
  c.setLineDash([16, 14]);
  c.strokeRect(8, 8, GRID - 16, GRID - 16);
  c.beginPath();
  c.moveTo(8, 8); c.lineTo(GRID - 8, GRID - 8);
  c.moveTo(GRID - 8, 8); c.lineTo(8, GRID - 8);
  c.moveTo(GRID / 2, 8); c.lineTo(GRID / 2, GRID - 8);
  c.moveTo(8, GRID / 2); c.lineTo(GRID - 8, GRID / 2);
  c.stroke();
  c.setLineDash([]);
  c.restore();
}

function drawModelGuide(c) {
  if (!state.model.length) return;
  c.save();
  c.lineCap = "round";
  c.lineJoin = "round";
  for (const pts of state.model) {
    strokePath(c, pts);
    c.strokeStyle = "rgba(156,43,37,0.16)";
    c.lineWidth = 40;
    c.stroke();
    strokePath(c, pts);
    c.strokeStyle = "rgba(156,43,37,0.42)";
    c.lineWidth = 3;
    c.setLineDash([2, 10]);
    c.stroke();
    c.setLineDash([]);
  }
  c.restore();
}

function drawInk(c, pts, color, width = 18, alpha = 1) {
  c.save();
  c.globalAlpha = alpha;
  c.lineCap = "round";
  c.lineJoin = "round";
  strokePath(c, pts);
  c.strokeStyle = color;
  c.lineWidth = width;
  c.stroke();
  // subtle darker core
  strokePath(c, pts);
  c.strokeStyle = "rgba(0,0,0,0.12)";
  c.lineWidth = Math.max(2, width * 0.28);
  c.stroke();
  c.restore();
}

function drawOrderBadge(c, bx, by, index1, color, preserved) {
  c.save();
  c.beginPath(); c.arc(bx, by, 27, 0, Math.PI * 2);
  c.fillStyle = color; c.fill();
  c.lineWidth = preserved ? 7 : 4;
  c.strokeStyle = preserved ? "#1f8a4c" : "rgba(255,255,255,0.9)";
  c.stroke();
  c.fillStyle = "#fff";
  c.font = "bold 30px -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif";
  c.textAlign = "center"; c.textBaseline = "middle";
  c.fillText(String(index1), bx, by + 2);
  if (preserved) {
    c.font = "bold 20px sans-serif";
    const label = "保";
    const tw = c.measureText(label).width + 18;
    c.fillStyle = "#1f8a4c";
    roundRect(c, bx + 18, by - 44, tw, 30, 8); c.fill();
    c.fillStyle = "#fff";
    c.fillText(label, bx + 18 + tw / 2, by - 28);
  }
  c.restore();
}

function roundRect(c, x, y, w, h, r) {
  c.beginPath();
  c.moveTo(x + r, y);
  c.arcTo(x + w, y, x + w, y + h, r);
  c.arcTo(x + w, y + h, x, y + h, r);
  c.arcTo(x, y + h, x, y, r);
  c.arcTo(x, y, x + w, y, r);
  c.closePath();
}

function drawPill(c, x, y, text, bg) {
  c.save();
  c.font = "bold 26px -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif";
  const w = c.measureText(text).width + 30;
  const h = 44;
  let px = x - w / 2, py = y - h / 2;
  px = Math.max(10, Math.min(GRID - w - 10, px));
  py = Math.max(10, Math.min(GRID - h - 10, py));
  c.globalAlpha = 0.95;
  c.fillStyle = bg;
  roundRect(c, px, py, w, h, 12); c.fill();
  c.fillStyle = "#fff";
  c.textAlign = "center"; c.textBaseline = "middle";
  c.fillText(text, px + w / 2, py + h / 2 + 2);
  c.restore();
}

function playbackView() {
  const plan = state.plan;
  if (!plan || !plan.feasible || state.planStale) return null;
  const arr = state.strokes.map((s) => ({ ...s }));
  const addSteps = [];
  let last = null;
  for (let k = 0; k < state.pb && k < plan.steps.length; k++) {
    const step = plan.steps[k];
    last = step;
    if (step.kind === "delete") {
      let idx = arr.findIndex((s) => s.uid === step.uid);
      if (idx < 0) idx = Math.min(step.index, arr.length - 1);
      if (idx >= 0) arr.splice(idx, 1);
    } else if (step.kind === "move") {
      let from = step.from;
      if (arr[from] && step.uid != null && arr[from].uid !== step.uid) {
        from = arr.findIndex((s) => s.uid === step.uid);
      }
      if (from >= 0) applyMove(arr, from, step.to);
    } else if (step.kind === "add") {
      addSteps.push(step);
    }
  }
  return { arr, addSteps, last };
}

function drawDashedAdd(c, pts, alpha, animate, width = 12) {
  c.save();
  c.globalAlpha = alpha;
  c.lineCap = "round"; c.lineJoin = "round";
  strokePath(c, pts);
  c.strokeStyle = "#7e22ce";
  c.lineWidth = width;
  c.setLineDash([26, 18]);
  if (animate) c.lineDashOffset = -performance.now() / 35;
  c.stroke();
  c.restore();
}

function render() {
  resizeCanvas();
  const sx = canvas.width / GRID;
  ctx.setTransform(sx, 0, 0, sx, 0, 0);
  ctx.clearRect(0, 0, GRID, GRID);

  drawGrid(ctx);
  drawModelGuide(ctx);

  const view = playbackView();
  const showPlan = view !== null;

  // Persistent faint dashed previews of every missing stroke once planned.
  if (showPlan) {
    const appliedAddSlots = new Set(view.addSteps.map((s) => s.slot));
    for (const step of state.plan.steps) {
      if (step.kind !== "add" || appliedAddSlots.has(step.slot)) continue;
      drawDashedAdd(ctx, step.points, 0.35, false, 9);
    }
  }

  // Normal / playback strokes.
  const strokesToDraw = view ? view.arr : state.strokes;
  const now = performance.now();
  const moveUid = view && view.last && view.last.kind === "move" ? view.last.uid : null;
  const badgePos = resolveBadgePositions(strokesToDraw);

  strokesToDraw.forEach((s, i) => {
    if (moveUid != null && s.uid === moveUid) {
      const pulse = 0.25 + 0.18 * Math.sin(now / 220);
      drawInk(ctx, s.points, "#1d5fd0", 30 + 6 * Math.sin(now / 220), pulse);
      drawInk(ctx, s.points, "#1d5fd0", 18, 1);
    } else if (state.highlightUid != null && s.uid === state.highlightUid) {
      drawInk(ctx, s.points, "#2e86ab", 26, 0.35);
      drawInk(ctx, s.points, s.color, 18, 1);
    } else {
      drawInk(ctx, s.points, s.color || "#22201c", 18, 0.96);
    }
    drawOrderBadge(ctx, badgePos[i][0], badgePos[i][1], i + 1, s.color || "#22201c", s.preserved);
  });

  // In-progress draft.
  if (state.draft && state.draft.length > 1) {
    drawInk(ctx, state.draft, "#9aa0a6", 18, 0.8);
  }

  if (showPlan) {
    // Applied add strokes (animated dashed).
    for (const step of view.addSteps) {
      const isLast = view.last === step;
      drawDashedAdd(ctx, step.points, isLast ? 1 : 0.7, isLast, isLast ? 14 : 11);
      if (isLast) {
        const [cx, cy] = centroid(step.points);
        drawPill(ctx, cx, Math.max(60, cy - 70), `补第 ${step.slot + 1} 笔`, "#7e22ce");
      }
    }

    // Delete ghost.
    if (view.last && view.last.kind === "delete" && view.last.points) {
      const pulse = 0.3 + 0.2 * Math.sin(now / 200);
      drawInk(ctx, view.last.points, "#d3382c", 26, pulse);
      drawInk(ctx, view.last.points, "#d3382c", 16, 0.85);
      const [cx, cy] = centroid(view.last.points);
      drawPill(ctx, cx, Math.max(60, cy - 60), `删除第 ${view.last.index + 1} 笔`, "#d3382c");
    }
    // Move tag.
    if (view.last && view.last.kind === "move") {
      const target = view.arr.find((s) => s.uid === view.last.uid);
      if (target) {
        const [cx, cy] = centroid(target.points);
        drawPill(ctx, cx, Math.max(60, cy - 70),
          `移笔 ${view.last.from + 1} → ${view.last.to + 1}`, "#1d5fd0");
      }
    }

    // Auto-advance playback.
    if (state.playing && state.plan.feasible) {
      if (!state.lastTick) state.lastTick = now;
      if (now - state.lastTick > PLAY_MS) {
        if (state.pb < state.plan.steps.length) {
          state.pb += 1;
          state.lastTick = now;
        }
        if (state.pb >= state.plan.steps.length) state.playing = false;
        renderStepList();
        updatePlanButtons();
      }
    }
  }

  requestAnimationFrame(render);
}

/* ------------------------- pointer drawing ------------------------- */

canvas.addEventListener("pointerdown", (e) => {
  if (state.strokes.length >= MAX_STROKES) {
    toast(`最多 ${MAX_STROKES} 笔，请撤销或删除后再写`);
    return;
  }
  e.preventDefault();
  canvas.setPointerCapture(e.pointerId);
  state.drawing = true;
  const p = toGrid(e);
  state.draft = [p];
  $("canvasHint").style.opacity = "0";
});

canvas.addEventListener("pointermove", (e) => {
  if (!state.drawing || !state.draft) return;
  e.preventDefault();
  const p = toGrid(e);
  if (dist(state.draft[state.draft.length - 1], p) >= 4) state.draft.push(p);
});

function endStroke(e) {
  if (!state.drawing) return;
  state.drawing = false;
  try { canvas.releasePointerCapture(e.pointerId); } catch (_) { /* noop */ }
  if (e && e.clientX != null) {
    const p = toGrid(e);
    if (state.draft) state.draft.push(p);
  }
  const pts = finalizePoints(state.draft || []);
  state.draft = null;
  if (pts.length >= 2 && pathLen(pts) > 12) {
    state.strokes.push({
      uid: `u${state.uidCursor++}`,
      points: pts,
      preserved: false,
      color: PALETTE[state.colorCursor++ % PALETTE.length],
    });
    afterStrokesChanged({ keepPlan: false });
  }
}
function pathLen(pts) {
  let L = 0;
  for (let i = 1; i < pts.length; i++) L += dist(pts[i - 1], pts[i]);
  return L;
}
canvas.addEventListener("pointerup", endStroke);
canvas.addEventListener("pointercancel", endStroke);

/* ------------------------- stroke list UI ------------------------- */

function afterStrokesChanged() {
  // Once the teacher has asked for a revision, keep the suggestion live:
  // synchronously invalidate the old one, then recompute from actual order.
  const shouldReplan = state.everPlanned;
  invalidatePlan();
  renderStrokeList();
  if (shouldReplan) requestPlan();
}

function invalidatePlan() {
  state.plan = null;
  state.pb = 0;
  state.playing = false;
  state.planStale = state.everPlanned;
  renderPlanPanel();
  renderStepList();
  updatePlanButtons();
}

function renderStrokeList() {
  const ul = $("strokeList");
  ul.innerHTML = "";
  $("strokeCount").textContent = String(state.strokes.length);
  $("emptyStrokes").style.display = state.strokes.length ? "none" : "block";

  state.strokes.forEach((s, i) => {
    const li = document.createElement("li");
    li.className = "stroke-item" + (s.preserved ? " preserved" : "") +
      (state.highlightUid === s.uid ? " highlight" : "");

    const badge = document.createElement("span");
    badge.className = "order";
    badge.style.background = s.color;
    badge.textContent = String(i + 1);

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.innerHTML = `<div class="name">第 ${i + 1} 笔${s.preserved ? " · 已保留" : ""}</div>
      <div class="sub">${s.points.length} 个坐标点</div>`;

    const label = document.createElement("label");
    label.className = "preserve-toggle";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = !!s.preserved;
    cb.addEventListener("change", () => {
      s.preserved = cb.checked;
      afterStrokesChanged();
    });
    label.appendChild(cb);
    label.appendChild(document.createTextNode("保留"));

    const del = document.createElement("button");
    del.type = "button";
    del.className = "link-del";
    del.textContent = "删除";
    if (s.preserved) {
      del.disabled = true;
      del.title = "保留笔不能删除；请先取消“保留”";
    }
    del.addEventListener("click", () => {
      if (s.preserved) return;
      state.strokes.splice(i, 1);
      afterStrokesChanged();
    });

    li.appendChild(badge);
    li.appendChild(meta);
    li.appendChild(label);
    li.appendChild(del);

    li.addEventListener("mouseenter", () => { state.highlightUid = s.uid; });
    li.addEventListener("mouseleave", () => { state.highlightUid = null; });
    ul.appendChild(li);
  });
}

/* --------------------------- plan panel --------------------------- */

function renderPlanPanel() {
  const stale = $("staleNotice");
  const noSol = $("noSolution");
  const summary = $("planSummary");
  stale.hidden = !(state.planStale && state.everPlanned && !state.plan);

  if (state.plan && state.plan.feasible) {
    noSol.hidden = true;
    summary.hidden = false;
    $("costTotal").textContent = state.plan.cost;
    $("cntMove").textContent = state.plan.counts.move;
    $("cntDel").textContent = state.plan.counts.delete;
    $("cntAdd").textContent = state.plan.counts.add;
  } else if (state.plan && !state.plan.feasible) {
    summary.hidden = true;
    noSol.hidden = false;
    noSol.textContent = state.plan.reason || "无解";
  } else {
    summary.hidden = true;
    noSol.hidden = true;
  }
  updatePlanButtons();
}

function updatePlanButtons() {
  const hasFeasible = !!(state.plan && state.plan.feasible && !state.planStale);
  const steps = hasFeasible ? state.plan.steps : [];
  const realNext = hasFeasible ? state.plan.next_action : null;

  $("btnPlan").disabled = state.busy || !state.model.length;
  $("btnPlan").textContent = state.busy ? "求解中…" : "求最优修订";

  $("btnAdopt").disabled = !realNext || state.busy;
  if (realNext) {
    const verb = realNext.kind === "delete" ? "删除" : "移动";
    const where = realNext.kind === "delete"
      ? `第 ${realNext.index + 1} 笔`
      : `第 ${realNext.from + 1} 笔 → 第 ${realNext.to + 1} 位`;
    $("btnAdopt").textContent = `采纳下一步：${verb}${where}`;
    $("adoptHint").textContent = "采纳后按当前实际笔序重新求解";
  } else {
    $("btnAdopt").textContent = "采纳下一步移笔 / 删笔";
    $("adoptHint").textContent = hasFeasible
      ? (state.plan.counts.add ? "已无需移 / 删，请播放查看虚线补笔" : "已经符合范字笔序")
      : "";
  }

  const hasSteps = steps.length > 0;
  $("btnPlay").disabled = !hasSteps;
  $("btnNext").disabled = !hasSteps || state.pb >= steps.length;
  $("btnPrev").disabled = !hasSteps || state.pb <= 0;
  $("btnResetPb").disabled = !hasSteps || state.pb === 0;
  $("btnPlay").textContent = state.playing ? "暂停" : (state.pb >= steps.length ? "重播" : "播放");
}

function stepTag(kind) {
  if (kind === "move") return '<span class="tag tag-move">移</span>';
  if (kind === "delete") return '<span class="tag tag-delete">删</span>';
  return '<span class="tag tag-add">补</span>';
}

function renderStepList() {
  const ol = $("stepList");
  if (!state.plan || !state.plan.feasible || state.planStale) {
    ol.innerHTML = "";
    return;
  }
  const steps = state.plan.steps;
  ol.innerHTML = "";
  steps.forEach((s, i) => {
    const li = document.createElement("li");
    li.className = "step" + (i < state.pb - 1 ? " done" : "") +
      (i === state.pb - 1 ? " current" : "");
    li.innerHTML = `<span class="n">${i + 1}</span><span>${stepTag(s.kind)}${s.label}</span>`;
    li.addEventListener("click", () => {
      state.playing = false;
      state.pb = i + 1;
      state.lastTick = 0;
      renderStepList();
      updatePlanButtons();
    });
    ol.appendChild(li);
  });
  updatePlanButtons();
}

/* --------------------------- adopt action --------------------------- */

async function adoptNext() {
  const na = state.plan && state.plan.next_action;
  if (!na || state.planStale) return;

  // Guard against any index/uid mismatch.
  if (na.uid != null && state.strokes[na.index] &&
      state.strokes[na.index].uid !== na.uid) {
    const idx = state.strokes.findIndex((s) => s.uid === na.uid);
    if (idx < 0) { toast("该笔已不存在，建议失效，请重新求解"); invalidatePlan(); return; }
    if (na.kind === "delete") na.index = idx;
    else na.from = idx;
  }

  addAdoptLog(na.label);

  if (na.kind === "delete") {
    state.strokes.splice(na.index, 1);
  } else {
    applyMove(state.strokes, na.from, na.to);
  }
  renderStrokeList();
  // Drop the old suggestion, then recompute from the actual new stroke order.
  invalidatePlan();
  await requestPlan();
  toast("已采纳，并按实际笔序重新计算");
}

function addAdoptLog(label) {
  const ul = $("adoptLog");
  const li = document.createElement("li");
  const now = new Date();
  const hh = String(now.getHours()).padStart(2, "0");
  const mm = String(now.getMinutes()).padStart(2, "0");
  const ss = String(now.getSeconds()).padStart(2, "0");
  li.innerHTML = `${label}<small>${hh}:${mm}:${ss}</small>`;
  ul.prepend(li);
}

/* --------------------------- sample data --------------------------- */

async function loadSample() {
  if (!state.charId) return;
  try {
    const data = await fetchJSON(`/api/sample/${state.charId}`);
    state.colorCursor = 0;
    state.uidCursor = 1;
    state.strokes = data.strokes.map((s) => ({
      uid: s.uid,
      points: s.points,
      preserved: false,
      color: PALETTE[state.colorCursor++ % PALETTE.length],
    }));
    state.everPlanned = false;
    state.planStale = false;
    renderStrokeList();
    $("canvasHint").style.opacity = "0";
    await requestPlan();
    toast("已填入含移笔 / 删笔 / 缺笔错误的示例");
  } catch (e) {
    toast("示例载入失败：" + e.message);
  }
}

/* --------------------------- wire controls --------------------------- */

$("threshold").addEventListener("input", (e) => {
  state.threshold = Number(e.target.value);
  $("thresholdValue").textContent = e.target.value;
});
$("threshold").addEventListener("change", () => {
  if (!state.everPlanned) return;
  invalidatePlan();
  requestPlan();
});

$("btnPlan").addEventListener("click", requestPlan);
$("btnAdopt").addEventListener("click", adoptNext);
$("btnUndo").addEventListener("click", () => {
  state.strokes.pop();
  afterStrokesChanged();
});
$("btnClear").addEventListener("click", () => {
  if (!state.strokes.length) return;
  if (!window.confirm("确定清空全部实写笔迹吗？")) return;
  state.strokes = [];
  afterStrokesChanged();
  $("canvasHint").style.opacity = "1";
});
$("btnSample").addEventListener("click", loadSample);

$("btnPlay").addEventListener("click", () => {
  if (!state.plan || !state.plan.steps.length) return;
  if (state.pb >= state.plan.steps.length) state.pb = 0; // replay
  state.playing = !state.playing;
  state.lastTick = 0;
  updatePlanButtons();
});
$("btnNext").addEventListener("click", () => {
  state.playing = false;
  if (state.pb < state.plan.steps.length) state.pb += 1;
  state.lastTick = 0;
  renderStepList();
});
$("btnPrev").addEventListener("click", () => {
  state.playing = false;
  if (state.pb > 0) state.pb -= 1;
  state.lastTick = 0;
  renderStepList();
});
$("btnResetPb").addEventListener("click", () => {
  state.playing = false; state.pb = 0; state.lastTick = 0;
  renderStepList();
});

/* ------------------------------ boot ------------------------------ */
resizeCanvas();
requestAnimationFrame(render);
initCharacters().catch((e) => toast("范字加载失败：" + e.message));
