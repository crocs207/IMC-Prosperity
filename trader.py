"""
IMC Prosperity 2026 — Tutorial Round Trader

Optimised from backtesting on tutorial round data.

EMERALDS  → Market make around known fair value = 10,000
            Best params: edge=4, aggr_edge=2
            Post bid=9996 / ask=10004 (inside the bots' 9992/10008)
            Lift any ask <= 9998 or hit any bid >= 10002 aggressively

TOMATOES  → Step inside the market quotes by 1 tick each side
            Inventory-skew quotes to stay flat
            Best params: step=1, inv_max=1

Both strategies earn the spread passively; EMERALDS also uses light aggression
near fair value to guarantee fills when the price is already favourable.
"""

from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List
import json


# ─── Tunable parameters (from backtesting) ─────────────

EM_FAIR       = 10_000   #  we known true value for EMERALDS
EM_EDGE       = 4        #  the half-spread: post 9996 / 10008
EM_AGGR_EDGE  = 2        # aggression when ask <= 9998 or bid >= 10002

TOM_STEP      = 1        # post the  best_bid+1 / best_ask-1
TOM_INV_MAX   = 1        # max inventory-skew adjustment in ticks

POSITION_LIMITS = {
    "EMERALDS": 10,
    "TOMATOES": 20,
}


# ─── Trader ────────────────────────────────
class Trader:

    def run(self, state: TradingState):
        orders: Dict[str, List[Order]] = {}

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

        return orders, 0, ""  # Basically orders for the tick and 0 is the conversion which is not given and  "" trader data to carry to next tick 

    # ─────────────────────────────────────────────────────────────────────────
    def _emeralds(self, depth: OrderDepth, pos: int) -> List[Order]:
        result: List[Order] = []
        lim  = POSITION_LIMITS["EMERALDS"]
        fair = EM_FAIR

        # ── Aggressive: lift low asks ──────────────────────────────────────
        for ask_px in sorted(depth.sell_orders):
            if ask_px <= fair - EM_AGGR_EDGE:
                qty = min(-depth.sell_orders[ask_px], lim - pos)
                if qty > 0:
                    result.append(Order("EMERALDS", ask_px, qty))
                    pos += qty
            else:
                break

        # ── Aggressive: hit high bids ────────────────────────────────────────
        for bid_px in sorted(depth.buy_orders, reverse=True):
            if bid_px >= fair + EM_AGGR_EDGE:
                qty = min(depth.buy_orders[bid_px], lim + pos)
                if qty > 0:
                    result.append(Order("EMERALDS", bid_px, -qty))
                    pos -= qty
            else:
                break

        # ── Passive: post tight quotes with inventory skew ───────────────────
        inv_adj = round(pos / lim)                      # -1, 0, or +1
        our_bid = min(fair - EM_EDGE - inv_adj, fair - 1)
        our_ask = max(fair + EM_EDGE - inv_adj, fair + 1)

        best_bid = max(depth.buy_orders)  if depth.buy_orders  else 0
        best_ask = min(depth.sell_orders) if depth.sell_orders else 999_999

        if our_bid > best_bid:                          # only post if improving
            buy_cap = lim - pos
            if buy_cap > 0:
                result.append(Order("EMERALDS", our_bid, buy_cap))

        if our_ask < best_ask:
            sell_cap = lim + pos
            if sell_cap > 0:
                result.append(Order("EMERALDS", our_ask, -sell_cap))

        return result

    # ─────────────────────────────────────────────────────────────────────────
    def _tomatoes(self, depth: OrderDepth, pos: int) -> List[Order]:
        result: List[Order] = []
        lim = POSITION_LIMITS["TOMATOES"]

        if not depth.buy_orders or not depth.sell_orders:
            return result

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)

        # Inventory skew: lean against current position
        inv_adj = round((pos / lim) * TOM_INV_MAX)

        our_bid = best_bid + TOM_STEP - inv_adj
        our_ask = best_ask - TOM_STEP - inv_adj

        # Sanity guards
        if our_bid >= our_ask:
            mid = (best_bid + best_ask) // 2
            our_bid = mid - 1
            our_ask = mid + 1
        our_bid = min(our_bid, best_ask - 1)
        our_ask = max(our_ask, best_bid + 1)

        # Only post if we're inside the market (improves on best quote)
        if our_bid > best_bid:
            buy_cap = lim - pos
            if buy_cap > 0:
                result.append(Order("TOMATOES", our_bid, buy_cap))

        if our_ask < best_ask:
            sell_cap = lim + pos
            if sell_cap > 0:
                result.append(Order("TOMATOES", our_ask, -sell_cap))

        return result

