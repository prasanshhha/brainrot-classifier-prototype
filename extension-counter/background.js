importScripts("config.js");

const today = () => new Date().toLocaleDateString("en-CA"); // local YYYY-MM-DD

function tally(day) {
  const vals = Object.values(day || {});
  return { reels: vals.length, rot: vals.filter(Boolean).length };
}

async function updateBadge() {
  const { enabled = true, days = {} } = await chrome.storage.local.get(["enabled", "days"]);
  if (!enabled) {
    await chrome.action.setBadgeBackgroundColor({ color: "#888" });
    return chrome.action.setBadgeText({ text: "off" });
  }
  const { rot } = tally(days[today()]);
  await chrome.action.setBadgeBackgroundColor({ color: "#d93025" });
  await chrome.action.setBadgeText({ text: rot ? String(rot) : "" });
}

// one writer, one write at a time, so two tabs can't clobber each other
let queue = Promise.resolve();
function record(id, flagged) {
  queue = queue.then(async () => {
    const { days = {} } = await chrome.storage.local.get("days");
    const key = today();
    days[key] = days[key] || {};
    days[key][id] = flagged ? 1 : 0;
    for (const k of Object.keys(days).sort().slice(0, -CONFIG.KEEP_DAYS)) delete days[k];
    await chrome.storage.local.set({ days });
    await updateBadge();
  }).catch((e) => console.warn("record failed", e));
  return queue;
}

async function resetToday() {
  queue = queue.then(async () => {
    const { days = {} } = await chrome.storage.local.get("days");
    delete days[today()];
    await chrome.storage.local.set({ days });
    await updateBadge();
  });
  return queue;
}

async function toB64(blob) {
  const buf = new Uint8Array(await blob.arrayBuffer());
  let bin = "";
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return btoa(bin);
}

// screenshot the visible tab, crop to the video, shrink to 384px wide
async function capture(windowId, rect, dpr) {
  const url = await chrome.tabs.captureVisibleTab(windowId, { format: "jpeg", quality: 70 });
  const bmp = await createImageBitmap(await (await fetch(url)).blob());
  const sx = Math.max(0, rect.x * dpr), sy = Math.max(0, rect.y * dpr);
  const sw = Math.min(bmp.width - sx, rect.width * dpr), sh = Math.min(bmp.height - sy, rect.height * dpr);
  if (sw < 10 || sh < 10) throw new Error("video is off screen");
  const scale = Math.min(1, 384 / sw);
  const canvas = new OffscreenCanvas(Math.round(sw * scale), Math.round(sh * scale));
  canvas.getContext("2d").drawImage(bmp, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
  return toB64(await canvas.convertToBlob({ type: "image/jpeg", quality: 0.7 }));
}

async function tinyGray(b64) {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const bmp = await createImageBitmap(new Blob([bytes], { type: "image/jpeg" }));
  const c = new OffscreenCanvas(32, 56);
  const ctx = c.getContext("2d");
  ctx.drawImage(bmp, 0, 0, 32, 56);
  const px = ctx.getImageData(0, 0, 32, 56).data;
  const g = new Float32Array(32 * 56);
  for (let i = 0; i < g.length; i++) g[i] = (0.299 * px[i * 4] + 0.587 * px[i * 4 + 1] + 0.114 * px[i * 4 + 2]) / 255;
  return g;
}

// demo only: fast-changing frames score high. not the trained model
async function demoScore(frames) {
  const grays = await Promise.all(frames.map(tinyGray));
  let total = 0, cuts = 0;
  for (let i = 1; i < grays.length; i++) {
    let d = 0;
    for (let k = 0; k < grays[i].length; k++) d += Math.abs(grays[i][k] - grays[i - 1][k]);
    d /= grays[i].length;
    total += d;
    if (d > 0.25) cuts++;
  }
  const motion = grays.length > 1 ? total / (grays.length - 1) : 0;
  const prob = 1 / (1 + Math.exp(-(-2.5 + 20 * motion + 0.5 * cuts)));
  return {
    prob: Math.round(prob * 1000) / 1000,
    flagged: prob >= CONFIG.DEMO_THRESHOLD,
    reasons: [`demo score from motion ${motion.toFixed(3)}, ${cuts} cuts`],
  };
}

async function predict(id, platform, frames) {
  if (CONFIG.MODE === "demo") return demoScore(frames);
  const res = await fetch(CONFIG.API_URL + "/predict", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ video_id: id.slice(3), platform, frames_b64: frames, cuts_per_sec: null }),
    signal: AbortSignal.timeout(CONFIG.API_TIMEOUT_MS),
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${(await res.text()).slice(0, 200)}`);
  const data = await res.json();
  return { prob: data.prob, flagged: data.flagged, reasons: data.reasons || [] };
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    try {
      if (msg.type === "CAPTURE") reply({ ok: true, frame: await capture(sender.tab.windowId, msg.rect, msg.dpr) });
      else if (msg.type === "PREDICT") reply({ ok: true, data: await predict(msg.id, msg.platform || "instagram", msg.frames) });
      else if (msg.type === "RECORD") { await record(msg.id, msg.flagged); reply({ ok: true }); }
      else if (msg.type === "RESET") { await resetToday(); reply({ ok: true }); }
      else reply({ ok: false, error: "unknown message" });
    } catch (e) {
      reply({ ok: false, error: String(e) });
    }
  })();
  return true;
});

chrome.storage.onChanged.addListener((changes) => { if (changes.enabled) updateBadge(); });
chrome.runtime.onInstalled.addListener(updateBadge);
chrome.runtime.onStartup.addListener(updateBadge);
