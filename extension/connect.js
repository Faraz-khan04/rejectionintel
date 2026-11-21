/* RejectionIntel — sign-in handshake.
 *
 * WHY A CONTENT SCRIPT MINTS THE TOKEN
 * ------------------------------------
 * /api/auth/token is session-authenticated. A fetch from the popup or the
 * service worker is cross-origin and carries no cookie, so it would 401 even
 * for a signed-in user. This script runs INSIDE the app page, so the same
 * request is same-origin and the session cookie rides along automatically.
 * The user never sees, copies or pastes a token.
 *
 * IT DOES NOT RUN ON EVERY PAGE LOAD
 * ----------------------------------
 * Minting a credential silently, every time someone opens their own report,
 * is not something a user asked for. The handshake fires only when the
 * extension explicitly asked for it: either the tab was opened with
 * ?ri_connect=1, or a previous attempt found the user signed out and left a
 * pending flag behind. The pending flag is what makes "Connect -> sign in ->
 * connected" work as one motion: the token is minted on the redirect back
 * from the magic link, without a second click.
 */

(async () => {
  const params = new URLSearchParams(location.search);
  const asked = params.get("ri_connect") === "1";

  let pending = false;
  if (!asked) {
    try {
      const c = await chrome.storage.local.get("ri_connect_pending");
      pending = c.ri_connect_pending === true;
    } catch (e) {
      return; // extension context gone; nothing sensible to do
    }
  }
  if (!asked && !pending) return;

  let result;
  try {
    const res = await fetch("/api/auth/token", {
      method: "POST",
      credentials: "include",
    });

    if (res.status === 401) {
      // Signed out, or a guest session. Both are a step the user has to take,
      // not an error — the worker leaves this tab open so they can take it.
      result = { ok: false, reason: "signed_out" };
    } else if (!res.ok) {
      result = { ok: false, reason: "server", status: res.status };
    } else {
      const data = await res.json();
      result = data && data.token
        ? { ok: true, token: data.token, account: data.account || "" }
        : { ok: false, reason: "server" };
    }
  } catch (e) {
    result = { ok: false, reason: "unreachable" };
  }

  try {
    chrome.runtime.sendMessage({ type: "handshake_result", result });
  } catch (e) {
    /* worker asleep; the pending flag survives and the next load retries */
  }
})();
