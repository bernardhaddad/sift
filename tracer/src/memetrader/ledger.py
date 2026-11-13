"""The Insider Radar's track record: does a grade predict what the token's price does next?

Rules, fixed before the first entry (2026-10-14) and printed on the page:
- A token enters the ledger the first hour the radar publishes it with a current-method trace and
  GeckoTerminal has a USD price for it. Its grade is frozen at that moment; later re-grades never
  rewrite the record.
- Every hour for 7 days the ledger asks for the price. It keeps the lowest price seen within the
  7 days, the price 24 to 30 hours after entry and the price 7 days to 7 days 6 hours after entry,
  each with the hours actually elapsed. A sample outside its window is not taken.
- Collapsed = the lowest price within 7 days was at most 20% of the entry price (-80%), or the price
  service answered "no price" for this token continuously for its last 24 hours (it stopped
  trading). For the 7-day median a vanished token counts as -100%.
- Void = the price service could not be reached for more than 6 hours in a row while the entry was
  open; the hourly samples could have missed a crash, so the entry is not scored at all. A pause
  in our own pipeline is never read as a collapse.
- A grade's scorecard is shown once 10 of its scored entries have closed.

Amendments of 2026-10-26 (defects, found by review; the rules above are unchanged):
- "Vanished" now counts only the hours the price service answered "no price". Before, hours it could
  not be reached also counted, and an entry whose window closed during an outage could be scored as
  vanished (-100%) instead of void. Entries closed before the fix keep their recorded outcome.
- An entry that closes without a 7-day price and is neither void nor vanished (its price was still
  answered within its last 24 hours) counts in the collapse rate by its lowest price, and is left
  out of the 7-day median, which needs a 7-day price. This was already how scores were computed.
- A batch the price service answered with an empty list is no longer an answer: a degraded service
  returns that for whole batches, and 24 hours of it scored every open entry as vanished. Those
  hours now count as "could not ask", like an unreachable service. A price that is not a finite
  positive number (NaN, infinity) is no price, and a malformed row is skipped instead of stopping
  the hour's update.

Added 2026-10-15, outside the rules above and changing no score: the first hourly sample at or
below 50% and at or below 20% of the entry price (dd50, dd80), with the hours since entry. Entries
that were already below a line when this started record it with an unknown time, never a guess.

Added 2026-11-07, outside the rules above and changing no score: exit warnings. For a High or Watch
entry inside its 7 days, the first hourly reading of the linked wallets' holdings that is under half
of the first reading fires one warning, but only when the reading before it was at or above half,
so the drop is seen between two consecutive readings and an entry that was already below half when
this started never gets a late warning. The warning keeps the hour, the holdings, the price that
hour and the lowest price seen after it. Holdings count only linked wallets still among the 20
largest accounts, so a wallet that left them may still hold a little. Its own track record
(EXIT_WARNINGS) counts entries that entered on or after `exit_since`, set on the first run of this
code and never moved.

What it can and cannot show: grades are not assigned at random. The scanner watches established
pools, requests and the trending feed skew to new launches at peak attention, so a difference
between grades is evidence, not proof. Prices are sampled hourly, so a crash and recovery inside
one hour is not seen.
"""

import math
import statistics
from datetime import datetime, timedelta

from memetrader import filters

HOURS_24 = timedelta(hours=24)
WINDOW = timedelta(hours=6)
DAYS_7 = timedelta(days=7)
MAX_GAP = timedelta(hours=6)
COLLAPSE = 0.2  # lowest price <= 20% of entry
MIN_CLOSED = 10
DRAWDOWNS = (("dd50", 0.5), ("dd80", 0.2))  # first hourly sample at or below this share of entry
GRADES = ("high", "watch", "low", "incomplete")


def _at(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _price(v) -> float | None:
    try:
        p = float(v)
    except (TypeError, ValueError):
        return None
    return p if math.isfinite(p) and p > 0 else None


def _hours(td: timedelta) -> float:
    return round(td.total_seconds() / 3600, 1)


def update(ledger: dict | None, tokens: list, prices: dict, now: datetime, answered=None) -> dict:
    """One hourly step. `tokens` are the radar's current rows; `prices` maps mint -> USD price;
    `answered` is the set of mints the price service actually answered for this hour (priced or
    not). Mints outside it are treated as "could not ask", never as "no price"."""
    answered = set(prices) if answered is None else set(answered)
    entries = {m: dict(e) for m, e in ((ledger or {}).get("entries") or {}).items()}
    stamp = now.isoformat(timespec="seconds")
    for t in tokens:
        mint = t.get("mint")
        p = _price(prices.get(mint))
        if not mint or mint in entries or p is None or t.get("grade") not in GRADES:
            continue
        if mint in filters.NON_MEME_MINTS:
            continue
        entries[mint] = {
            "mint": mint,
            "name": t.get("name"),
            "grade": t["grade"],
            "source": t.get("source"),
            "entered_at": stamp,
            "p0": p,
            "min_price": p,
            "last_price": p,
            "last_at": stamp,
            "asked_at": stamp,
            "unpriced_since": None,
            "p24": None,
            "p7d": None,
            "dd50": None,
            "dd80": None,
            "dd_since": stamp,
            "void": False,
            "vanished": False,
            "closed": False,
        }
    for mint in filters.NON_MEME_MINTS & set(entries):  # outside the radar's scope (filters.py)
        entries[mint]["out_of_scope"] = True
    held = {t.get("mint"): t["retained"] for t in tokens if t.get("retained")}
    ages = {t.get("mint"): t["trading_since"] for t in tokens if t.get("trading_since")}
    for mint, e in entries.items():
        start = _at(e.get("entered_at"))
        if e.get("closed") or start is None:
            continue
        age = now - start
        since = ages.get(mint)
        if since and not e.get("trading_since"):  # token age (radar trading_since): passive
            e["trading_since"] = since
        r = held.get(mint)
        crossed = False
        if r and age <= DAYS_7:  # linked supply still held (radar.retained): passive, never scored
            reading = {"at": stamp, "pct": r.get("held_pct"), "out_of_top": r.get("out_of_top"),
                       "below_pct": r.get("below_pct")}
            prev = e.get("held_last") or {}
            e.setdefault("held_first", reading)
            e["held_last"] = reading
            first_pct = e["held_first"].get("pct")
            crossed = bool(  # exit warning: the drop under half is seen between two readings
                e.get("grade") in ("high", "watch") and first_pct and reading["pct"] is not None
                and prev.get("pct") is not None and prev["pct"] >= SOLD_SHARE * first_pct
                and reading["pct"] < SOLD_SHARE * first_pct
            )
        if "dd_since" not in e:  # entered before drawdown times were recorded
            e["dd_since"] = stamp
            for key, line in DRAWDOWNS:
                below = e.get("p0") and (e.get("min_price") or e["p0"]) <= line * e["p0"]
                e[key] = {"at": None, "hours": None, "before": stamp} if below else None
        if mint in answered:
            asked = _at(e.get("asked_at")) or start
            if now - asked > MAX_GAP:
                e["void"] = True  # samples were missing for over 6 hours: not scored
            e["asked_at"] = stamp
            p = _price(prices.get(mint))
            if p is None:
                e["unpriced_since"] = e.get("unpriced_since") or stamp
            else:
                e["unpriced_since"] = None
                e["last_price"], e["last_at"] = p, stamp
                if age <= DAYS_7:
                    e["min_price"] = min(e.get("min_price") or p, p)
                    for key, line in DRAWDOWNS:
                        if e.get(key) is None and p <= line * e["p0"]:
                            e[key] = {"at": stamp, "hours": _hours(age)}
                if HOURS_24 <= age < HOURS_24 + WINDOW and e.get("p24") is None:
                    e["p24"] = {"at": stamp, "price": p, "hours": _hours(age)}
                if DAYS_7 <= age < DAYS_7 + WINDOW and e.get("p7d") is None:
                    e["p7d"] = {"at": stamp, "price": p, "hours": _hours(age)}
        fresh = e.get("last_at") == stamp  # a price was answered this hour
        w = e.get("exit_warning")
        if crossed and w is None:
            last = e["held_last"]
            e["exit_warning"] = {"at": stamp, "hours": _hours(age), "held_pct": last["pct"],
                                 "first_pct": e["held_first"]["pct"], "out_of_top": last.get("out_of_top"),
                                 "price": e.get("last_price"), "price_at": e.get("last_at"),
                                 "low": e.get("last_price") if fresh else None}
        elif w is not None and fresh and age <= DAYS_7:  # lowest price seen after the warning
            low = w.get("low")
            e["exit_warning"] = {**w, "low": e["last_price"] if low is None else min(low, e["last_price"])}
        w5 = e.get("exit_warning_5m")  # the 5-minute clock's warning (exitwatch.merge_into_ledger)
        if w5 is not None and fresh and age <= DAYS_7 and stamp > str(w5.get("at") or ""):
            low = w5.get("low")
            low = e["last_price"] if low is None else min(low, e["last_price"])
            e["exit_warning_5m"] = {**w5, "low": low}
        if e.get("p7d") is not None or age >= DAYS_7 + WINDOW:
            e["closed"] = True
            e["vanished"] = False
            if e.get("p7d") is None:
                asked = _at(e.get("asked_at")) or start
                since = _at(e.get("unpriced_since"))
                if now - asked > MAX_GAP:
                    e["void"] = True  # the window closed while the service was unreachable: void wins
                elif since is not None and asked - since >= HOURS_24:
                    # the service answered "no price" for the last 24 hours it was asked: hours it
                    # could not be reached are not "no price" (amendment of 2026-10-26)
                    e["vanished"] = True
    since = (ledger or {}).get("rules_since") or stamp
    exit_since = (ledger or {}).get("exit_since") or stamp  # set once, on this code's first run
    out = {"rules_since": since, "exit_since": exit_since, "updated_at": stamp, "entries": entries}
    if (ledger or {}).get("exit5_since"):  # set once by exitwatch.merge_into_ledger, never moved
        out["exit5_since"] = ledger["exit5_since"]
    return out


def _ret(e: dict, key: str) -> float | None:
    obs = e.get(key)
    return obs["price"] / e["p0"] - 1.0 if obs and e.get("p0") else None


def scorecard(ledger: dict) -> dict:
    """Per grade, over scored (non-void) entries: entered, closed, collapse rate among closed,
    median change after 24 hours (entries with a 24-hour sample) and after 7 days (closed entries;
    vanished ones count as -100%), and for dd50/dd80 how many closed entries fell that far and the
    median hours it took (entries whose crossing time is unknown are counted, not timed)."""
    out = {}
    entries = [e for e in (ledger or {}).get("entries", {}).values() if not e.get("out_of_scope")]
    for g in GRADES:
        es = [e for e in entries if e.get("grade") == g]
        scored = [e for e in es if not e.get("void")]
        closed = [e for e in scored if e.get("closed")]
        r24 = [r for r in (_ret(e, "p24") for e in scored) if r is not None]
        r7 = [-1.0 if e.get("vanished") else _ret(e, "p7d") for e in closed]
        r7 = [r for r in r7 if r is not None]
        collapsed = sum(1 for e in closed if e.get("vanished") or e.get("min_price", 1) <= COLLAPSE * e["p0"])
        out[g] = {
            "entries": len(es),
            "void": len(es) - len(scored),
            "closed": len(closed),
            "ready": len(closed) >= MIN_CLOSED,
            "median_24h": round(statistics.median(r24), 4) if r24 else None,
            "n_24h": len(r24),
            "median_7d": round(statistics.median(r7), 4) if r7 else None,
            "n_7d": len(r7),
            "collapsed": collapsed,
            "collapse_rate": round(collapsed / len(closed), 4) if closed else None,
        }
        for key, _line in DRAWDOWNS:  # closed entries that fell that far, and how fast
            hit = [e[key] for e in closed if e.get(key)]
            hours = [d["hours"] for d in hit if d.get("hours") is not None]
            out[g][key] = {
                "reached": len(hit),
                "time_unknown": len(hit) - len(hours),
                "median_hours": statistics.median(hours) if hours else None,
            }
    return out


YOUNG_DAYS = 30
SOLD_SHARE = 0.5
COMPARISONS = {  # fixed on 2026-10-19, before the first entry closed; published in radar.json
    "fixed_at": "2026-10-19",
    "scope": "High risk and Watch entries, closed and not void",
    "outcome": (
        "a collapse is a lowest price within 7 days at or below 20% of the entry price, "
        "or a token that vanished"
    ),
    "min_each": MIN_CLOSED,
    "age": f"Young: the token had traded for {YOUNG_DAYS} days or less when it entered; older: longer",
    "exit": "Sold: the linked wallets' last reading within 7 days is under half their first; held: otherwise",
    "note": "descriptive, not a prediction: selling and falling prices can happen together",
}


def _collapsed(e: dict) -> bool:
    return bool(e.get("vanished") or e.get("min_price", 1) <= COLLAPSE * e["p0"])


def comparisons(ledger: dict) -> dict:
    """The comparisons fixed in COMPARISONS, over closed, non-void High and Watch entries: for each
    side, how many closed entries it has, how many collapsed and the rate. A comparison is "ready"
    when both sides have at least min_each entries; entries without the reading are left out."""
    es = [e for e in (ledger or {}).get("entries", {}).values()
          if e.get("grade") in ("high", "watch") and e.get("closed") and not e.get("void")
          and not e.get("out_of_scope")]

    def side(group: list) -> dict:
        n, c = len(group), sum(1 for e in group if _collapsed(e))
        return {"closed": n, "collapsed": c, "rate": round(c / n, 4) if n else None}

    def young(e):
        since, at = _at(str(e.get("trading_since") or "").replace("Z", "+00:00")), _at(e.get("entered_at"))
        return None if since is None or at is None else at - since <= timedelta(days=YOUNG_DAYS)

    def sold(e):
        first, last = e.get("held_first") or {}, e.get("held_last") or {}
        if first.get("pct") is None or last.get("pct") is None:
            return None
        return last["pct"] < SOLD_SHARE * first["pct"]

    out = {"definitions": COMPARISONS}
    for key, test, a, b in (("age", young, "young", "older"), ("exit", sold, "sold", "held")):
        tagged = [(test(e), e) for e in es]
        sides = {a: side([e for t, e in tagged if t is True]), b: side([e for t, e in tagged if t is False])}
        out[key] = {**sides, "unknown": sum(1 for t, _ in tagged if t is None),
                    "ready": all(v["closed"] >= MIN_CLOSED for v in sides.values())}
    return out


EXIT_WARNINGS = {  # fixed on 2026-11-07, before the first warning fired; published in radar.json
    "fixed_at": "2026-11-07",
    "rule": (
        "a High risk or Watch entry inside its 7 days whose linked wallets' holdings among the 20 "
        "largest accounts fall under half of the first reading, seen between two hourly readings"
    ),
    "counted": "entries that entered on or after exit_since and are not void",
    "outcomes": (
        "for each closed warned entry: whether its 7-day price is below the price at the warning "
        "(a vanished token is below), the change from the warning price to the 7-day price and to "
        "the lowest price after the warning, whether it collapsed, and whether the warning came "
        "before the first hourly sample at or below 20% of the entry price"
    ),
    "min_closed": MIN_CLOSED,
    "note": (
        "a wallet that left the 20 largest accounts may still hold a little; prices are hourly, "
        "so a warning and a crash inside the same hour look simultaneous"
    ),
}


def exit_warnings(ledger: dict | None) -> dict:
    """The exit warnings' own track record (EXIT_WARNINGS), plus every warning newest first for
    the page. Medians are of changes from the warning price; a vanished token counts as -100%."""
    entries = [e for e in ((ledger or {}).get("entries") or {}).values()
               if e.get("grade") in ("high", "watch") and not e.get("out_of_scope")]
    since = (ledger or {}).get("exit_since")
    counted = [e for e in entries if since and str(e.get("entered_at") or "") >= since and not e.get("void")]
    warned = [e for e in counted if e.get("exit_warning")]
    closed = [e for e in warned if e.get("closed") and (e["exit_warning"].get("price") or 0) > 0]

    def end(e):
        w = e["exit_warning"]
        if e.get("vanished"):
            return -1.0
        p7 = e.get("p7d") or {}
        return p7["price"] / w["price"] - 1.0 if p7.get("price") else None

    ends = [r for r in (end(e) for e in closed) if r is not None]
    lows = [e["exit_warning"]["low"] / e["exit_warning"]["price"] - 1.0
            for e in closed if e["exit_warning"].get("low")]
    collapsed = [e for e in closed if _collapsed(e)]
    first = [e for e in collapsed
             if (e.get("dd80") or {}).get("at") and e["dd80"]["at"] > e["exit_warning"]["at"]]
    quiet = [e for e in counted if e.get("closed") and not e.get("exit_warning")]
    recent = sorted(
        ({"mint": e.get("mint"), "name": e.get("name"), "grade": e.get("grade"), **e["exit_warning"]}
         for e in entries if e.get("exit_warning")),
        key=lambda r: r["at"], reverse=True,
    )
    return {
        "definitions": EXIT_WARNINGS,
        "since": since,
        "fired": len(warned),
        "closed": len(closed),
        "ready": len(closed) >= MIN_CLOSED,
        "ended_lower": sum(1 for r in ends if r < 0),
        "median_7d": round(statistics.median(ends), 4) if ends else None,
        "median_low": round(statistics.median(lows), 4) if lows else None,
        "collapsed": len(collapsed),
        "warned_first": len(first),
        "not_warned": {"closed": len(quiet), "collapsed": sum(1 for e in quiet if _collapsed(e))},
        "recent": recent[:40],
    }


EXIT_WARNINGS_5M = {  # fixed on 2026-11-11 (docs/research-exit-watch-5min.md), before it ran
    "fixed_at": "2026-11-11",
    "rule": "Read the linked wallets every 5 minutes; warn once when their holdings fall under half "
    "of the hourly first reading between two consecutive readings.",
    "counted": "Entries that entered on or after exit5_since and are not void.",
    "compare": "For entries warned on either clock, which clock warned first; for entries warned on "
    "both, the median lead in minutes; for collapses, how many each clock warned before the first "
    "hourly sample at -80%.",
    "min_closed": MIN_CLOSED,
}


def _minutes(a, b) -> float | None:
    a, b = _at(a), _at(b)
    return (b - a).total_seconds() / 60 if a and b else None


def exit_warnings_5m(ledger: dict | None) -> dict:
    """The 5-minute warnings' own record (EXIT_WARNINGS_5M), with the comparison against the
    hourly clock on the same entries. Medians are of changes from the warning price."""
    entries = [e for e in ((ledger or {}).get("entries") or {}).values()
               if e.get("grade") in ("high", "watch") and not e.get("out_of_scope")]
    since = (ledger or {}).get("exit5_since")
    counted = [e for e in entries if since and str(e.get("entered_at") or "") >= since and not e.get("void")]
    warned = [e for e in counted if e.get("exit_warning_5m")]
    closed = [e for e in warned if e.get("closed") and (e["exit_warning_5m"].get("price") or 0) > 0]

    def end(e):
        if e.get("vanished"):
            return -1.0
        p7 = e.get("p7d") or {}
        return p7["price"] / e["exit_warning_5m"]["price"] - 1.0 if p7.get("price") else None

    ends = [r for r in (end(e) for e in closed) if r is not None]
    lows = [e["exit_warning_5m"]["low"] / e["exit_warning_5m"]["price"] - 1.0
            for e in closed if e["exit_warning_5m"].get("low")]
    either = [e for e in counted if e.get("exit_warning_5m") or e.get("exit_warning")]
    first_5m = first_1h = 0
    leads = []
    for e in either:
        a5 = (e.get("exit_warning_5m") or {}).get("at")
        a1 = (e.get("exit_warning") or {}).get("at")
        if a5 and (not a1 or a5 < a1):
            first_5m += 1
        else:
            first_1h += 1
        if a5 and a1:
            leads.append(_minutes(a5, a1))
    gone = [e for e in counted if e.get("closed") and _collapsed(e) and (e.get("dd80") or {}).get("at")]

    def before(e, key):
        return bool((e.get(key) or {}).get("at") and e[key]["at"] < e["dd80"]["at"])

    recent = sorted(
        ({"mint": e.get("mint"), "name": e.get("name"), "grade": e.get("grade"), **e["exit_warning_5m"]}
         for e in entries if e.get("exit_warning_5m")),
        key=lambda r: r["at"], reverse=True,
    )
    return {
        "definitions": EXIT_WARNINGS_5M,
        "since": since,
        "fired": len(warned),
        "closed": len(closed),
        "ready": len(closed) >= MIN_CLOSED,
        "ended_lower": sum(1 for r in ends if r < 0),
        "median_7d": round(statistics.median(ends), 4) if ends else None,
        "median_low": round(statistics.median(lows), 4) if lows else None,
        "collapsed": sum(1 for e in closed if _collapsed(e)),
        "first": {"5m": first_5m, "1h": first_1h},
        "median_lead_min": round(statistics.median(leads), 1) if leads else None,
        "only": {"5m": sum(1 for e in either if not e.get("exit_warning")),
                 "1h": sum(1 for e in either if not e.get("exit_warning_5m"))},
        "collapses_warned_before": {
            "collapses": len(gone),
            "5m": sum(before(e, "exit_warning_5m") for e in gone),
            "1h": sum(before(e, "exit_warning") for e in gone),
        },
        "recent": recent[:40],
    }


def open_mints(ledger: dict | None) -> list:
    """Open entries, the longest unanswered first: they are nearest the 6-hour void line, so a
    rate-limited hour must still reach them."""
    open_ = [(e.get("asked_at") or "", m) for m, e in ((ledger or {}).get("entries") or {}).items()
             if not e.get("closed")]
    return [m for _, m in sorted(open_)]


# Added 2026-11-13, outside the rules above and changing no score: base rates by token age, so a
# grade is read next to how often tokens of the same age collapsed whatever their grade.
AGE_BANDS = ("under_1d", "1_7d", "7_30d", "over_30d")  # under 1 day, 1 to 7, 7 to 30 (incl.), over 30
AGE_BASE_RATES = (
    "Closed entries of every grade that are in the radar's scope and not void, grouped by how long the "
    "token had traded when it entered (under 1 day, 1 to under 7 days, 7 to 30 days, over 30 days), "
    "where a collapse is a lowest price within 7 days at or below 20% of the entry price or a token "
    "that vanished."
)


def _utc_naive(at: datetime) -> datetime:
    """The same instant without a zone; a stamp written without one is already UTC."""
    return at.replace(tzinfo=None) - (at.utcoffset() or timedelta(0))


def age_at_entry(entry: dict) -> timedelta | None:
    """How long the token had traded when it entered the ledger: entered_at minus trading_since
    (the creation time of its oldest pool listed, as the radar row carried it). None when either
    is missing or unreadable."""
    since = _at(str(entry.get("trading_since") or "").replace("Z", "+00:00"))
    at = _at(str(entry.get("entered_at") or "").replace("Z", "+00:00"))
    return None if since is None or at is None else _utc_naive(at) - _utc_naive(since)


def age_band(entry: dict) -> str | None:
    """The entry's AGE_BANDS band: under 1 day (a pool listed only after the entry included), 1 to
    under 7 days, 7 to 30 days (30 included, like the young side of COMPARISONS), over 30 days. None
    when the token age is unknown."""
    age = age_at_entry(entry)
    if age is None:
        return None
    if age < timedelta(days=1):
        return "under_1d"
    if age < timedelta(days=7):
        return "1_7d"
    return "7_30d" if age <= timedelta(days=YOUNG_DAYS) else "over_30d"


def age_base_rates(book: dict | None) -> dict:
    """AGE_BASE_RATES over the scorecard's own scope (closed entries of every grade, not void and
    not out of scope): per band, how many closed and how many collapsed, for all grades together
    and per grade. Entries without a token age are left out of the bands and counted as unknown."""

    def blank() -> dict:
        return {b: {"closed": 0, "collapsed": 0} for b in AGE_BANDS}

    bands, by_grade, unknown = blank(), {g: blank() for g in GRADES}, 0
    for e in ((book or {}).get("entries") or {}).values():
        if not isinstance(e, dict) or e.get("grade") not in GRADES or e.get("out_of_scope"):
            continue
        if e.get("void") or not e.get("closed"):
            continue
        band = age_band(e)
        if band is None:
            unknown += 1
            continue
        hit = int(_collapsed(e))
        for cell in (bands[band], by_grade[e["grade"]][band]):
            cell["closed"] += 1
            cell["collapsed"] += hit
    return {"definition": AGE_BASE_RATES, "bands": bands, "by_grade": by_grade, "unknown": unknown}
