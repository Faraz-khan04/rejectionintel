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

  const ADAPTERS = {
    "greenhouse.io": {
      title: () => first([".app-title", "h1.section-header", "h1"]),
      company: () =>
        first([".company-name", "#header .company-name"]).replace(/^at\s+/i, "") ||
        document.title.split(" at ").pop(),
      jd: () => txt(q("#content")) || txt(q(".job__description")),
      applyButton: () => q("#submit_app, input[type=submit], button[type=submit]"),
    },
    "lever.co": {
      title: () => first([".posting-headline h2", "h2"]),
      company: () =>
        first([".main-header-logo img[alt]"]) ||
        (q(".main-header-logo img") || {}).alt ||
        location.hostname.split(".")[0],
      jd: () => txt(q(".section-wrapper.page-full-width")) || txt(q(".content")),
      applyButton: () => q(".template-btn-submit, button[type=submit]"),
    },
    "ashbyhq.com": {
      title: () => first(["h1", "[class*='jobPostingHeader'] h1"]),
      company: () =>
        first(["[class*='companyName']"]) || document.title.split(" @ ").pop(),
      jd: () => txt(q("[class*='jobPostingDescription']")) || txt(q("main")),
      applyButton: () => q("button[type=submit]"),
    },
    "linkedin.com": {
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
