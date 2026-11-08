const $ = (id) => document.getElementById(id);
const today = () => new Date().toLocaleDateString("en-CA");

async function show() {
  const { enabled = true, days = {} } = await chrome.storage.local.get(["enabled", "days"]);
  $("enabled").checked = enabled;
  const vals = Object.values(days[today()] || {});
  const rot = vals.filter(Boolean).length;
  $("rot").textContent = rot;
  $("reels").textContent = vals.length;
  $("pct").textContent = vals.length ? Math.round((rot * 100) / vals.length) + "%" : "0%";
}

async function showMode() {
  if (CONFIG.MODE === "demo") {
    $("mode").textContent = "Demo mode: scores come from motion, not the model.";
    return;
  }
  try {
    const res = await fetch(CONFIG.API_URL + "/health");
    if (!res.ok) { $("mode").textContent = `Model API error ${res.status}.`; return; }
    const h = await res.json();
    $("mode").textContent = h.has_api_key === false
      ? "Model API online, but GEMINI_API_KEY is not set on the server."
      : `Model online${h.model ? " (" + h.model + ")" : ""}.`;
  } catch (e) {
    $("mode").textContent = "Model API offline (" + CONFIG.API_URL + ").";
  }
}

$("enabled").addEventListener("change", (e) => chrome.storage.local.set({ enabled: e.target.checked }));
$("reset").addEventListener("click", () => chrome.runtime.sendMessage({ type: "RESET" }));
chrome.storage.onChanged.addListener(show);
show();
showMode();
