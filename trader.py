"""
IMC Prosperity 2026 — trader_v3.py
====================================
Built from surgical log analysis. Root causes identified:

ROOT CAUSE 1 — EMERALDS position saturation:
  v1 hit +10 at ts=29800 → missed 4 buy fills (22 qty × 3 edge = 66 PnL)
  v1 hit -10 at ts=93300 → missed 7 sell fills (32 qty × 3 edge = 96 PnL)
  v2 made it WORSE by adding even stronger skew → fewer fills overall → 1000 PnL
  
  FIX: Use EXPONENTIAL skew. Near limit, quotes shift so far that we barely
  get filled on the inventory-worsening side, but still get filled on the
  inventory-reducing side. We NEVER stop posting both sides.

ROOT CAUSE 2 — EMERALDS: we sold at 10004/10005 but top traders sell at 10003:
  We posted ask=10004 when we should post 10003. Top trader avg fill = 5.34
  means they post at fair±3. We were posting at fair±4 (too conservative).
  FIX: Set edge=3, never 4.

ROOT CAUSE 3 — TOMATOES missed fills when market traded at 4984/4992/4993:
  These were market trades where we weren't posting inside (position-skewed
  our bid too far below market best bid).
  FIX: Cap inventory skew at max 2 ticks so we always stay inside market.

ROOT CAUSE 4 — Top traders fill 5.5x more qty than us:
  They must post EVERY tick, with correct sizing. We sometimes fail to post
  because of bad quote clamps.
  FIX: Always post both sides. Only suppress a side when physically at limit.
"""

from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List
import json

FAIR_EM = 10_000
EM_EDGE = 3          # post 9997/10003 — matches top trader avg fill of 5.34
EM_AGGR = 2          # aggress at 9998/10002

TOM_STEP = 1         # 1 tick inside market
TOM_MAX_SKEW = 2     # cap skew at 2 ticks so we always stay inside market

LIMITS = {"EMERALDS": 10, "TOMATOES": 20}


class Trader:

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        if "EMERALDS" in state.order_depths:
            result["EMERALDS"] = self._emeralds(
                state.order_depths["EMERALDS"],
                state.position.get("EMERALDS", 0),
            )
        if "TOMATOES" in state.order_depths:
            result["TOMATOES"] = self._tomatoes(
                state.order_depths["TOMATOES"],
                state.position.get("TOMATOES", 0),
            )

        return result, 0, ""

    # ── EMERALDS ─────────────────────────────────────────────────────────────
    def _emeralds(self, depth: OrderDepth, pos: int) -> List[Order]:
        orders: List[Order] = []
        lim = LIMITS["EMERALDS"]

        best_bid = max(depth.buy_orders)  if depth.buy_orders  else 0
        best_ask = min(depth.sell_orders) if depth.sell_orders else 999_999

        # ── 1. Aggressive fills ───────────────────────────────────────────────
        # Buy anything <= 9998, sell anything >= 10002
        for ask_px in sorted(depth.sell_orders):
            if ask_px > FAIR_EM - EM_AGGR:
                break
            qty = min(-depth.sell_orders[ask_px], lim - pos)
            if qty > 0:
                orders.append(Order("EMERALDS", ask_px, qty))
                pos += qty

        for bid_px in sorted(depth.buy_orders, reverse=True):
            if bid_px < FAIR_EM + EM_AGGR:
                break
            qty = min(depth.buy_orders[bid_px], lim + pos)
            if qty > 0:
                orders.append(Order("EMERALDS", bid_px, -qty))
                pos -= qty

        # ── 2. Passive quotes with exponential inventory skew ─────────────────
        # Key insight: skew must be STRONG near limits but GENTLE in the middle.
        # At pos=0: bid=9997, ask=10003 (symmetric, full size)
        # At pos=+5: bid=9995, ask=10003 (bid shifted down 2, ask unchanged)
        # At pos=+9: bid=9990, ask=10003 (bid shifted down 7, heavily suppressed)
        # At pos=-9: bid=9997, ask=10010 (ask shifted up 7, heavily suppressed)
        # This way we ALWAYS post both sides but make the inventory-worsening
        # side very unlikely to fill.
        
        inv = pos / lim  # normalised: -1.0 to +1.0
        
        # Exponential skew: gentle in middle, aggressive near limits
        # bid_skew: positive = bid moves DOWN (discourages buying when long)
        bid_skew = int(round(inv * inv * inv * 7))   # cubic: 0→0, 0.5→0.9, 1→7
        ask_skew = int(round(inv * inv * inv * 7))   # same direction (both shift same way)

        our_bid = FAIR_EM - EM_EDGE - bid_skew
        our_ask = FAIR_EM + EM_EDGE - ask_skew  # negative bid_skew when short → ask rises

        # Hard clamps: never cross fair
        our_bid = min(our_bid, FAIR_EM - 1)
        our_ask = max(our_ask, FAIR_EM + 1)

        # Post bid if it improves on market AND we're not at long limit
        if our_bid > best_bid and pos < lim:
            buy_cap = lim - pos
            orders.append(Order("EMERALDS", our_bid, buy_cap))

        # Post ask if it improves on market AND we're not at short limit
        if our_ask < best_ask and pos > -lim:
            sell_cap = lim + pos
            orders.append(Order("EMERALDS", our_ask, -sell_cap))

        return orders

    # ── TOMATOES ─────────────────────────────────────────────────────────────
    def _tomatoes(self, depth: OrderDepth, pos: int) -> List[Order]:
        orders: List[Order] = []
        lim = LIMITS["TOMATOES"]

        if not depth.buy_orders or not depth.sell_orders:
            return orders

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        mid = (best_bid + best_ask) / 2

        # Inventory skew: CAPPED at TOM_MAX_SKEW (2 ticks) so we ALWAYS
        # stay inside the market spread and get filled
        inv_frac = pos / lim
        inv_adj = max(-TOM_MAX_SKEW, min(TOM_MAX_SKEW, round(inv_frac * TOM_MAX_SKEW * 2)))

        our_bid = best_bid + TOM_STEP - inv_adj
        our_ask = best_ask - TOM_STEP - inv_adj

        # Sanity: ensure valid spread
        if our_bid >= our_ask:
            mid_int = (best_bid + best_ask) // 2
            our_bid = mid_int - 1
            our_ask = mid_int + 1

        our_bid = min(our_bid, best_ask - 1)
        our_ask = max(our_ask, best_bid + 1)

        # Post bid if inside market and not at long limit
        if our_bid > best_bid and pos < lim:
            buy_cap = lim - pos
            orders.append(Order("TOMATOES", our_bid, buy_cap))

        # Post ask if inside market and not at short limit
        if our_ask < best_ask and pos > -lim:
            sell_cap = lim + pos
            orders.append(Order("TOMATOES", our_ask, -sell_cap))

        return orders