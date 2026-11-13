// Which trade links on the screener carry a referral code. The disclosure under the table names a
// platform only when its link actually changes with the code set, and the page gives a link its code
// only when the disclosure names that platform. Pure functions: nothing here reads or changes the page.
// The page works without this file: then no link carries a code and the page says Sift takes no cut.
(function (root) {
  "use strict";

  // a pool row with every field a link template reads: a pool address and a token mint that is not SOL
  const SAMPLE_ROW = Object.freeze({
    address: "Samp1ePoo1Address111111111111111111111111111",
    mint: "Samp1eTokenMint1111111111111111111111111111",
  });

  // the link a template gives for a row, or null when it gives none or fails
  function linkFor(template, row, code, cfg) {
    try {
      const url = template(row, code, cfg);
      return typeof url === "string" && url ? url : null;
    } catch (err) {
      return null;
    }
  }

  // the platforms, in button order, whose row link changes when their referral code is set
  function codedPlatforms(cfg, sampleRow) {
    const c = cfg && typeof cfg === "object" ? cfg : {};
    const templates = c.links && typeof c.links === "object" ? c.links : {};
    const codes = c.referrals && typeof c.referrals === "object" ? c.referrals : {};
    const row = sampleRow && typeof sampleRow === "object" ? sampleRow : SAMPLE_ROW;
    const out = [];
    for (const k of Array.isArray(c.buttons) ? c.buttons : []) {
      if (typeof k !== "string" || out.includes(k)) continue;
      const template = templates[k];
      const code = codes[k] || ""; // read the way the page reads it
      if (typeof template !== "function" || !code) continue;
      const coded = linkFor(template, row, code, c);
      if (coded !== null && coded !== linkFor(template, row, "", c)) out.push(k);
    }
    return out;
  }

  const api = Object.freeze({ SAMPLE_ROW: SAMPLE_ROW, codedPlatforms: codedPlatforms });
  if (typeof module === "object" && module && module.exports) module.exports = api;
  if (root) root.SIFT_LINKS = api;
})(typeof window !== "undefined" ? window : null);
