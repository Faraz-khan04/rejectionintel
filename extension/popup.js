/* Popup logic. Separate file because MV3's CSP forbids inline script.
 *
 * Every line the user reads here is a sentence about their own data. No
 * metric-with-a-label, no invented averages, no percentage the extension
 * cannot show its working for.
 */

const $ = (id) => document.getElementById(id);
// Production default. Development overrides it from the popup's
// Advanced field. Shipping localhost as the default means a real
// install silently queues captures against a server that is not
// running on the user's machine.
const DEFAULT_ENDPOINT = "https://rejectionintel.example";  // <-- set your domain

function setMsg(text, warn) {
  const el = $("msg");
  el.textContent = text || "";
  el.className = "msg" + (warn ? " warn" : "");
}

function show(id, on) { $(id).classList.toggle("hidden", !on); }

/* ---------- connection ---------- */

function paintConnection(c) {
  const dot = $("dot");
  const conn = $("conn");

  if (c.ri_needs_reconnect) {
    dot.className = "dot warn";
    conn.textContent = "Sign-in expired";
    show("connect", true);
    $("connect").textContent = "Reconnect";
    $("connNote").textContent =
      "Your saved sign-in stopped working. Nothing captured has been lost — " +
      "it is queued and will send once you reconnect.";
    show("connNote", true);
    return;
  }

  if (!c.ri_token) {
    dot.className = "dot";
    conn.textContent = "Not connected";
    show("connect", true);
    $("connect").textContent = "Connect to RejectionIntel";
    $("connNote").textContent =
      "Opens the app in a tab and links this extension to your account. " +
      "If you are not signed in yet, sign in and it links itself.";
    show("connNote", true);
    return;
  }

  dot.className = "dot on";
  conn.textContent = c.ri_account
    ? `Connected · ${c.ri_account}`
    : "Connected";
  show("connect", false);
  show("connNote", false);
}

/* ---------- queue ---------- */

function paintQueue(queue) {
  const n = queue.length;
  $("queueLine").textContent =
    n === 0 ? "Nothing waiting to send."
      : n === 1 ? "1 application waiting to send."
        : `${n} applications waiting to send.`;
  show("flush", n > 0);
}

/* ---------- this job ---------- */

async function paintJob() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) { show("jobZone", false); return; }

  chrome.tabs.sendMessage(tab.id, { type: "scrape" }, (data) => {
    // lastError fires on any page without our content script. Not an error
    // worth showing — most tabs are not job postings.
    if (chrome.runtime.lastError || !data || !data.company || !data.job_title) {
      show("jobZone", false);
      return;
    }
    $("jobTitle").textContent = data.job_title;
    $("jobCompany").textContent = data.company;
    show("jobZone", true);
  });
}

/* ---------- follow-up ---------- */

/* A fixed template with two blanks filled from the user's own record.
 *
 * WHAT IS NOT IN IT: any claim about how long employers usually take, what a
 * normal reply window is, or what "most companies" do. The extension does not
 * know those things, and a follow-up email that opens with an invented
 * industry average is worse than one that just asks the question.
 *
 * The recipient is left blank because we do not know it. Guessing
 * careers@company.com would produce bounces the user would only find out
 * about later.
 */
function followUpLink(card) {
  const role = card.role || "the role";
  const company = card.company || "your company";
  const applied = card.applied_at
    ? `on ${card.applied_at}`
    : `${card.days_since} days ago`;

  const subject = `Following up on my ${role} application`;
  const body = [
    "Hello,",
    "",
    `I applied for the ${role} position at ${company} ${applied} and wanted `
      + "to check whether the role is still open and where things stand.",
    "",
    "I'm still very interested, and happy to send anything else that would "
      + "help.",
    "",
    "Thank you for your time.",
  ].join("\n");

  return `mailto:?subject=${encodeURIComponent(subject)}`
       + `&body=${encodeURIComponent(body)}`;
}

function paintSignals(sig) {
  // Every branch here can legitimately be "Not enough data yet." The gates
  // live in core/cohort.py; the popup only renders what it is told.
  const bench = (sig.benchmark && sig.benchmark.verdict) || "Not enough data yet.";
  $("benchLine").textContent = bench;

  const emp = sig.employer;
  $("employerLine").textContent = emp
    ? (emp.available ? emp.statement : `${emp.name}: not enough data yet.`)
    : "";
  show("cohortZone", true);

  const stale = (sig.stale && sig.stale.cards) || [];
  const n = (sig.stale && sig.stale.count) || 0;
  if (!n) { show("staleZone", false); return; }

  $("staleLine").textContent = n === 1
    ? "1 application has had no reply for over 21 days."
    : `${n} applications have had no reply for over 21 days.`;

  const list = $("staleList");
  list.innerHTML = "";
  stale.forEach((card) => {
    const row = document.createElement("div");
    row.className = "stale-row";

    const what = document.createElement("div");
    what.className = "stale-what";
    const b = document.createElement("b");
    b.textContent = card.company;
    const when = document.createElement("span");
    when.className = "stale-when";
    when.textContent = `${card.role} · ${card.days_since} days`;
    what.appendChild(b);
    what.appendChild(when);

    const go = document.createElement("button");
    go.className = "stale-go";
    go.textContent = "Follow up";
    go.addEventListener("click", () => {
      chrome.tabs.create({ url: followUpLink(card) });
    });

    row.appendChild(what);
    row.appendChild(go);
    list.appendChild(row);
  });
  show("staleZone", true);
}

async function loadSignals() {
  // Needs the company from the page when there is one, so the employer line
  // can be about the job being looked at rather than nothing in particular.
  let company = "";
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab && tab.id) {
      company = await new Promise((resolve) => {
        chrome.tabs.sendMessage(tab.id, { type: "scrape" }, (d) => {
          resolve((!chrome.runtime.lastError && d && d.company) || "");
        });
      });
    }
  } catch (e) { /* not a job board */ }

  chrome.runtime.sendMessage({ type: "signals", company }, (res) => {
    if (chrome.runtime.lastError || !res || res.error || !res.signals) {
      show("cohortZone", false);
      show("staleZone", false);
      return;
    }
    paintSignals(res.signals);
  });
}

/* ---------- skill gap (Feature 6) ---------- */

/* Renders the endpoint's own JSON. No sentence is composed here around a
 * number the server sent, and no number is computed here at all: the same
 * compute_fit() behind the full report produced these, and a second opinion
 * from the popup could only ever disagree with it.
 */
function paintGap(r) {
  const gap = $("gap");
  const missing = r.missing_skills || [];

  const items = missing.map((m) => {
    const need = m.necessity === "required" ? "required" : "preferred";
    return `<li>${escapeHtml(m.skill_name)}<span class="need">${need}</span>`
         + `<span class="wks">${escapeHtml(m.weeks_range || "")}`
         + `${m.milestone ? " · " + escapeHtml(m.milestone) : ""}</span></li>`;
  }).join("");

  gap.innerHTML = `
    <div class="gap-head">
      <span class="gap-pct">${r.overall_match_percentage}%</span>
      <span class="gap-band">${escapeHtml(r.band || "")}</span>
    </div>
    <p class="gap-verdict">${escapeHtml(r.verdict || "")}</p>
    ${items ? `<ul class="gap-list">${items}</ul>`
            : `<p class="gap-note">Nothing listed in this job description is missing
               from your resume.</p>`}
    ${r.note ? `<p class="gap-note">${escapeHtml(r.note)}</p>` : ""}`;
}

function escapeHtml(v) {
  return String(v == null ? "" : v).replace(/[&<>"']/g,
    (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
}

/* ---------- refresh ---------- */

async function refresh() {
  const c = await chrome.storage.local.get([
    "ri_token", "ri_endpoint", "ri_account", "ri_queue",
    "ri_last_error", "ri_needs_reconnect", "ri_connect_state",
    "ri_resume", "ri_profile",
  ]);

  $("endpoint").value = c.ri_endpoint || DEFAULT_ENDPOINT;
  $("token").value = c.ri_token || "";
  $("resume").value = c.ri_resume || "";
  $("profile").value = c.ri_profile
    ? JSON.stringify(c.ri_profile, null, 2) : "";

  paintConnection(c);
  paintQueue(Array.isArray(c.ri_queue) ? c.ri_queue : []);
  paintJob();
  if (c.ri_token && !c.ri_needs_reconnect) loadSignals();
  else { show("cohortZone", false); show("staleZone", false); }

  if (c.ri_connect_state === "signed_out") {
    setMsg("Sign in on the tab that just opened. This connects itself once " +
           "you are in.", true);
  } else if (c.ri_connect_state === "unreachable") {
    setMsg("Could not reach the server. Is it running?", true);
  } else if (c.ri_last_error) {
    setMsg(c.ri_last_error, true);
  }
}

/* ---------- actions ---------- */

$("connect").addEventListener("click", () => {
  setMsg("Opening the app…");
  chrome.runtime.sendMessage({ type: "connect" }, () => {
    // The tab does the work; the worker stores the result. Closing the popup
    // is expected here, so state is read fresh on next open rather than held.
    setTimeout(refresh, 600);
  });
});

$("capture").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) { setMsg("No active tab.", true); return; }

  chrome.tabs.sendMessage(tab.id, { type: "scrape" }, (data) => {
    if (chrome.runtime.lastError || !data) {
      setMsg("This page is not a supported job board.", true);
      return;
    }
    if (!data.company || !data.job_title) {
      setMsg("Could not read a company and job title from this page.", true);
      return;
    }
    chrome.runtime.sendMessage({ type: "capture", data }, (res) => {
      setMsg(res && res.duplicate
        ? `Already captured: ${data.job_title}`
        : `Captured: ${data.job_title} at ${data.company}`);
      refresh();
    });
  });
});

$("flush").addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "drain" }, (res) => {
    if (!res) { setMsg("Background worker did not respond.", true); return; }
    if (res.reason === "no token") { setMsg("Connect first.", true); return; }
    setMsg(`Sent ${res.sent || 0}. ${res.remaining || 0} still waiting.`);
    refresh();
  });
});

$("analyze").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) return;

  setMsg("Reading the job description…");
  chrome.tabs.sendMessage(tab.id, { type: "scrape" }, (data) => {
    if (chrome.runtime.lastError || !data || !data.jd_text) {
      setMsg("Could not read a job description from this page.", true);
      return;
    }
    chrome.runtime.sendMessage(
      { type: "analyze", jd_text: data.jd_text }, (res) => {
        if (!res || res.error === "no_resume") {
          setMsg("Add your resume text under Advanced first.", true);
          return;
        }
        if (res.error) { setMsg(String(res.error), true); return; }
        setMsg("");
        paintGap(res.report);
      });
  });
});

$("autofill").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) return;

  const store = await chrome.storage.local.get("ri_profile");
  if (!store.ri_profile) {
    setMsg("Add your autofill profile under Advanced first.", true);
    return;
  }

  await chrome.storage.local.remove("ri_autofill");
  try {
    // Injected on demand rather than running on every page: a script that can
    // write into forms should only be present when it was asked for.
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["autofill.js"],
    });
  } catch (e) {
    setMsg("This page does not allow filling.", true);
    return;
  }

  setTimeout(async () => {
    const r = (await chrome.storage.local.get("ri_autofill")).ri_autofill;
    if (!r) { setMsg("No response from the page.", true); return; }
    if (r.error === "no_profile") {
      setMsg("Autofill profile is not valid JSON.", true);
      return;
    }
    const base = r.filled === 1 ? "Filled 1 field." : `Filled ${r.filled} fields.`;
    // Stated separately, never folded into the count: a file input cannot be
    // set by script, and implying otherwise would send an empty application.
    setMsg(r.file_fields
      ? `${base} Attach your resume yourself — browsers do not allow a script `
        + "to do that."
      : base);
  }, 400);
});

$("save").addEventListener("click", async () => {
  const token = $("token").value.trim();
  const endpoint = ($("endpoint").value.trim() || DEFAULT_ENDPOINT)
    .replace(/\/+$/, "");
  const patch = { ri_endpoint: endpoint };
  if (token) patch.ri_token = token;

  patch.ri_resume = $("resume").value.trim();
  const raw = $("profile").value.trim();
  if (raw) {
    try {
      patch.ri_profile = JSON.parse(raw);
    } catch (e) {
      setMsg("Autofill profile is not valid JSON — nothing saved.", true);
      return;
    }
  }
  await chrome.storage.local.set(patch);
  await chrome.storage.local.remove([
    "ri_last_error", "ri_needs_reconnect", "ri_connect_state",
  ]);
  setMsg("Saved.");
  refresh();
});

$("disconnect").addEventListener("click", async () => {
  // Local only. The token stays valid server-side until revoked from the app,
  // which is the honest description of what this button does.
  await chrome.storage.local.remove([
    "ri_token", "ri_account", "ri_last_error",
    "ri_needs_reconnect", "ri_connect_state",
  ]);
  setMsg("Disconnected here. Anything captured stays queued.");
  refresh();
});

refresh();
