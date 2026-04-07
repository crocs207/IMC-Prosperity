"""
IMC Prosperity 2026 — Tutorial Round Trader v2
===============================================
Improvements from log analysis:

EMERALDS ISSUE: We ended with position = -10 (max short). This means our
sell side was too aggressive vs our buy side. At end of day, -10 units marked
at 10000 = -10 * 0 = 0 unrealized, BUT we missed buy opportunities to flatten.
Fix: Stronger inventory skew to prevent hitting position limits.
Also: We only filled 62% of market trades — we need to post BOTH sides always.

TOMATOES ISSUE: avg buy=4984.6, avg sell=4996.6 → only ~12 spread captured
on a 13-tick market. But TOMATOES is trending DOWN overall (started ~5006,
ended ~4990), so our longs were underwater. 
Fix: Add a slow trend filter. Don't accumulate longs if price is trending down.

KEY INSIGHT FROM TOP PERFORMERS (avg fill ~5.3-5.5):
They are likely doing pure arbitrage on EMERALDS — buying everything below
10000 and selling everything above 10000 using the FULL position limit each
tick. Our edge was 3-4 ticks per fill which is good, but we ran out of
inventory (hit -10) and stopped earning.
"""

from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List
import json
import math


# ─── Parameters ──────────────────────────────────────────────────────────────

EM_FAIR       = 10_000
EM_EDGE       = 3        # half-spread: bid=9997, ask=10003
EM_AGGR_EDGE  = 2        # aggress if ask<=9998 or bid>=10002

TOM_STEP      = 1        # step inside market
TOM_INV_MAX   = 3        # stronger inventory skew (was 1)
TOM_TREND_WIN = 50       # ticks for trend detection

POSITION_LIMITS = {
    "EMERALDS": 10,
    "TOMATOES": 20,
}


class Trader:

    def __init__(self):
        self.tom_mids: List[float] = []

    def run(self, state: TradingState):
        orders: Dict[str, List[Order]] = {}

        # Restore state
        if state.traderData:
            try:
                saved = json.loads(state.traderData)
                self.tom_mids = saved.get("tom_mids", [])
            except Exception:
                self.tom_mids = []

        if "EMERALDS" in state.order_depths:
            orders["EMERALDS"] = self._emeralds(
                state.order_depths["EMERALDS"],
                state.position.get("EMERALDS", 0),
            )

        if "TOMATOES" in state.order_depths:
            orders["TOMATOES"] = self._tomatoes(
                state.order_depths["TOMATOES"],
                state.position.get("TOMATOES", 0),
            )

        trader_data = json.dumps({"tom_mids": self.tom_mids[-TOM_TREND_WIN:]})
        return orders, 0, trader_data

    # ── EMERALDS ──────────────────────────────────────────────────────────────
    def _emeralds(self, depth: OrderDepth, pos: int) -> List[Order]:
        result: List[Order] = []
        lim  = POSITION_LIMITS["EMERALDS"]
        fair = EM_FAIR

        # Aggressive: lift cheap asks
        for ask_px in sorted(depth.sell_orders):
            if ask_px <= fair - EM_AGGR_EDGE:
                qty = min(-depth.sell_orders[ask_px], lim - pos)
                if qty > 0:
                    result.append(Order("EMERALDS", ask_px, qty))
                    pos += qty
            else:
                break

        # Aggressive: hit rich bids
        for bid_px in sorted(depth.buy_orders, reverse=True):
            if bid_px >= fair + EM_AGGR_EDGE:
                qty = min(depth.buy_orders[bid_px], lim + pos)
                if qty > 0:
                    result.append(Order("EMERALDS", bid_px, -qty))
                    pos -= qty
            else:
                break

        # Passive quotes with STRONGER inventory skew to prevent limit saturation
        # Scale skew proportionally to inventory fraction
        inv_frac = pos / lim           # -1.0 to 1.0
        inv_adj  = round(inv_frac * 2) # -2 to +2

        our_bid = min(fair - EM_EDGE - inv_adj, fair - 1)
        our_ask = max(fair + EM_EDGE - inv_adj, fair + 1)

        best_bid = max(depth.buy_orders)  if depth.buy_orders  else 0
        best_ask = min(depth.sell_orders) if depth.sell_orders else 999_999

        if our_bid > best_bid:
            buy_cap = lim - pos
            if buy_cap > 0:
                result.append(Order("EMERALDS", our_bid, buy_cap))

        if our_ask < best_ask:
            sell_cap = lim + pos
            if sell_cap > 0:
                result.append(Order("EMERALDS", our_ask, -sell_cap))

        return result

    # ── TOMATOES ─────────────────────────────────────────────────────────────
    def _tomatoes(self, depth: OrderDepth, pos: int) -> List[Order]:
        result: List[Order] = []
        lim = POSITION_LIMITS["TOMATOES"]

        if not depth.buy_orders or not depth.sell_orders:
            return result

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        mid      = (best_bid + best_ask) / 2

        # Track mid price history
        self.tom_mids.append(mid)
        if len(self.tom_mids) > TOM_TREND_WIN:
            self.tom_mids.pop(0)

        # Trend detection: compare recent half vs earlier half
        trend_bias = 0
        if len(self.tom_mids) >= TOM_TREND_WIN:
            half = TOM_TREND_WIN // 2
            recent = sum(self.tom_mids[-half:]) / half
            earlier = sum(self.tom_mids[:half]) / half
            diff = recent - earlier
            # Bias: if trending down, resist buying; if trending up, resist selling
            if diff < -3:    trend_bias = 1   # trending down → skew ask (sell more)
            elif diff > 3:   trend_bias = -1  # trending up   → skew bid (buy more)

        # Inventory skew (stronger than before)
        inv_adj = round((pos / lim) * TOM_INV_MAX) + trend_bias

        our_bid = best_bid + TOM_STEP - inv_adj
        our_ask = best_ask - TOM_STEP - inv_adj

        # Sanity guards
        if our_bid >= our_ask:
            mid_int = (best_bid + best_ask) // 2
            our_bid = mid_int - 1
            our_ask = mid_int + 1
        our_bid = min(our_bid, best_ask - 1)
        our_ask = max(our_ask, best_bid + 1)

        if our_bid > best_bid:
            buy_cap = lim - pos
            if buy_cap > 0:
                result.append(Order("TOMATOES", our_bid, buy_cap))

        if our_ask < best_ask:
            sell_cap = lim + pos
            if sell_cap > 0:
                result.append(Order("TOMATOES", our_ask, -sell_cap))

        return result