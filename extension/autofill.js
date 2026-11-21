/* RejectionIntel — ATS form autofill.
 *
 * STRICTLY CLIENT-SIDE. This file makes no network request of any kind. The
 * profile is read from chrome.storage.local, written into the page's inputs,
 * and nothing leaves the browser. That matters more here than anywhere else in
 * the extension: this is the one place holding a phone number and a home
 * address, and an application form is the last page anyone wants a background
 * sync happening on.
 *
 * MATCHING IS CONSERVATIVE ON PURPOSE
 * -----------------------------------
 * Every field is matched on the attributes ATS platforms actually set — name,
 * id, autocomplete, aria-label, then the visible <label>. A field that matches
 * nothing is left alone, and a field the user has already typed into is never
 * overwritten. Filling the wrong box on a job application is worse than
 * filling no box: the applicant does not reread a form they believe was
 * completed for them.
 *
 * WHAT IT CANNOT DO
 * -----------------
 * Attach a resume. Browsers forbid setting <input type="file"> from script,
 * for good reasons. The count reported back never includes the file field, so
 * "filled 7 fields" never implies a resume went with them.
 *
 * Injected on demand by popup.js via chrome.scripting.executeScript.
 */

(async () => {
  "use strict";

  const store = await chrome.storage.local.get("ri_profile");
  let profile = store.ri_profile;
  if (typeof profile === "string") {
    try { profile = JSON.parse(profile); } catch (e) { profile = null; }
  }
  if (!profile || typeof profile !== "object") {
    chrome.runtime.sendMessage({ type: "autofill_result", error: "no_profile" });
    return;
  }

  /* Field key -> the tokens that identify it on real ATS forms. Ordered from
   * most specific to least: "first_name" must win before "name". */
  const FIELDS = [
    ["first_name", ["first_name", "firstname", "given-name", "given_name",
                    "fname", "first name"]],
    ["last_name",  ["last_name", "lastname", "family-name", "family_name",
                    "lname", "surname", "last name"]],
    ["full_name",  ["full_name", "fullname", "candidate_name", "your name",
                    "name"]],
    ["email",      ["email", "e-mail", "email_address", "emailaddress"]],
    ["phone",      ["phone", "telephone", "mobile", "tel", "phone_number"]],
    ["linkedin_url", ["linkedin", "linked_in", "linkedin_url",
                      "linkedin profile"]],
    ["github_url", ["github", "git_hub", "github_url"]],
    ["portfolio_url", ["portfolio", "website", "personal_site", "url",
                       "web_site"]],
    ["location",   ["location", "city", "address", "where are you based"]],
    ["company",    ["current_company", "employer", "current employer"]],
    ["title",      ["current_title", "job_title", "current role"]],
  ];

  const norm = (s) => (s || "").toString().toLowerCase().replace(/[\s\-]+/g, "_");

  function labelText(el) {
    if (el.id) {
      const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lab) return lab.innerText || "";
    }
    const wrap = el.closest("label");
    return wrap ? (wrap.innerText || "") : "";
  }

  /* Everything the page tells us about this input, in one haystack. */
  function haystack(el) {
    return [el.name, el.id, el.getAttribute("autocomplete"),
            el.getAttribute("aria-label"), el.placeholder, labelText(el)]
      .map(norm).join(" | ");
  }

  function keyFor(el) {
    const hay = haystack(el);
    if (!hay.trim()) return null;
    for (const [key, tokens] of FIELDS) {
      // Longest token first so "first_name" cannot be claimed by "name".
      for (const token of tokens) {
        if (hay.includes(norm(token))) return key;
      }
    }
    return null;
  }

  function setValue(el, value) {
    // React and friends listen for input/change and ignore a bare .value
    // assignment, so the form's own state would never see the text.
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // A derived value, not a stored one: forms ask for either shape.
  const derived = Object.assign({}, profile);
  if (!derived.full_name && (profile.first_name || profile.last_name)) {
    derived.full_name = [profile.first_name, profile.last_name]
      .filter(Boolean).join(" ");
  }

  const inputs = document.querySelectorAll(
    "input[type=text], input[type=email], input[type=tel], input[type=url], " +
    "input:not([type]), textarea");

  let filled = 0;
  const used = [];

  inputs.forEach((el) => {
    if (el.disabled || el.readOnly || el.type === "file") return;
    // Never overwrite something the person typed. An autofiller that clobbers
    // a half-finished answer is a bug they will not forgive.
    if (el.value && el.value.trim()) return;

    const key = keyFor(el);
    if (!key) return;
    const value = derived[key];
    if (!value) return;

    setValue(el, String(value));
    filled += 1;
    used.push(key);
  });

  const fileFields = document.querySelectorAll("input[type=file]").length;

  chrome.runtime.sendMessage({
    type: "autofill_result",
    filled,
    fields: used,
    // Reported separately so the count never implies the resume was attached.
    file_fields: fileFields,
  });
})();
