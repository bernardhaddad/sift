// referral codes and link templates; an empty code gives a plain link
window.SIFT_CONFIG = {
  referrals: {
    gmgn: "",
    axiom: "",
    photon: "",
    jupiter: "",   // referral account public key from referral.jup.ag
    trojan: "",
  },
  jupiterFeeBps: 0,  // >0 adds a fee to Jupiter swaps (shown on the page)
  // token-page links; p is a pool row from data.json
  links: {
    gmgn: (p, ref) => p.mint ? `https://gmgn.ai/sol/token/${ref ? ref + "_" : ""}${p.mint}` : null,
    // Axiom pages are per pool, with the chain named (format copied from axiom.trade on 30 Oct 2026)
    axiom: (p) => p.address ? `https://axiom.trade/meme/${p.address}?chain=sol` : null,
    photon: (p) => `https://photon-sol.tinyastro.io/en/lp/${p.address}`,
    jupiter: (p, ref, cfg) => {
      const sol = "So11111111111111111111111111111111111111112";
      if (!p.mint || p.mint === sol) return null;
      const fee = cfg && cfg.jupiterFeeBps > 0 && ref ? `&referrer=${encodeURIComponent(ref)}&feeBps=${cfg.jupiterFeeBps}` : "";
      // jup.ag/swap/SOL-<mint> now drops the token and opens SOL to USDC; sell/buy keeps it
      return `https://jup.ag/swap?sell=${sol}&buy=${p.mint}${fee}`;
    },
    trojan: (p, ref) => p.mint ? `https://t.me/solana_trojanbot?start=${ref ? "r-" + ref + "-" : ""}${p.mint}` : null,
    gecko: (p) => `https://www.geckoterminal.com/solana/pools/${p.address}`,
  },
  // sign-up links
  signup: {
    gmgn: (ref) => ref ? `https://gmgn.ai/?ref=${encodeURIComponent(ref)}` : "https://gmgn.ai",
    axiom: (ref) => ref ? `https://axiom.trade/@${encodeURIComponent(ref)}` : "https://axiom.trade",
    photon: (ref) => ref ? `https://photon-sol.tinyastro.io/@${encodeURIComponent(ref)}` : "https://photon-sol.tinyastro.io",
    trojan: (ref) => ref ? `https://t.me/solana_trojanbot?start=r-${encodeURIComponent(ref)}` : "https://t.me/solana_trojanbot",
  },
  // buttons shown per row, in order
  buttons: ["gmgn", "axiom", "photon", "jupiter"],
};
