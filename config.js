// Sift referral configuration. This is the only file to edit to get paid.
//
// Each platform pays a share of the fees on volume that arrives through your
// referral code (typically 10-30% of the platform's ~1% take, for as long as
// the referred wallet keeps trading). Leave a code empty and the button still
// works, it just links without a referral.
//
// Verify each URL format on the platform's own referral page when you create
// the account: formats change, and a wrong template silently earns nothing.
//   GMGN     https://gmgn.ai            -> Referral       (code goes before the mint)
//   Axiom    https://axiom.trade        -> Rewards        (handle for /@handle links)
//   Photon   https://photon-sol.tinyastro.io -> Referral  (handle)
//   Jupiter  https://referral.jup.ag    -> referral account public key
//   Trojan   https://t.me/solana_trojanbot -> /referral   (code)
window.SIFT_CONFIG = {
  referrals: {
    gmgn: "",
    axiom: "",
    photon: "",
    jupiter: "",
    trojan: "",
  },
  // Token-page links. `p` is a pool row from data.json (address = pool, mint = token).
  links: {
    gmgn: (p, ref) => p.mint ? `https://gmgn.ai/sol/token/${ref ? ref + "_" : ""}${p.mint}` : null,
    axiom: (p, ref) => p.mint ? `https://axiom.trade/t/${p.mint}${ref ? "?ref=" + encodeURIComponent(ref) : ""}` : null,
    photon: (p, ref) => `https://photon-sol.tinyastro.io/en/lp/${p.address}${ref ? "?handle=" + encodeURIComponent(ref) : ""}`,
    jupiter: (p, ref) => p.mint ? `https://jup.ag/swap/SOL-${p.mint}${ref ? "?referrer=" + encodeURIComponent(ref) : ""}` : null,
    trojan: (p, ref) => p.mint ? `https://t.me/solana_trojanbot?start=${ref ? "r-" + ref + "-" : ""}${p.mint}` : null,
    gecko: (p) => `https://www.geckoterminal.com/solana/pools/${p.address}`,
  },
  // Sign-up links for the "trade with" strip.
  signup: {
    gmgn: (ref) => ref ? `https://gmgn.ai/?ref=${encodeURIComponent(ref)}` : "https://gmgn.ai",
    axiom: (ref) => ref ? `https://axiom.trade/@${encodeURIComponent(ref)}` : "https://axiom.trade",
    photon: (ref) => ref ? `https://photon-sol.tinyastro.io/@${encodeURIComponent(ref)}` : "https://photon-sol.tinyastro.io",
    trojan: (ref) => ref ? `https://t.me/solana_trojanbot?start=r-${encodeURIComponent(ref)}` : "https://t.me/solana_trojanbot",
  },
  // Which buttons to show per row, in order. Remove one to hide it everywhere.
  buttons: ["gmgn", "axiom", "photon", "jupiter"],
};
