#!/usr/bin/env python3
"""Trace one Solana token's top holders back to who funded them, as the Sift Insider Radar does.

Usage: python3 trace.py <token mint or pool address> [result.json]

Python 3.11+, standard library only. Uses the public Solana node unless you set SOLANA_RPC_URL
(any RPC endpoint) or HELIUS_API_KEY (a free Helius key is much faster). Prints the same report the
radar page shows; with a second argument, also writes the full result as JSON.
"""

import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "scripts"))

import trace_token  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    os.environ["TRACE_MINT"] = argv[1].strip()
    known = HERE.parent / "radar-known.json"  # shared-infrastructure wallets the site publishes
    if known.is_file() and "KNOWN_PUBLIC_FILE" not in os.environ:
        os.environ["KNOWN_PUBLIC_FILE"] = str(known)
    with tempfile.TemporaryDirectory() as tmp:
        out_json = pathlib.Path(argv[2]) if len(argv) > 2 else pathlib.Path(tmp) / "result.json"
        out_md = pathlib.Path(tmp) / "report.md"
        code = trace_token.main(["trace_token", str(out_json), str(out_md)])
        if out_md.is_file():
            print(out_md.read_text())
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
