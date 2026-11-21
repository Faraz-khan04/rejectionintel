/* RejectionIntel — background service worker (MV3).
 *
 * QUEUE FIRST, SEND SECOND
 * ------------------------
 * The single most likely moment for a capture to be lost is the one it
 * happens in: the user clicks Apply and closes the tab. So every capture is
 * written to chrome.storage.local before any network call, and the queue is
 * drained separately. A failed send leaves the item queued for the next
 * attempt rather than dropping it.
 *
 * MV3 service workers are also killed aggressively when idle, which is a
 * second reason not to hold state in memory.
 *
 * REVIEW, DON'T CAPTURE SILENTLY
 * ------------------------------
 * A badge and a notification tell the user what was recorded. Silent
 * collection of browsing behaviour is what gets extensions pulled from
 * stores, and it directly contradicts a product whose pitch is that it
 * won't claim things it can't support.
 */

// Production default. Development overrides it from the popup's
// Advanced field. Shipping localhost as the default means a real
// install silently queues captures against a server that is not
// running on the user's machine.
const DEFAULT_ENDPOINT = "https://rejectionintel.example";  // <-- set your domain
const QUEUE_KEY = "ri_queue";
const MAX_QUEUE = 200;

async function getConfig() {
  const c = await chrome.storage.local.get(["ri_token", "ri_endpoint"]);
  return {
    token: c.ri_token || "",
    endpoint: (c.ri_endpoint || DEFAULT_ENDPOINT).replace(/\/+$/, ""),
  };
}

async function readQueue() {
  const c = await chrome.storage.local.get(QUEUE_KEY);
  return Array.isArray(c[QUEUE_KEY]) ? c[QUEUE_KEY] : [];
}

async function writeQueue(items) {
  await chrome.storage.local.set({ [QUEUE_KEY]: items.slice(-MAX_QUEUE) });
  const n = items.length;
  await chrome.action.setBadgeText({ text: n ? String(n) : "" });
  await chrome.action.setBadgeBackgroundColor({ color: "#5A47E0" });
}

async function enqueue(item) {
  const queue = await readQueue();
  // Same role captured twice in one session — a double click, or a board that
  // fires submit more than once.
  const dup = queue.some(
    (q) => q.company === item.company && q.job_title === item.job_title
  );
  if (!dup) {
    queue.push({ ...item, queued_at: Date.now() });
    await writeQueue(queue);
  }
  return dup;
}

async function drain() {
  const { token, endpoint } = await getConfig();
  if (!token) return { sent: 0, reason: "no token" };

  const queue = await readQueue();
  if (!queue.length) return { sent: 0, reason: "empty" };

  let sent = 0;
  let i = 0;
  const keep = [];

  for (; i < queue.length; i++) {
    const item = queue[i];
    let res;
    try {
      res = await fetch(`${endpoint}/api/applications/capture`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(item),
      });
    } catch (e) {
      keep.push(item); // offline — retry later
      continue;
    }

    if (res.ok) {
      sent += 1;
    } else if (res.status === 401) {
      // A rejected token fails identically for every item, so stop trying.
      // CRITICAL: everything from here to the end must be kept, not just the
      // current item — dropping the tail would silently lose captures the
      // user made before their token expired.
      keep.push(...queue.slice(i));
      // Clear the dead credential so the popup offers Reconnect rather than
      // showing a green light over a token that no longer works.
      await chrome.storage.local.remove("ri_token");
      await chrome.storage.local.set({
        ri_needs_reconnect: true,
        ri_last_error: "Sign-in expired. Reconnect from the popup.",
      });
      break;
    } else if (res.status === 400) {
      // Malformed and will never succeed. Dropping it is correct; keeping it
      // would block the queue behind an item that can never drain.
      continue;
    } else {
      keep.push(item); // server hiccup — retry later
    }
  }

  await writeQueue(keep);
  if (sent) await chrome.storage.local.remove("ri_last_error");
  return { sent, remaining: keep.length };
}

/* Open the app and let connect.js mint a token from inside the page. The
 * pending flag survives the sign-in redirect, so "Connect -> sign in" is one
 * motion rather than two clicks. */
async function connect() {
  const { endpoint } = await getConfig();
  await chrome.storage.local.set({ ri_connect_pending: true });
  await chrome.storage.local.remove("ri_connect_state");
  const tab = await chrome.tabs.create({
    url: `${endpoint}/app?ri_connect=1`,
    active: true,
  });
  await chrome.storage.local.set({ ri_connect_tab: tab.id });
  return { opened: true };
}

async function handshake(result) {
  const r = result || {};

  if (!r.ok || !r.token) {
    // Leave the tab open: signed_out means the user has a step to take, and
    // closing the tab would take away the thing they need.
    await chrome.storage.local.set({ ri_connect_state: r.reason || "failed" });
    return { ok: false };
  }

  await chrome.storage.local.set({
    ri_token: r.token,
    ri_account: r.account || "",
  });
  await chrome.storage.local.remove([
    "ri_last_error", "ri_needs_reconnect", "ri_connect_pending",
    "ri_connect_state",
  ]);

  const c = await chrome.storage.local.get("ri_connect_tab");
  if (c.ri_connect_tab) {
    try { await chrome.tabs.remove(c.ri_connect_tab); } catch (e) { /* closed */ }
    await chrome.storage.local.remove("ri_connect_tab");
  }

  const drained = await drain();
  return { ok: true, ...drained };
}

chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
  if (!msg || !msg.type) return;

  if (msg.type === "capture") {
    (async () => {
      const dup = await enqueue(msg.data);
      if (!dup) {
        await chrome.action.setTitle({
          title: `Captured: ${msg.data.job_title} at ${msg.data.company}`,
        });
      }
      const result = await drain();
      respond({ ok: true, duplicate: dup, ...result });
    })();
    return true; // keep the channel open for the async reply
  }

  if (msg.type === "drain") {
    drain().then(respond);
    return true;
  }

  if (msg.type === "connect") {
    connect().then(respond);
    return true;
  }

  if (msg.type === "handshake_result") {
    handshake(msg.result).then(respond);
    return true;
  }

  if (msg.type === "ghost") {
    // The content script cannot fetch this itself: content scripts are subject
    // to CORS and the API sends no CORS headers. Routing through the worker
    // also means the token never enters the page.
    (async () => {
      const { token, endpoint } = await getConfig();
      const body = new URLSearchParams({
        jd_text: (msg.jd_text || "").slice(0, 20000),
        company: msg.company || "",
      });
      const headers = { "Content-Type": "application/x-www-form-urlencoded" };
      if (token) headers.Authorization = `Bearer ${token}`;
      try {
        const res = await fetch(`${endpoint}/api/ghost/check`,
                                { method: "POST", headers, body });
        if (!res.ok) { respond({ error: `http_${res.status}` }); return; }
        respond({ report: await res.json(), connected: Boolean(token) });
      } catch (e) {
        respond({ error: "unreachable" });
      }
    })();
    return true;
  }

  if (msg.type === "signals") {
    (async () => {
      const { token, endpoint } = await getConfig();
      if (!token) { respond({ error: "not_connected" }); return; }
      const qs = msg.company ? `?company=${encodeURIComponent(msg.company)}` : "";
      try {
        const res = await fetch(`${endpoint}/api/cohort/signals${qs}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) { respond({ error: `http_${res.status}` }); return; }
        respond({ signals: await res.json() });
      } catch (e) {
        respond({ error: "unreachable" });
      }
    })();
    return true;
  }

  if (msg.type === "analyze") {
    // /api/analyze-jd is unauthenticated and form-encoded. The resume never
    // leaves storage except on this call, and only to the user's own server.
    (async () => {
      const { endpoint } = await getConfig();
      const store = await chrome.storage.local.get("ri_resume");
      const resume = (store.ri_resume || "").trim();
      if (resume.length < 80) { respond({ error: "no_resume" }); return; }

      const body = new URLSearchParams({
        jd_text: (msg.jd_text || "").slice(0, 20000),
        resume_text: resume.slice(0, 40000),
      });
      try {
        const res = await fetch(`${endpoint}/api/analyze-jd`, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { respond({ error: data.detail || `http_${res.status}` }); return; }
        respond({ report: data });
      } catch (e) {
        respond({ error: "unreachable" });
      }
    })();
    return true;
  }

  if (msg.type === "autofill_result") {
    // Injected script reporting back. Parked for the popup to read, because
    // the popup may already have closed by the time the fill finishes.
    chrome.storage.local.set({ ri_autofill: { ...msg, at: Date.now() } });
    respond({ ok: true });
    return true;
  }

  if (msg.type === "queue") {
    readQueue().then((q) => respond({ queue: q }));
    return true;
  }
});

// Retry on wake — the worker is killed when idle, so this is the only
// reliable moment to flush anything stranded by an earlier failure.
chrome.runtime.onStartup.addListener(() => drain());
chrome.runtime.onInstalled.addListener(() => drain());
