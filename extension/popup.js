/* Popup logic. Kept in a separate file because MV3's content security policy
 * forbids inline script in extension pages. */

const $ = (id) => document.getElementById(id);
const DEFAULT_ENDPOINT = "http://127.0.0.1:8000";

function setMsg(text, isError) {
  const el = $("msg");
  el.textContent = text || "";
  el.className = "msg" + (isError ? " err" : "");
}

async function refresh() {
  const c = await chrome.storage.local.get([
    "ri_token", "ri_endpoint", "ri_queue", "ri_last_error",
  ]);
  $("endpoint").value = c.ri_endpoint || DEFAULT_ENDPOINT;
  $("token").value = c.ri_token || "";

  const queue = Array.isArray(c.ri_queue) ? c.ri_queue : [];
  $("queued").textContent = String(queue.length);

  const pill = $("status");
  if (!c.ri_token) {
    pill.textContent = "no token";
    pill.className = "pill warn";
  } else if (c.ri_last_error) {
    pill.textContent = "token rejected";
    pill.className = "pill warn";
  } else {
    pill.textContent = "ready";
    pill.className = "pill ok";
  }

  if (c.ri_last_error) setMsg(c.ri_last_error, true);
}

$("save").addEventListener("click", async () => {
  const token = $("token").value.trim();
  const endpoint = ($("endpoint").value.trim() || DEFAULT_ENDPOINT).replace(/\/+$/, "");
  await chrome.storage.local.set({ ri_token: token, ri_endpoint: endpoint });
  await chrome.storage.local.remove("ri_last_error");
  setMsg("Saved. Anything queued will send on the next capture.");
  refresh();
});

/* Manual capture for when the automatic trigger misses — a board changed its
 * markup, or the user applied by email. Better than losing the application. */
$("capture").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) { setMsg("No active tab.", true); return; }

  chrome.tabs.sendMessage(tab.id, { type: "scrape" }, (data) => {
    if (chrome.runtime.lastError || !data) {
      setMsg("This page isn't a supported job board.", true);
      return;
    }
    if (!data.company || !data.job_title) {
      setMsg("Couldn't read a company and job title from this page.", true);
      return;
    }
    chrome.runtime.sendMessage({ type: "capture", data }, (res) => {
      setMsg(
        res && res.duplicate
          ? `Already captured: ${data.job_title}`
          : `Captured: ${data.job_title} at ${data.company}`
      );
      refresh();
    });
  });
});

$("flush").addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "drain" }, (res) => {
    if (!res) { setMsg("Background worker didn't respond.", true); return; }
    if (res.reason === "no token") { setMsg("Add your API token first.", true); return; }
    setMsg(`Sent ${res.sent || 0}. ${res.remaining || 0} still queued.`);
    refresh();
  });
});

refresh();
