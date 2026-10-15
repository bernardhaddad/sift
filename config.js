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
