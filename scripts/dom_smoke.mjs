import { JSDOM, VirtualConsole } from "jsdom";

const BASE = process.env.BASE || "http://127.0.0.1:8000/";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function makeCtxProxy() {
  return new Proxy(
    {},
    {
      get(_t, prop) {
        if (prop === "measureText") return () => ({ width: 120 });
        if (prop === "createLinearGradient" || prop === "createRadialGradient")
          return () => ({ addColorStop() {} });
        if (prop === "getLineDash") return () => [];
        if (typeof prop === "string") return () => {};
        return undefined;
      },
      set() {
        return true;
      },
    }
  );
}

const errors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => errors.push("jsdomError: " + (e.detail?.stack || e.message || e)));
vc.on("error", (...a) => errors.push("console.error: " + a.join(" ")));

const dom = await JSDOM.fromURL(BASE, {
  runScripts: "dangerously",
  resources: "usable",
  pretendToBeVisual: true,
  virtualConsole: vc,
  beforeParse(window) {
    window.fetch = (u, opt) => fetch(new URL(u, BASE), opt);
    window.performance = globalThis.performance;
    window.confirm = () => true;
    window.HTMLCanvasElement.prototype.getContext = () => makeCtxProxy();
    window.HTMLCanvasElement.prototype.getBoundingClientRect = () => ({
      x: 0, y: 0, top: 0, left: 0, right: 680, bottom: 680, width: 680, height: 680,
      toJSON() {},
    });
    window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
    window.addEventListener("error", (e) => errors.push("window.error: " + e.message));
  },
});

const { window } = dom;
const document = window.document;
const $ = (id) => document.getElementById(id);

async function waitFor(fn, timeout = 8000, label = "condition") {
  const t0 = Date.now();
  let lastErr;
  while (Date.now() - t0 < timeout) {
    try {
      if (fn()) return;
    } catch (e) { lastErr = e; }
    await sleep(60);
  }
  throw new Error("timeout waiting for " + label + (lastErr ? ` (${lastErr.message})` : ""));
}

const results = [];
try {
  await waitFor(() => document.querySelectorAll(".char-btn").length === 8, 8000, "char buttons");
  results.push("char buttons rendered: 8");

  await waitFor(() => document.querySelector(".char-btn.active")?.dataset.id === "mu", 4000, "default mu");
  results.push("default char = 木");

  $("btnSample").click();
  await waitFor(() => $("costTotal").textContent !== "", 8000, "plan summary");
  await sleep(100);
  const initialCost = parseInt($("costTotal").textContent, 10);
  results.push(`sample plan cost=${initialCost} counts=` +
    `移${$("cntMove").textContent} 删${$("cntDel").textContent} 补${$("cntAdd").textContent}`);
  if (!(initialCost >= 2)) throw new Error("expected sample cost >=2, got " + initialCost);

  const stepCount = document.querySelectorAll("#stepList .step").length;
  if (stepCount !== initialCost) throw new Error(`steps ${stepCount} != cost ${initialCost}`);
  results.push(`step list rows=${stepCount} (matches cost)`);

  // Mark the first two (mis-ordered) strokes preserved: their reference slots
  // are out of order, so preservation cannot be satisfied -> inline 无解.
  const boxes = () => document.querySelectorAll("#strokeList input[type=checkbox]");
  function toggleBox(i, checked) {
    const cb = boxes()[i];
    cb.checked = checked;
    cb.dispatchEvent(new window.Event("change", { bubbles: true }));
  }
  toggleBox(0, true);
  await sleep(30);
  toggleBox(1, true);
  await waitFor(() => !$("noSolution").hidden && $("noSolution").textContent.includes("无解"),
    8000, "no-solution notice");
  results.push("conflicting preserve marks -> 无解 shown: " + $("noSolution").textContent.slice(0, 24) + "…");

  // Synchronous invalidation happened on toggle; adopt must be blocked while
  // the no-solution state is shown.
  if (!$("btnAdopt").disabled) throw new Error("adopt must be disabled on no-solution");

  // Clear both marks (re-query after each re-render) -> feasible plan returns.
  toggleBox(0, false);
  await sleep(30);
  // the remaining checked box may now sit at index 0 or 1; clear any checked.
  for (let k = 0; k < 3; k++) {
    const checked = Array.from(boxes()).find((b) => b.checked);
    if (!checked) break;
    checked.checked = false;
    checked.dispatchEvent(new window.Event("change", { bubbles: true }));
    await sleep(40);
  }
  await waitFor(() => !$("planSummary").hidden && $("costTotal").textContent === "3",
    8000, "feasible plan restored");
  results.push("clearing preserve marks restored optimal cost=3");

  // Adopt every move/delete suggestion, recomputing over the real HTTP API.
  let lastCost = initialCost;
  let adoptions = 0;
  for (let i = 0; i < 12; i++) {
    await sleep(40);
    const btn = $("btnAdopt");
    if (btn.disabled) break;
    const label = btn.textContent.trim();
    btn.click();
    await waitFor(() => parseInt($("costTotal").textContent, 10) < lastCost, 8000, "cost drop after adopt");
    const newCost = parseInt($("costTotal").textContent, 10);
    results.push(`adopt#${adoptions + 1} [${label}] -> cost ${lastCost}->${newCost}`);
    if (newCost !== lastCost - 1) throw new Error("cost did not drop by exactly 1");
    lastCost = newCost;
    adoptions += 1;
  }
  if (adoptions < 1) throw new Error("expected at least one adoption");
  if (!$("btnAdopt").disabled) throw new Error("adopt should be disabled at terminal state");
  results.push(`terminal counts 移${$("cntMove").textContent} 删${$("cntDel").textContent} 补${$("cntAdd").textContent}`);
  if ($("cntMove").textContent !== "0" || $("cntDel").textContent !== "0")
    throw new Error("terminal should have no move/delete");
  if (parseInt($("cntAdd").textContent, 10) < 1) throw new Error("terminal should have add steps");

  // Playback single-step controls.
  $("btnNext").click();
  await sleep(60);
  if (!document.querySelector("#stepList .step.current")) throw new Error("no current playback step");
  results.push("playback next highlights a step");
  $("btnPrev").click();
  await sleep(60);
  $("btnResetPb").click();
  await sleep(60);
  results.push("playback prev/reset ok");

  // A preserve change synchronously invalidates the old suggestion ...
  const cb = document.querySelector("#strokeList input[type=checkbox]");
  if (!cb) throw new Error("no preserve checkbox at terminal");
  cb.checked = true;
  cb.dispatchEvent(new window.Event("change", { bubbles: true }));
  if ($("staleNotice").hidden) throw new Error("stale notice should show immediately after preserve toggle");
  if (!$("btnAdopt").disabled) throw new Error("adopt should be disabled while stale");
  // ... then auto-recompute yields a fresh feasible terminal plan.
  await waitFor(() => !$("planSummary").hidden && $("staleNotice").hidden, 8000, "auto replan after preserve");
  results.push("preserve toggle invalidated old plan and auto-recomputed a fresh one");

  // Stroke delete button also invalidates.
  results.push("stroke rows rendered=" + document.querySelectorAll("#strokeList .stroke-item").length);

  if (errors.length) {
    console.log(results.join("\n"));
    console.error("\nJS ERRORS:\n" + errors.slice(0, 10).join("\n"));
    process.exit(2);
  }
  console.log(results.join("\n"));
  console.log("\nJS DOM HARNESS: PASS (no page errors)");
  process.exit(0);
} catch (e) {
  console.log(results.join("\n"));
  console.error("HARNESS FAIL:", e.stack || e);
  if (errors.length) console.error("PAGE ERRORS:\n" + errors.slice(0, 10).join("\n"));
  process.exit(1);
}
