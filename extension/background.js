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

const DEFAULT_ENDPOINT = "http://127.0.0.1:8000";
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
      await chrome.storage.local.set({
        ri_last_error: "Token rejected. Copy a fresh one from the app.",
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

  if (msg.type === "queue") {
    readQueue().then((q) => respond({ queue: q }));
    return true;
  }
});

// Retry on wake — the worker is killed when idle, so this is the only
// reliable moment to flush anything stranded by an earlier failure.
chrome.runtime.onStartup.addListener(() => drain());
chrome.runtime.onInstalled.addListener(() => drain());
