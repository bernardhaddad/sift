// Sift referral configuration. This is the only file to edit to get paid.
//
// Each platform pays a share of the fees on volume that arrives through your
// referral code (typically 10-30% of the platform's ~1% take, for as long as
// the referred wallet keeps trading). Leave a code empty and the button still
// works, it just links without a referral.
//
// Formats verified against each platform's documentation on 2026-09-05.
// Re-check them when you create the accounts: formats change, and a wrong
// template silently earns nothing.
//   GMGN    docs.gmgn.ai/index/referral-link. Code = the `ref` value shown in the
//           address bar after "Connect Telegram" on gmgn.ai, or from @gmgnaibot.
//           Token pages carry it directly: gmgn.ai/sol/token/<code>_<mint>.
//           Pays a tiered share (up to ~30%) of fees on referred volume.
//   Axiom   docs.axiom.trade/getting-started/referral-program. Your link is
//           axiom.trade/@<handle>; attribution happens at SIGN-UP only, so the
//           handle matters on the sign-up link, not on token pages. 30% of net
//           fee on direct referrals, 3% / 2% on the next two levels.
//   Photon  photon-sol.tinyastro.io/en/referrals after signing up. Link is
//           photon-sol.tinyastro.io/@<handle>; attribution at sign-up.
//   Jupiter DIFFERENT MODEL: referral.jup.ag creates a referral account (a
//           public key, small SOL rent) and the link ADDS a fee to the visitor's
//           swap (feeBps, e.g. 50 = 0.5%) paid to you in the output token. That
//           is a charge on the visitor, not a share of an existing fee, so it is
//           off unless jupiterFeeBps > 0, and the page discloses it when on.
//   Trojan  /referral inside t.me/solana_trojanbot gives a code; share of bot fees.
window.SIFT_CONFIG = {
  referrals: {
    gmgn: "",
    axiom: "",
    photon: "",
    jupiter: "",   // referral account public key from referral.jup.ag
    trojan: "",
  },
  jupiterFeeBps: 0,  // 0 = plain Jupiter links. >0 adds that fee to visitor swaps and is disclosed.
  // Token-page links. `p` is a pool row from data.json (address = pool, mint = token).
  links: {
    gmgn: (p, ref) => p.mint ? `https://gmgn.ai/sol/token/${ref ? ref + "_" : ""}${p.mint}` : null,
    axiom: (p) => p.mint ? `https://axiom.trade/t/${p.mint}` : null,
    photon: (p) => `https://photon-sol.tinyastro.io/en/lp/${p.address}`,
    jupiter: (p, ref, cfg) => {
      if (!p.mint) return null;
      const fee = cfg && cfg.jupiterFeeBps > 0 && ref ? `?referrer=${encodeURIComponent(ref)}&feeBps=${cfg.jupiterFeeBps}` : "";
      return `https://jup.ag/swap/SOL-${p.mint}${fee}`;
    },
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
