"""
IMC Prosperity 2026 — Tutorial Round Backtester
================================================
Usage:
    python backtester.py                     # default params + sweep
    python backtester.py --default-only      # just run default params

Put this file in the same folder as your price/trade CSVs.
Edit DATA_PATHS / TRADE_PATHS at the bottom to match your file names.

Fill mechanics (Prosperity-accurate):
  Aggressive: our bid >= market ask → immediate fill (walk the book)
  Passive:    our bid > market best bid → filled when sell flow crosses us
              our ask < market best ask → filled when buy flow lifts us

Strategy:
  EMERALDS  → Market make around known fair value = 10,000
              Post bid at (10000 - em_edge), ask at (10000 + em_edge)
              Aggressive-lift asks <= 10000 - em_aggr
  TOMATOES  → Step inside the existing market quotes by tom_step ticks
              Inventory skew to stay flat
"""

import csv
import math
import statistics
import sys
from collections import defaultdict


# ─── Data loading ─────────────────────────────────────────────────────────────

def load(paths):
    rows = []
    for path in paths:
        with open(path) as f:
            for r in csv.DictReader(f, delimiter=';'):
                rows.append(r)
    return rows


def build_trade_flow(trade_rows, bid_ask_lookup, all_price_ts):
    """
    For each trade, determine direction (buy/sell) and store by (timestamp, product).
    """
    flow = defaultdict(lambda: defaultdict(list))
    for t in trade_rows:
        ts  = int(t['timestamp'])
        sym = t['symbol']
        px  = float(t['price'])
        qty = float(t['quantity'])
        idx = min(range(len(all_price_ts)), key=lambda i: abs(all_price_ts[i] - ts))
        ba  = bid_ask_lookup.get(all_price_ts[idx], {}).get(sym)
        if ba and ba[0] and ba[1]:
            direction = 'buy' if px >= ba[1] else 'sell'
            flow[ts][sym].append((qty, direction))
    return flow


# ─── Core simulation ──────────────────────────────────────────────────────────

LIMITS = {"EMERALDS": 10, "TOMATOES": 20}


def run(by_ts, timestamps, trade_flow,
        em_edge, em_aggr,
        tom_step, tom_inv_max):
    """
    Parameters
    ----------
    em_edge    : half-spread for EMERALDS quotes around 10,000
    em_aggr    : threshold for aggressive EMERALDS orders
    tom_step   : ticks we improve inside TOMATOES market (1 = best_bid+1 / best_ask-1)
    tom_inv_max: max inventory-skew adjustment in ticks for TOMATOES
    """
    pos  = {"EMERALDS": 0,   "TOMATOES": 0}
    cash = {"EMERALDS": 0.0, "TOMATOES": 0.0}
    pnl_series = []
    em_fills = 0
    tom_fills = 0

    for ts_key in timestamps:
        tick   = by_ts[ts_key]
        ts_int = int(ts_key[1])
        flow   = trade_flow[ts_int]

        # ── EMERALDS ──────────────────────────────────────────────────────────
        if "EMERALDS" in tick:
            row  = tick["EMERALDS"]
            p    = pos["EMERALDS"]
            lim  = LIMITS["EMERALDS"]
            bp1  = float(row['bid_price_1']) if row['bid_price_1'] else None
            ap1  = float(row['ask_price_1']) if row['ask_price_1'] else None
            bv1  = int(row['bid_volume_1'])  if row['bid_volume_1'] else 0
            av1  = int(row['ask_volume_1'])  if row['ask_volume_1'] else 0

            if bp1 and ap1:
                FAIR    = 10_000
                inv_adj = round(p / lim)          # -1, 0, or +1
                our_bid = min(FAIR - em_edge - inv_adj, FAIR - 1)
                our_ask = max(FAIR + em_edge - inv_adj, FAIR + 1)

                # Aggressive fills
                if ap1 <= FAIR - em_aggr and p < lim:
                    q = min(av1, lim - p)
                    if q > 0:
                        cash["EMERALDS"] -= ap1 * q
                        p += q
                        em_fills += 1

                if bp1 >= FAIR + em_aggr and p > -lim:
                    q = min(bv1, lim + p)
                    if q > 0:
                        cash["EMERALDS"] += bp1 * q
                        p -= q
                        em_fills += 1

                # Passive fills
                if our_bid > bp1:
                    buy_cap = lim - p
                    for qty, d in flow.get("EMERALDS", []):
                        if d == 'sell' and buy_cap > 0:
                            filled = min(buy_cap, int(qty))
                            cash["EMERALDS"] -= our_bid * filled
                            p       += filled
                            buy_cap -= filled
                            em_fills += 1

                if our_ask < ap1:
                    sell_cap = lim + p
                    for qty, d in flow.get("EMERALDS", []):
                        if d == 'buy' and sell_cap > 0:
                            filled = min(sell_cap, int(qty))
                            cash["EMERALDS"] += our_ask * filled
                            p        -= filled
                            sell_cap -= filled
                            em_fills += 1

                pos["EMERALDS"] = max(-lim, min(lim, p))

        # ── TOMATOES ──────────────────────────────────────────────────────────
        if "TOMATOES" in tick:
            row  = tick["TOMATOES"]
            mid  = float(row['mid_price'])
            p    = pos["TOMATOES"]
            lim  = LIMITS["TOMATOES"]
            bp1  = float(row['bid_price_1']) if row['bid_price_1'] else None
            ap1  = float(row['ask_price_1']) if row['ask_price_1'] else None

            if bp1 and ap1:
                inv_adj = round((p / lim) * tom_inv_max)

                # Step inside market + skew for inventory
                our_bid = int(bp1) + tom_step - inv_adj
                our_ask = int(ap1) - tom_step - inv_adj

                # Guard: keep quotes valid
                if our_bid >= our_ask:
                    our_bid = int(mid) - 1
                    our_ask = int(mid) + 1
                our_bid = min(our_bid, int(ap1) - 1)
                our_ask = max(our_ask, int(bp1) + 1)

                if our_bid > bp1:
                    buy_cap = lim - p
                    for qty, d in flow.get("TOMATOES", []):
                        if d == 'sell' and buy_cap > 0:
                            filled = min(buy_cap, int(qty))
                            cash["TOMATOES"] -= our_bid * filled
                            p       += filled
                            buy_cap -= filled
                            tom_fills += 1

                if our_ask < ap1:
                    sell_cap = lim + p
                    for qty, d in flow.get("TOMATOES", []):
                        if d == 'buy' and sell_cap > 0:
                            filled = min(sell_cap, int(qty))
                            cash["TOMATOES"] += our_ask * filled
                            p        -= filled
                            sell_cap -= filled
                            tom_fills += 1

                pos["TOMATOES"] = max(-lim, min(lim, p))

        # Mark to market
        total = 0.0
        for prod in ["EMERALDS", "TOMATOES"]:
            if prod in tick:
                total += cash[prod] + pos[prod] * float(tick[prod]['mid_price'])
        pnl_series.append(total)

    final   = pnl_series[-1] if pnl_series else 0.0
    peak    = pnl_series[0]  if pnl_series else 0.0
    max_dd  = 0.0
    for pv in pnl_series:
        peak   = max(peak, pv)
        max_dd = max(max_dd, peak - pv)

    half   = len(pnl_series) // 2
    d1     = pnl_series[half - 1] - pnl_series[0] if half else 0
    d2     = pnl_series[-1]       - pnl_series[half] if half else 0
    sharpe = statistics.mean([d1, d2]) / (statistics.stdev([d1, d2]) + 1e-9) if half else 0

    return {
        "final_pnl":    round(final, 2),
        "max_drawdown": round(max_dd, 2),
        "sharpe":       round(sharpe, 3),
        "em_fills":     em_fills,
        "tom_fills":    tom_fills,
        "pnl_series":   pnl_series,
    }


# ─── Parameter sweep ──────────────────────────────────────────────────────────

def sweep(by_ts, timestamps, trade_flow):
    grid = [
        (ee, ea, ts2, ti)
        for ee  in [2, 3, 4]
        for ea  in [1, 2]      if ea < ee
        for ts2 in [1, 2, 3]
        for ti  in [1, 2, 3]
    ]

    total = len(grid)
    print(f"Running {total} parameter combinations...\n")

    results = []
    for ee, ea, ts2, ti in grid:
        r = run(by_ts, timestamps, trade_flow, ee, ea, ts2, ti)
        results.append((r['final_pnl'], r['max_drawdown'], r['sharpe'],
                        ee, ea, ts2, ti, r['em_fills'], r['tom_fills'],
                        r['pnl_series']))

    results.sort(key=lambda x: x[0], reverse=True)

    hdr = (f"{'Rk':<4} {'EMedge':>7} {'EMaggr':>7} {'TStep':>6} {'TInvMax':>8} "
           f"{'PnL':>10} {'MaxDD':>9} {'Sharpe':>9} {'EMf':>6} {'TOMf':>6}")
    print(hdr)
    print("─" * len(hdr))
    for rank, row in enumerate(results[:20], 1):
        pnl2, dd2, sh2, ee, ea, ts2, ti, ef, tf, _ = row
        print(f"{rank:<4} {ee:>7} {ea:>7} {ts2:>6} {ti:>8} "
              f"{pnl2:>10.1f} {dd2:>9.1f} {sh2:>9.3f} {ef:>6} {tf:>6}")

    return results


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    # ── Edit these paths to match your setup ──────────────────────────────────
    DATA_PATHS = [
        'prices_round_0_day_-1.csv',
        'prices_round_0_day_-2.csv',
    ]
    TRADE_PATHS = [
        'trades_round_0_day_-1.csv',
        'trades_round_0_day_-2.csv',
    ]
    # ─────────────────────────────────────────────────────────────────────────

    print("=" * 70)
    print("  IMC Prosperity 2026 — Backtester (Tutorial Round)")
    print("=" * 70)

    price_rows = load(DATA_PATHS)
    trade_rows = load(TRADE_PATHS)
    print(f"  Loaded {len(price_rows)} price rows, {len(trade_rows)} trade rows\n")

    # Build shared lookup structures
    by_ts = defaultdict(dict)
    for r in price_rows:
        by_ts[(r['day'], r['timestamp'])][r['product']] = r
    timestamps = sorted(by_ts.keys(), key=lambda x: (int(x[0]), int(x[1])))

    bid_ask_lookup = {}
    for r in price_rows:
        ts   = int(r['timestamp'])
        prod = r['product']
        if ts not in bid_ask_lookup:
            bid_ask_lookup[ts] = {}
        b = float(r['bid_price_1']) if r['bid_price_1'] else None
        a = float(r['ask_price_1']) if r['ask_price_1'] else None
        bid_ask_lookup[ts][prod] = (b, a)
    all_price_ts = sorted(bid_ask_lookup.keys())

    trade_flow = build_trade_flow(trade_rows, bid_ask_lookup, all_price_ts)

    # ── Default run ───────────────────────────────────────────────────────────
    print("── Default parameters ───────────────────────────────────────────────")
    r = run(by_ts, timestamps, trade_flow,
            em_edge=3, em_aggr=2, tom_step=1, tom_inv_max=2)
    print(f"  Final PnL      : {r['final_pnl']:>10,.2f}")
    print(f"  Max Drawdown   : {r['max_drawdown']:>10,.2f}")
    print(f"  Sharpe (daily) : {r['sharpe']:>10.3f}")
    print(f"  EMERALDS fills : {r['em_fills']:>10}")
    print(f"  TOMATOES fills : {r['tom_fills']:>10}")

    if '--default-only' in sys.argv:
        sys.exit(0)

    # ── Sweep ─────────────────────────────────────────────────────────────────
    print("\n── Parameter Sweep (top 20) ─────────────────────────────────────────")
    all_results = sweep(by_ts, timestamps, trade_flow)

    best = all_results[0]
    pnl2, dd2, sh2, ee, ea, ts2, ti, ef, tf, best_series = best

    print(f"\n── Best Configuration ───────────────────────────────────────────────")
    print(f"  EMERALDS  : edge={ee}, aggr_edge={ea}")
    print(f"  TOMATOES  : step={ts2}, inv_max={ti}")
    print(f"  PnL       : {pnl2:,.2f}")
    print(f"  MaxDD     : {dd2:,.2f}")
    print(f"  Sharpe    : {sh2:.3f}")

    # Save PnL series for the best run
    with open('pnl_best.csv', 'w') as f:
        f.write("tick,pnl\n")
        for i, pv in enumerate(best_series):
            f.write(f"{i},{pv:.2f}\n")
    print("\n  Best run PnL series saved to pnl_best.csv")