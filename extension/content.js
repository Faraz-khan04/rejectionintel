/* RejectionIntel — content script.
 *
 * WHY ONE ADAPTER PER BOARD, NOT A GENERIC SCRAPER
 * ------------------------------------------------
 * Every job board restructures its DOM every few months. A single clever
 * heuristic breaks on all five at once and you find out from a user. Small
 * per-board selector maps break one at a time, and the failure is obvious:
 * that board stops capturing while the others keep working.
 *
 * Each adapter also has a fallback chain, so a renamed class degrades to a
 * worse guess rather than to nothing.
 *
 * WHY WE LISTEN FOR THE SUBMIT CLICK, NOT THE URL
 * -----------------------------------------------
 * Confirmation pages are wildly inconsistent — some redirect, some swap the
 * DOM in place, some do nothing visible. The click on the apply button is the
 * one event every board has.
 */

(function () {
  "use strict";

  const q = (sel, root) => (root || document).querySelector(sel);
  const txt = (el) => (el && el.textContent ? el.textContent.trim() : "");

  /* Try each selector in order; first non-empty wins. */
  function first(selectors) {
    for (const sel of selectors) {
      const v = txt(q(sel));
      if (v) return v;
    }
    return "";
  }


  /* ==================================================================
   * PRE-APPLY GHOST FILTER
   *
   * WHY THE HEURISTICS ARE NOT REIMPLEMENTED HERE
   * ---------------------------------------------
   * The obvious build is to port ghost.py's five checks into this file so the
   * banner renders with no network call. That creates two implementations of
   * the same judgement, and the moment one is edited they disagree — the same
   * failure guard 1 exists to prevent for scores. A banner saying "2 signals"
   * over a report saying "3 signals" for one advert is indefensible whichever
   * is right. So the advert is sent to ghost.py and the answer comes back from
   * the one place that owns it.
   *
   * WHAT IS COMPUTED LOCALLY
   * ------------------------
   * Only what the server cannot see: the posting age the board prints on the
   * page. It is shown verbatim, as a fact, and is not fed into any count.
   *
   * There is no risk score, no percentage and no prediction. The banner shows
   * how many named signals fired out of five, what they were, and — when the
   * extension is connected — what this person's own history with the employer
   * says.
   * ================================================================== */

  const BANNER_CSS = `
:host{all:initial}
.wrap{font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;
  border:1px solid #d9d9e3;border-left:3px solid var(--tier,#78809A);
  border-radius:10px;padding:13px 15px;margin:0 0 16px;background:#fff;color:#14161f}
@media (prefers-color-scheme:dark){
  .wrap{background:#14161F;color:#F1F2F7;border-color:#252938}
}
.head{display:flex;align-items:baseline;gap:9px;flex-wrap:wrap}
.count{font-weight:650;font-size:14.5px;color:var(--tier,#78809A)}
.verdict{font-size:13.5px;opacity:.9}
ul{margin:9px 0 0;padding:0 0 0 17px}
li{font-size:13px;margin:3px 0;opacity:.92}
li b{font-weight:600}
.prior{margin:10px 0 0;padding:9px 11px;border-radius:8px;font-size:13px;
  background:rgba(120,128,154,.12)}
.posted{font-size:12.5px;opacity:.75;margin:8px 0 0}
.caveat{font-size:11.5px;opacity:.62;margin:9px 0 0;line-height:1.5}
.dismiss{float:right;background:none;border:0;cursor:pointer;font-size:16px;
  line-height:1;opacity:.45;color:inherit;padding:0 0 0 10px}
.dismiss:hover{opacity:.9}
`;

  const TIER = {                 // the report's own confidence colours
    observed: "#0E8A5F", weak: "#C4820E", none: "#78809A", neg: "#C4433F",
  };

  const escHtml = (v) => String(v == null ? "" : v).replace(/[&<>"']/g,
    (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));

  /* The board prints this; the server never sees it. Shown as text, never
     counted — "45 days" is a fact, "45 days therefore risky" is a claim. */
  function postedNote() {
    const body = (document.body ? document.body.innerText : "") || "";
    const m = body.match(
      /\bposted\s+(?:on\s+)?(\d{1,3})\+?\s*(day|days|week|weeks|month|months)\s+ago\b/i)
      || body.match(/\b(\d{1,3})\+?\s*(days?|weeks?|months?)\s+ago\b/i);
    if (!m) return "";
    return `This board says the posting went up ${m[1]} ${m[2].toLowerCase()} ago.`;
  }

  function titleNode(a) {
    for (const sel of (a.titleSel || [])) {
      const el = q(sel);
      if (el && txt(el)) return el;
    }
    return null;
  }

  function render(root, report, connected) {
    const listing = report.listing || {};
    if (!listing.checked) return false;

    const n = listing.signals_found || 0;
    const total = listing.signals_total || 5;
    const tier = n === 0 ? TIER.observed
      : n <= 2 ? TIER.weak
        : TIER.neg;

    const items = (listing.detail || []).map(
      (d) => `<li><b>${escHtml(d.label)}</b> — ${escHtml(d.why)}</li>`).join("");

    const prior = report.prior
      ? `<p class="prior">${escHtml(report.prior.verdict)}</p>` : "";

    const posted = postedNote();
    const postedLine = posted ? `<p class="posted">${escHtml(posted)}</p>` : "";

    const upgrade = (!connected && !report.prior)
      ? `<p class="posted">Connect the extension to also see how this employer
         has responded to you before.</p>` : "";

    root.innerHTML = `<style>${BANNER_CSS}</style>
      <div class="wrap" style="--tier:${tier}">
        <button class="dismiss" id="x" title="Hide">&times;</button>
        <div class="head">
          <span class="count">${n} of ${total} signals</span>
          <span class="verdict">${escHtml(listing.verdict || "")}</span>
        </div>
        ${items ? `<ul>${items}</ul>` : ""}
        ${prior}
        ${postedLine}
        ${upgrade}
        <p class="caveat">${escHtml(listing.caveat || "")}</p>
      </div>`;

    const x = root.getElementById("x");
    if (x) x.addEventListener("click", () => { root.host.remove(); });
    return true;
  }

  function ghostBanner(a) {
    const anchor = titleNode(a);
    const jd = a.jd() || "";
    // Under 80 characters ghost.py declines to check, and it is right to:
    // a banner over an unread page would be a guess.
    if (!anchor || jd.length < 200) return;

    const host = document.createElement("div");
    host.id = "ri-ghost-banner";
    const root = host.attachShadow({ mode: "open" });
    anchor.parentNode.insertBefore(host, anchor);

    chrome.runtime.sendMessage(
      { type: "ghost", jd_text: jd, company: a.company() || "" },
      (res) => {
        // Any failure removes the banner rather than showing a local guess.
        if (chrome.runtime.lastError || !res || res.error || !res.report) {
          host.remove();
          return;
        }
        if (!render(root, res.report, res.connected)) host.remove();
      });
  }

  const ADAPTERS = {
    "greenhouse.io": {
      // Same list the title text uses; the banner needs the node, not the string.
      titleSel: [".app-title", "h1.section-header", "h1"],
      title: () => first([".app-title", "h1.section-header", "h1"]),
      company: () =>
        first([".company-name", "#header .company-name"]).replace(/^at\s+/i, "") ||
        document.title.split(" at ").pop(),
      jd: () => txt(q("#content")) || txt(q(".job__description")),
      applyButton: () => q("#submit_app, input[type=submit], button[type=submit]"),
    },
    "lever.co": {
      titleSel: [".posting-headline h2", "h2"],
      title: () => first([".posting-headline h2", "h2"]),
      company: () =>
        first([".main-header-logo img[alt]"]) ||
        (q(".main-header-logo img") || {}).alt ||
        location.hostname.split(".")[0],
      jd: () => txt(q(".section-wrapper.page-full-width")) || txt(q(".content")),
      applyButton: () => q(".template-btn-submit, button[type=submit]"),
    },
    "ashbyhq.com": {
      titleSel: ["[class*='jobPostingHeader'] h1", "h1"],
      title: () => first(["h1", "[class*='jobPostingHeader'] h1"]),
      company: () =>
        first(["[class*='companyName']"]) || document.title.split(" @ ").pop(),
      jd: () => txt(q("[class*='jobPostingDescription']")) || txt(q("main")),
      applyButton: () => q("button[type=submit]"),
    },
    "linkedin.com": {
      titleSel: [".job-details-jobs-unified-top-card__job-title",
                 ".t-24.job-details-jobs-unified-top-card__job-title", "h1"],
      title: () =>
        first([".job-details-jobs-unified-top-card__job-title", ".t-24.job-details-jobs-unified-top-card__job-title", "h1"]),
      company: () =>
        first([".job-details-jobs-unified-top-card__company-name", ".jobs-unified-top-card__company-name"]),
      jd: () => txt(q("#job-details")) || txt(q(".jobs-description__content")),
      // LinkedIn Easy Apply is a modal; the final button carries this label.
      applyButton: () =>
        q("button[aria-label*='Submit application']") ||
        q(".jobs-apply-button"),
    },
  };

  function adapter() {
    const host = location.hostname;
    for (const key of Object.keys(ADAPTERS)) {
      if (host.includes(key)) return ADAPTERS[key];
    }
    return null;
  }

  function scrape(a) {
    return {
      company: (a.company() || "").slice(0, 200),
      job_title: (a.title() || "").slice(0, 200),
      jd_text: (a.jd() || "").slice(0, 20000),
      source: location.hostname.replace(/^www\./, ""),
      url: location.href,
      applied_date: new Date().toISOString().slice(0, 10),
    };
  }

  const a = adapter();
  if (!a) return;

  // Boards render the posting after first paint often enough that a single
  // immediate attempt misses. One retry covers it without polling forever.
  if (!document.getElementById("ri-ghost-banner")) {
    setTimeout(() => { try { ghostBanner(a); } catch (e) { /* never break the page */ } }, 700);
    setTimeout(() => {
      if (!document.getElementById("ri-ghost-banner")) {
        try { ghostBanner(a); } catch (e) { /* ignore */ }
      }
    }, 2500);
  }

  let sent = false;

  function capture() {
    if (sent) return;
    const data = scrape(a);
    if (!data.company || !data.job_title) return; // nothing worth sending
    sent = true;
    // Queue in the worker, don't fetch from here: the tab is usually closed
    // moments after applying and an in-flight request dies with it.
    chrome.runtime.sendMessage({ type: "capture", data });
    setTimeout(() => { sent = false; }, 5000);
  }

  /* Capture on the click, in the capture phase, so we run before any handler
     that tears the page down. */
  document.addEventListener(
    "click",
    (e) => {
      const btn = a.applyButton();
      if (btn && (e.target === btn || btn.contains(e.target))) capture();
    },
    true
  );

  /* The popup asks for whatever is on screen right now, for manual capture
     when the automatic trigger misses. */
  chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
    if (msg && msg.type === "scrape") {
      respond(scrape(a));
      return true;
    }
  });
})();
