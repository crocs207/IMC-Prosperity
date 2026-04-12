
from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List
import json

LIMITS = {"EMERALDS": 80, "TOMATOES": 80}
FAIR_EM = 10_000


class Trader:

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        try:
            saved = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            saved = {}

        tom_prev_mid = saved.get("tpm", None)

        if "EMERALDS" in state.order_depths:
            result["EMERALDS"] = self._emeralds(
                state.order_depths["EMERALDS"],
                state.position.get("EMERALDS", 0),
            )

        new_tom_mid = tom_prev_mid
        if "TOMATOES" in state.order_depths:
            orders, new_tom_mid = self._tomatoes(
                state.order_depths["TOMATOES"],
                state.position.get("TOMATOES", 0),
                tom_prev_mid,
            )
            result["TOMATOES"] = orders

        new_data = json.dumps({"tpm": new_tom_mid})
        return result, 0, new_data

    # ── EMERALDS — size-only inventory, max edge ─────────────────────────────
    def _emeralds(self, depth: OrderDepth, pos: int) -> List[Order]:
        orders: List[Order] = []
        lim = LIMITS["EMERALDS"]
        fair = FAIR_EM

        best_bid = max(depth.buy_orders) if depth.buy_orders else fair - 8
        best_ask = min(depth.sell_orders) if depth.sell_orders else fair + 8

        max_buy = lim - pos
        max_sell = lim + pos
        tb = 0; ts = 0

        # Phase 1: Take mispriced (same as always)
        for ask_px in sorted(depth.sell_orders):
            if ask_px >= fair: break
            room = max_buy - tb
            if room <= 0: break
            qty = min(-depth.sell_orders[ask_px], room)
            if qty > 0:
                orders.append(Order("EMERALDS", ask_px, qty)); tb += qty

        for bid_px in sorted(depth.buy_orders, reverse=True):
            if bid_px <= fair: break
            room = max_sell - ts
            if room <= 0: break
            qty = min(depth.buy_orders[bid_px], room)
            if qty > 0:
                orders.append(Order("EMERALDS", bid_px, -qty)); ts += qty

        # NO EXIT-AT-FAIR. Every fill earns 7 edge.

        # Phase 2: Fixed-price quotes, size-only inventory control
        pp = pos + tb - ts
        abs_pp = abs(pp)

        # Prices ALWAYS at optimal edge — never degraded by skew
        our_bid = min(best_bid + 1, fair - 1)   # 9993
        our_ask = max(best_ask - 1, fair + 1)    # 10007

        # SIZE controls inventory:
        buy_room = max_buy - tb
        sell_room = max_sell - ts

        if pp > 0:
            # Long: reduce buys, full sells
            if abs_pp > 50:
                buy_sz = 0
            elif abs_pp > 30:
                buy_sz = min(5, buy_room)
            elif abs_pp > 15:
                buy_sz = min(15, buy_room)
            else:
                buy_sz = min(35, buy_room)
            sell_sz = min(60, sell_room)
        elif pp < 0:
            # Short: reduce sells, full buys
            if abs_pp > 50:
                sell_sz = 0
            elif abs_pp > 30:
                sell_sz = min(5, sell_room)
            elif abs_pp > 15:
                sell_sz = min(15, sell_room)
            else:
                sell_sz = min(35, sell_room)
            buy_sz = min(60, buy_room)
        else:
            buy_sz = min(35, buy_room)
            sell_sz = min(35, sell_room)

        if buy_sz > 0:
            orders.append(Order("EMERALDS", our_bid, buy_sz))
        if sell_sz > 0:
            orders.append(Order("EMERALDS", our_ask, -sell_sz))

        return orders

    # ── TOMATOES — size-only + counter-trend alpha ────────────────────────────
    def _tomatoes(self, depth: OrderDepth, pos: int, prev_mid):
        orders: List[Order] = []
        lim = LIMITS["TOMATOES"]

        if not depth.buy_orders or not depth.sell_orders:
            return orders, prev_mid

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        bid_vol = depth.buy_orders[best_bid]
        ask_vol = abs(depth.sell_orders[best_ask])
        spread = best_ask - best_bid
        mid = (best_bid + best_ask) / 2.0

        # VWAP fair
        if (bid_vol + ask_vol) > 0:
            fair = int(round(
                (best_bid * ask_vol + best_ask * bid_vol) / (bid_vol + ask_vol)
            ))
        else:
            fair = (best_bid + best_ask) // 2

        # Counter-trend ALPHA signal (not inventory — this is directional edge)
        ct_adj = 0
        if prev_mid is not None:
            delta = mid - prev_mid
            ct_adj = -int(round(delta * 0.8))
            ct_adj = max(-2, min(2, ct_adj))

        max_buy = lim - pos
        max_sell = lim + pos
        tb = 0; ts = 0

        # Phase 1: Take mispriced
        for ask_px in sorted(depth.sell_orders):
            if ask_px >= fair: break
            room = max_buy - tb
            if room <= 0: break
            qty = min(-depth.sell_orders[ask_px], room)
            if qty > 0:
                orders.append(Order("TOMATOES", ask_px, qty)); tb += qty

        for bid_px in sorted(depth.buy_orders, reverse=True):
            if bid_px <= fair: break
            room = max_sell - ts
            if room <= 0: break
            qty = min(depth.buy_orders[bid_px], room)
            if qty > 0:
                orders.append(Order("TOMATOES", bid_px, -qty)); ts += qty

        # NO EXIT-AT-FAIR. Size manages inventory.

        # Phase 2: Multi-level quotes — prices use CT only (alpha), not inventory skew
        pp = pos + tb - ts
        abs_pp = abs(pp)
        buy_room = max_buy - tb
        sell_room = max_sell - ts

        if spread >= 10:
            # CT shifts prices for ALPHA (directional signal)
            inner_bid = best_bid + 2 - ct_adj
            inner_ask = best_ask - 2 - ct_adj
            outer_bid = best_bid + 4 - ct_adj
            outer_ask = best_ask - 4 - ct_adj

            # Clamp
            inner_bid = min(inner_bid, best_ask - 1)
            inner_ask = max(inner_ask, best_bid + 1)
            outer_bid = min(outer_bid, inner_bid - 1)
            outer_ask = max(outer_ask, inner_ask + 1)
            outer_bid = max(outer_bid, best_bid)
            outer_ask = min(outer_ask, best_ask)

            # SIZE-ONLY inventory management
            if pp > 0:
                # Long: cut buys, max sells
                if abs_pp > 40:
                    ib_sz = 0; ob_sz = 0
                elif abs_pp > 20:
                    ib_sz = min(3, buy_room); ob_sz = 0
                elif abs_pp > 10:
                    ib_sz = min(8, buy_room)
                    ob_sz = min(max(buy_room - ib_sz, 0), 5)
                else:
                    ib_sz = min(12, buy_room)
                    ob_sz = min(max(buy_room - ib_sz, 0), 15)
                is_sz = min(20, sell_room)
                os_sz = min(max(sell_room - is_sz, 0), 25)
            elif pp < 0:
                if abs_pp > 40:
                    is_sz = 0; os_sz = 0
                elif abs_pp > 20:
                    is_sz = min(3, sell_room); os_sz = 0
                elif abs_pp > 10:
                    is_sz = min(8, sell_room)
                    os_sz = min(max(sell_room - is_sz, 0), 5)
                else:
                    is_sz = min(12, sell_room)
                    os_sz = min(max(sell_room - is_sz, 0), 15)
                ib_sz = min(20, buy_room)
                ob_sz = min(max(buy_room - ib_sz, 0), 25)
            else:
                ib_sz = min(12, buy_room)
                ob_sz = min(max(buy_room - ib_sz, 0), 15)
                is_sz = min(12, sell_room)
                os_sz = min(max(sell_room - is_sz, 0), 15)

            if ib_sz > 0: orders.append(Order("TOMATOES", inner_bid, ib_sz))
            if is_sz > 0: orders.append(Order("TOMATOES", inner_ask, -is_sz))
            if ob_sz > 0 and outer_bid >= best_bid:
                orders.append(Order("TOMATOES", outer_bid, ob_sz))
            if os_sz > 0 and outer_ask <= best_ask:
                orders.append(Order("TOMATOES", outer_ask, -os_sz))
        else:
            our_bid = best_bid + 1 - ct_adj
            our_ask = best_ask - 1 - ct_adj
            if our_ask <= our_bid: our_bid = fair - 1; our_ask = fair + 1
            our_bid = min(our_bid, best_ask - 1)
            our_ask = max(our_ask, best_bid + 1)

            if pp > 0:
                buy_cap = 0 if abs_pp > 30 else min(5, buy_room)
                sell_cap = sell_room
            elif pp < 0:
                sell_cap = 0 if abs_pp > 30 else min(5, sell_room)
                buy_cap = buy_room
            else:
                buy_cap = min(12, buy_room); sell_cap = min(12, sell_room)

            if buy_cap > 0: orders.append(Order("TOMATOES", our_bid, buy_cap))
            if sell_cap > 0: orders.append(Order("TOMATOES", our_ask, -sell_cap))

        return orders, mid

    def bid(self):
        return 0