/* RejectionIntel — header sign-in box.
 *
 * WHY SHADOW DOM
 * --------------
 * This mounts into six pages whose stylesheets were written independently and
 * all define .btn, .row and .muted. Without isolation the panel inherits
 * whichever page it lands on and looks subtly broken on five of them. Custom
 * properties still pierce the boundary, so the panel reads --accent, --card
 * and the tier colours from :root and follows the light/dark toggle for free.
 *
 * WHAT IT WILL NOT DO
 * -------------------
 * No password strength meter. No "account already exists" message — the
 * server deliberately answers identically for a taken address and a new one,
 * and a UI that guessed otherwise would undo that. No social button for a
 * provider the server has no keys for; the button list comes from
 * /api/auth/providers, so a dead button cannot render.
 *
 * MOUNTING
 * --------
 *   <div id="authbox"></div>
 *   <script src="/authbox.js" defer></script>
 *
 * Falls back to .nav-right, then to the end of <body>.
 */

(function () {
  "use strict";

  const CSS = `
:host{display:inline-block;position:relative;font:14px/1.5 var(--body,system-ui,sans-serif)}
button{font:inherit;cursor:pointer;border-radius:10px;border:1px solid transparent}
.trigger{background:var(--ri-accent);color:var(--ri-card);border:0;
  height:var(--ri-ctl-h,38px);padding:0 15px;font-weight:600;font-size:13.5px}
.trigger:hover{background:var(--ri-accent-2)}
.trigger.plain{background:none;color:var(--ri-ink-2);
  border:1px solid var(--ri-line);font-weight:500}
.trigger.plain:hover{color:var(--ri-ink)}
/* Signed-in state is an initial, not an address. The full address is in the
   menu this opens, which is where someone looks when they want to check which
   account they are on — not something to leave on screen permanently. */
.trigger.avatar{width:var(--ri-ctl-h,38px);height:var(--ri-ctl-h,38px);padding:0;
  border-radius:50%;background:none;border:1px solid var(--ri-line);
  color:var(--ri-ink-2);font-weight:600;font-size:14px;line-height:1;
  display:grid;place-items:center}
.trigger.avatar:hover{color:var(--ri-ink);border-color:var(--ri-ink-2);
  background:none}

.panel{position:fixed;width:320px;box-sizing:border-box;z-index:2147483000;
  background:var(--ri-card);border:1px solid var(--ri-line);
  border-radius:14px;padding:18px;
  box-shadow:0 1px 2px rgba(0,0,0,.18),0 18px 44px -14px rgba(0,0,0,.45)}
.panel[hidden]{display:none}

.tabs{display:flex;gap:2px;margin:0 0 16px;border-bottom:1px solid var(--ri-line)}
.tab{background:none;border:0;border-radius:0;padding:7px 2px;margin-right:18px;
  color:var(--ri-muted);font-size:13px;border-bottom:2px solid transparent}
.tab[aria-selected="true"]{color:var(--ri-ink);font-weight:600;
  border-bottom-color:var(--ri-accent)}

label{display:block;font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--ri-muted);margin:0 0 5px}
input{width:100%;box-sizing:border-box;padding:9px 11px;border-radius:9px;
  border:1px solid var(--ri-line);background:var(--ri-field);
  color:var(--ri-ink);font:14px var(--body,system-ui,sans-serif);
  margin-bottom:12px}
input:focus{outline:2px solid var(--ri-accent);outline-offset:1px}

.submit{width:100%;background:var(--ri-accent);color:#fff;border:0;
  padding:10px;font-weight:600}
.submit:hover{background:var(--ri-accent-2)}
.submit[disabled]{opacity:.6;cursor:default}

.linkish{background:none;border:0;padding:0;margin-top:10px;
  color:var(--ri-muted);font-size:12.5px;text-decoration:underline}
.linkish:hover{color:var(--ri-ink)}

.or{display:flex;align-items:center;gap:10px;margin:16px 0 12px;
  color:var(--ri-muted);font-size:11px;letter-spacing:.06em;
  text-transform:uppercase}
.or::before,.or::after{content:"";flex:1;height:1px;background:var(--ri-line)}
.or[hidden]{display:none}

.oauth{display:flex;flex-direction:column;gap:8px}
.oauth button{width:100%;background:none;border:1px solid var(--ri-line);
  color:var(--ri-ink);padding:9px;font-size:13.5px;font-weight:500;
  display:flex;align-items:center;justify-content:center;gap:9px}
.oauth button:hover{border-color:var(--ri-muted)}
.oauth svg{width:16px;height:16px;flex:none}

.note{font-size:12px;line-height:1.5;margin:12px 0 0;padding:9px 11px;
  border-radius:9px}
.note.ok{background:var(--ri-ok-bg);color:var(--ri-ok)}
.note.bad{background:var(--ri-bad-bg);color:var(--ri-bad)}
.note.flat{background:var(--ri-flat-bg);color:var(--ri-ink-2)}
.note[hidden]{display:none}
.note a{color:inherit;font-family:var(--mono,monospace);font-size:11px;
  word-break:break-all}

.who{font-size:13px;color:var(--ri-ink);margin:0 0 14px;
  overflow:hidden;text-overflow:ellipsis}
.menu button{width:100%;text-align:left;background:none;
  border:1px solid var(--ri-line);padding:9px 11px;font-size:13px;
  color:var(--ri-ink-2);margin-bottom:7px}
.menu button:hover{color:var(--ri-ink);border-color:var(--ri-muted)}
.fine{font-size:11.5px;color:var(--ri-muted);line-height:1.5;margin:8px 0 0}
`;

  const ICONS = {
    google: '<svg viewBox="0 0 24 24"><path fill="#4285F4" d="M22.5 12.2c0-.8-.1-1.4-.2-2H12v3.8h6c-.1 1-.8 2.5-2.2 3.5l3.4 2.6c2-1.8 3.3-4.6 3.3-7.9z"/><path fill="#34A853" d="M12 23c3 0 5.5-1 7.3-2.7l-3.5-2.7c-.9.6-2.2 1.1-3.8 1.1-2.9 0-5.3-1.9-6.2-4.5l-3.6 2.8C3.9 20.5 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.8 14.2c-.2-.7-.4-1.4-.4-2.2s.1-1.5.4-2.2L2.2 7C1.4 8.5 1 10.2 1 12s.4 3.5 1.2 5z"/><path fill="#EA4335" d="M12 5.4c1.6 0 2.8.7 3.4 1.3l2.9-2.8C16.6 2.2 14.2 1 12 1 7.7 1 3.9 3.5 2.2 7l3.6 2.8C6.7 7.3 9.1 5.4 12 5.4z"/></svg>',
    github: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 1.5A10.5 10.5 0 0 0 1.5 12c0 4.6 3 8.6 7.2 10 .5.1.7-.2.7-.5v-1.9c-2.9.6-3.5-1.3-3.5-1.3-.5-1.2-1.2-1.5-1.2-1.5-.9-.6.1-.6.1-.6 1 .1 1.6 1 1.6 1 .9 1.6 2.4 1.1 3 .9.1-.7.4-1.1.6-1.4-2.3-.3-4.7-1.2-4.7-5.1 0-1.1.4-2 1-2.8-.1-.3-.4-1.3.1-2.7 0 0 .8-.3 2.7 1a9.3 9.3 0 0 1 5 0c1.9-1.3 2.7-1 2.7-1 .5 1.4.2 2.4.1 2.7.6.8 1 1.7 1 2.8 0 3.9-2.4 4.8-4.7 5.1.4.3.7 1 .7 1.9v2.9c0 .3.2.6.7.5 4.2-1.4 7.2-5.4 7.2-10A10.5 10.5 0 0 0 12 1.5z"/></svg>',
  };

  function mount() {
    // NOT "#authbox": app.html already owns that id for its own in-page
    // sign-in form, and mounting into it would swallow that form.
    return document.getElementById("ri-authbox")
        || document.querySelector(".nav-right")
        || document.querySelector(".nav-in")
        || document.body;
  }

  /* THEME
   * Custom properties inherit into a shadow root, but only the ones the page
   * actually defines. A missing --card fell back to #fff, which on a dark
   * page produced a white panel with near-white inputs inside it — visible in
   * testing as a panel that looked empty. Resolving the values here, with
   * theme-correct defaults, removes that whole class of failure.
   */
  function syncTheme(el) {
    const cs = getComputedStyle(document.documentElement);
    const dark = document.documentElement.getAttribute("data-theme") === "dark";
    const pick = (name, darkValue, lightValue) =>
      (cs.getPropertyValue(name) || "").trim() || (dark ? darkValue : lightValue);

    const map = {
      "--ri-card":     pick("--card",     "#141416", "#FCFBF9"),
      "--ri-field":    pick("--bg",       "#08080A", "#F4F3F0"),
      "--ri-line":     pick("--line",     "#26262A", "#E0DFDA"),
      "--ri-ink":      pick("--ink",      "#E8E8E4", "#111112"),
      "--ri-ink-2":    pick("--ink-2",    "#9A9AA1", "#44454A"),
      "--ri-muted":    pick("--muted",    "#7E7E85", "#6E7076"),
      "--ri-accent":   pick("--accent",   "#C7C7C9", "#2A2A2E"),
      "--ri-accent-2": pick("--accent-2", "#E8E8E4", "#111112"),
      "--ri-ok":       pick("--sem-strong",    "#3FC495", "#0E8A5F"),
      "--ri-ok-bg":    pick("--sem-strong-bg", "#10352A", "#E3F5ED"),
      "--ri-bad":      pick("--sem-neg",       "#F0776F", "#C4433F"),
      "--ri-bad-bg":   pick("--sem-neg-bg",    "#3A1D1D", "#FBEAEA"),
      "--ri-flat-bg":  pick("--sem-none-bg",   "#1F2431", "#EDEFF4"),
    };
    Object.entries(map).forEach(([k, v]) => el.style.setProperty(k, v));
  }

  /* TWO HOSTS, ON PURPOSE
   * The trigger belongs in the nav. The panel does not: backdrop.css sets
   * `.nav, .wrap { z-index: 1 }`, which lands after app.html's own
   * `.nav{z-index:50}` and flattens the two onto one layer. .wrap comes later
   * in the DOM, so it paints over anything inside the nav — and no z-index
   * within the nav's stacking context can climb out of it. Mounting the panel
   * directly on <body> sidesteps the whole question.
   */
  const host = document.createElement("div");
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>${CSS}</style><div id="slot"></div>`;
  const slot = root.getElementById("slot");
  mount().appendChild(host);

  const panelHost = document.createElement("div");
  panelHost.style.cssText =
    "position:absolute;top:0;left:0;width:0;height:0;z-index:2147483000";
  const panelRoot = panelHost.attachShadow({ mode: "open" });
  panelRoot.innerHTML = `<style>${CSS}</style><div id="panelSlot"></div>`;
  const panelSlot = panelRoot.getElementById("panelSlot");
  document.body.appendChild(panelHost);

  // Look in the panel first: that is where the form and its controls live.
  const el = (id) => panelRoot.getElementById(id) || root.getElementById(id);

  function paint(triggerHTML, panelHTML) {
    slot.innerHTML = triggerHTML;
    panelSlot.innerHTML = panelHTML;
  }

  function applyTheme() { syncTheme(host); syncTheme(panelHost); }
  applyTheme();

  // Follow the page's own light/dark toggle.
  new MutationObserver(applyTheme).observe(
    document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

  let providers = [];
  let mode = "login";          // login | signup | magic

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));

  /* ---------- rendering ---------- */

  function oauthMarkup() {
    if (!providers.length) return `<div class="or" hidden></div>`;
    return `<div class="or">or</div><div class="oauth">` + providers.map((p) =>
      `<button type="button" data-oauth="${esc(p.key)}">${ICONS[p.key] || ""}
        Continue with ${esc(p.label)}</button>`).join("") + `</div>`;
  }

  function signedOut() {
    const isSignup = mode === "signup";
    const isMagic = mode === "magic";
    paint(`<button class="trigger" id="open">Sign in</button>`, `
      <div class="panel" id="panel" hidden>
        <div class="tabs" role="tablist">
          <button class="tab" role="tab" data-mode="login"
            aria-selected="${!isSignup && !isMagic}">Sign in</button>
          <button class="tab" role="tab" data-mode="signup"
            aria-selected="${isSignup}">Create account</button>
        </div>

        <form id="form">
          <label for="e">Email</label>
          <input id="e" type="email" required autocomplete="email"
                 placeholder="you@example.com">
          ${isMagic ? "" : `
          <label for="p">Password</label>
          <input id="p" type="password" required
                 autocomplete="${isSignup ? "new-password" : "current-password"}"
                 placeholder="${isSignup ? "at least 10 characters" : ""}">`}
          <button class="submit" type="submit" id="go">${
            isMagic ? "Email me a link"
              : isSignup ? "Create account" : "Sign in"}</button>
        </form>

        <button class="linkish" id="toggleMagic">${
          isMagic ? "Use a password instead"
                  : "Email me a link instead"}</button>

        ${oauthMarkup()}
        <p class="note" id="note" hidden></p>
      </div>`);
    wireSignedOut();
  }

  function signedIn(me) {
    const initial = (me.display || "?").trim().charAt(0).toUpperCase();
    paint(`<button class="trigger avatar" id="open"
             aria-label="Account menu for ${esc(me.display)}"
             title="${esc(me.display)}">${esc(initial)}</button>`, `
      <div class="panel" id="panel" hidden>
        <p class="who">Signed in as <b>${esc(me.display)}</b></p>
        <div class="menu">
          <button type="button" id="setpw">Set or change password</button>
          <button type="button" id="out">Sign out</button>
          <button type="button" id="outAll">Sign out on every device</button>
        </div>
        <div id="pwForm" hidden>
          <label for="np">New password</label>
          <input id="np" type="password" autocomplete="new-password">
          <button class="submit" type="button" id="savepw">Save password</button>
          <p class="fine">Saving signs out every other device, which is the
            point of changing it.</p>
        </div>
        <p class="note" id="note" hidden></p>
      </div>`);
    wireSignedIn(me);
  }

  /* ---------- helpers ---------- */

  function note(cls, html) {
    const n = el("note");
    if (!n) return;
    n.className = "note " + cls;
    n.innerHTML = html;
    n.hidden = false;
  }

  function place(trigger, panel) {
    const r = trigger.getBoundingClientRect();
    const width = 320;
    // Keep it on screen on a narrow window rather than letting it run off the
    // right edge, which is what right:0 did inside a centred nav.
    const left = Math.max(10, Math.min(r.right - width, window.innerWidth - width - 10));
    panel.style.left = left + "px";
    panel.style.top = (r.bottom + 10) + "px";
  }

  /* Rebuild the panel in the current mode and show it where the trigger is. */
  function reopen() {
    signedOut();
    const trigger = root.getElementById("open");
    const panel = panelRoot.getElementById("panel");
    if (!trigger || !panel) return;
    place(trigger, panel);
    panel.hidden = false;
  }

  function wirePanel() {
    const open = root.getElementById("open");
    const panel = panelRoot.getElementById("panel");
    open.addEventListener("click", (e) => {
      e.stopPropagation();
      const opening = panel.hidden;
      if (opening) place(open, panel);
      panel.hidden = !opening;
    });
    window.addEventListener("resize", () => {
      if (!panel.hidden) place(open, panel);
    });
    window.addEventListener("scroll", () => {
      if (!panel.hidden) place(open, panel);
    }, true);
    panel.addEventListener("click", (e) => e.stopPropagation());
    if (!wirePanel.global) {
      // Once, not once per re-render: switching tabs rebuilds the panel, and
      // a listener per rebuild would leak a closure over a detached node.
      wirePanel.global = true;
      const close = () => {
        const cur = panelRoot.getElementById("panel");
        if (cur) cur.hidden = true;
      };
      document.addEventListener("click", close);
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") close();
      });
    }
  }

  function wireOauth() {
    panelRoot.querySelectorAll("[data-oauth]").forEach((b) => {
      b.addEventListener("click", () => {
        const next = encodeURIComponent(location.pathname);
        location.href = `/auth/oauth/${b.dataset.oauth}?next=${next}`;
      });
    });
  }

  /* A reload is the correct default: after signing in, most of a page's
   * content is different. /app is the exception — reloading there throws away
   * a CSV the user may have just chosen, so that page opts out and re-renders
   * itself instead. */
  async function afterAuthChange() {
    const me = await fetch("/api/auth/me").then((r) => r.json()).catch(() => null);
    document.dispatchEvent(new CustomEvent("ri-auth-changed", { detail: me }));
    if (window.RI_NO_RELOAD_ON_AUTH && me) {
      if (me.signed_in) signedIn(me); else signedOut();
    } else {
      location.reload();
    }
  }

  async function post(url, fields) {
    const body = new FormData();
    Object.entries(fields).forEach(([k, v]) => body.append(k, v));
    const res = await fetch(url, { method: "POST", body });
    let data = {};
    try { data = await res.json(); } catch (e) { /* empty body */ }
    return { res, data };
  }

  /* ---------- wiring ---------- */

  function wireSignedOut() {
    wirePanel();
    wireOauth();

    panelRoot.querySelectorAll(".tab").forEach((t) => {
      t.addEventListener("click", () => {
        mode = t.dataset.mode;
        reopen();
      });
    });

    el("toggleMagic").addEventListener("click", () => {
      mode = mode === "magic" ? "login" : "magic";
      reopen();
    });

    el("form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const email = el("e").value.trim();
      const pwEl = el("p");
      const password = pwEl ? pwEl.value : "";
      const go = el("go");

      go.disabled = true;
      const label = go.textContent;
      go.textContent = "Working…";

      try {
        if (mode === "magic") {
          const { res, data } = await post("/api/auth/request", { email });
          if (res.status === 429) note("bad", esc(data.detail));
          else if (!res.ok) note("bad", esc(data.detail || "Something went wrong."));
          else if (data.mode === "console") {
            note("flat", "No mail provider is configured on this server.<br>" +
              `<a href="${esc(data.link)}">Open the sign-in link</a>`);
          } else {
            note("ok", "If that address is valid, a link is on its way. It " +
              "expires in 15 minutes and works once.");
          }
        } else if (mode === "signup") {
          const { res, data } = await post("/api/auth/signup", { email, password });
          if (!res.ok) note("bad", esc(data.detail || "Could not create the account."));
          else if (data.signed_in) afterAuthChange();
          // Address already in use. The server does not say so, and neither
          // do we — the honest message is the one it returned.
          else note("ok", esc(data.note));
        } else {
          const { res, data } = await post("/api/auth/login", { email, password });
          if (!res.ok) note("bad", esc(data.detail || "Email or password is incorrect."));
          else afterAuthChange();
        }
      } catch (e) {
        note("bad", "Could not reach the server.");
      }

      go.disabled = false;
      go.textContent = label;
    });
  }

  function wireSignedIn(me) {
    wirePanel();

    el("setpw").addEventListener("click", () => {
      const f = el("pwForm");
      f.hidden = !f.hidden;
    });

    el("savepw").addEventListener("click", async () => {
      const password = el("np").value;
      const { res, data } = await post("/api/auth/set-password", { password });
      if (!res.ok) note("bad", esc(data.detail || "Could not save."));
      else note("ok", esc(data.note));
    });

    el("out").addEventListener("click", async () => {
      await post("/api/auth/logout", {});
      location.reload();
    });

    el("outAll").addEventListener("click", async () => {
      const { res, data } = await post("/api/auth/logout-everywhere", {});
      if (!res.ok) note("bad", esc(data.detail || "Could not sign out."));
      else location.reload();
    });
  }

  /* ---------- boot ---------- */

  (async function boot() {
    try {
      const [me, prov] = await Promise.all([
        fetch("/api/auth/me").then((r) => r.json()),
        fetch("/api/auth/providers").then((r) => r.json()),
      ]);
      providers = (prov && prov.oauth) || [];
      if (me.signed_in) signedIn(me); else signedOut();
    } catch (e) {
      // Server unreachable. Render the signed-out box rather than nothing;
      // a missing header is more confusing than one that fails on submit.
      signedOut();
    }
  })();
})();
