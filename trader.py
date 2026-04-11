
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

    # ── EMERALDS (v22 proven logic, 1015 PnL) ────────────────────────────────
    def _emeralds(self, depth: OrderDepth, pos: int) -> List[Order]:
        orders: List[Order] = []
        lim = LIMITS["EMERALDS"]
        fair = FAIR_EM

        best_bid = max(depth.buy_orders) if depth.buy_orders else fair - 8
        best_ask = min(depth.sell_orders) if depth.sell_orders else fair + 8

        max_buy = lim - pos
        max_sell = lim + pos
        tb = 0
        ts = 0

        # Phase 1: Take mispriced
        for ask_px in sorted(depth.sell_orders):
            if ask_px >= fair:
                break
            room = max_buy - tb
            if room <= 0:
                break
            qty = min(-depth.sell_orders[ask_px], room)
            if qty > 0:
                orders.append(Order("EMERALDS", ask_px, qty))
                tb += qty

        for bid_px in sorted(depth.buy_orders, reverse=True):
            if bid_px <= fair:
                break
            room = max_sell - ts
            if room <= 0:
                break
            qty = min(depth.buy_orders[bid_px], room)
            if qty > 0:
                orders.append(Order("EMERALDS", bid_px, -qty))
                ts += qty

        # Phase 2: Exit at fair (proven to work - aggressive take)
        pp = pos + tb - ts
        if pp > 10:
            exit_qty = min(pp - 5, max_sell - ts)
            if exit_qty > 0:
                orders.append(Order("EMERALDS", fair, -exit_qty))
                ts += exit_qty
        elif pp < -10:
            exit_qty = min(-pp - 5, max_buy - tb)
            if exit_qty > 0:
                orders.append(Order("EMERALDS", fair, exit_qty))
                tb += exit_qty

        # Phase 3: Passive quotes at 9993/10007
        pp = pos + tb - ts
        inv_frac = pp / lim
        raw_skew = inv_frac * 3 + (inv_frac ** 3) * 5
        skew = int(round(raw_skew))

        our_bid = best_bid + 1 - skew
        our_ask = best_ask - 1 - skew
        our_bid = min(our_bid, fair - 1)
        our_ask = max(our_ask, fair + 1)
        if our_ask <= our_bid:
            our_bid = fair - 1
            our_ask = fair + 1

        # Phase 4: Size
        buy_room = max_buy - tb
        sell_room = max_sell - ts
        abs_pp = abs(pp)

        if pp > 0:
            buy_cap = 0 if abs_pp > 60 else min(5, buy_room) if abs_pp > 35 else min(20, buy_room) if abs_pp > 15 else buy_room
            sell_cap = sell_room
        elif pp < 0:
            sell_cap = 0 if abs_pp > 60 else min(5, sell_room) if abs_pp > 35 else min(20, sell_room) if abs_pp > 15 else sell_room
            buy_cap = buy_room
        else:
            buy_cap = buy_room
            sell_cap = sell_room

        if buy_cap > 0:
            orders.append(Order("EMERALDS", our_bid, buy_cap))
        if sell_cap > 0:
            orders.append(Order("EMERALDS", our_ask, -sell_cap))

        return orders

    # ── TOMATOES (v22 base + optimizations) ───────────────────────────────────
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

        # Fair value: VWAP mid
        if (bid_vol + ask_vol) > 0:
            fair = int(round(
                (best_bid * ask_vol + best_ask * bid_vol) / (bid_vol + ask_vol)
            ))
        else:
            fair = (best_bid + best_ask) // 2

        # Counter-trend signal: fade last tick's move
        ct_adj = 0
        if prev_mid is not None:
            delta = mid - prev_mid
            # AC=-0.43: after +2 move, expect -0.86 next
            # Lean counter by ~40% of the move
            # Use 0.8 multiplier so delta=1 rounds to 1
            ct_adj = -int(round(delta * 0.8))
            # Clamp to prevent over-correction
            ct_adj = max(-2, min(2, ct_adj))

        max_buy = lim - pos
        max_sell = lim + pos
        tb = 0
        ts = 0

        # ── Phase 1: Sweep mispriced ─────────────────────────────────────
        for ask_px in sorted(depth.sell_orders):
            if ask_px >= fair:
                break
            room = max_buy - tb
            if room <= 0:
                break
            qty = min(-depth.sell_orders[ask_px], room)
            if qty > 0:
                orders.append(Order("TOMATOES", ask_px, qty))
                tb += qty

        for bid_px in sorted(depth.buy_orders, reverse=True):
            if bid_px <= fair:
                break
            room = max_sell - ts
            if room <= 0:
                break
            qty = min(depth.buy_orders[bid_px], room)
            if qty > 0:
                orders.append(Order("TOMATOES", bid_px, -qty))
                ts += qty

        # ── Phase 2: Exit at fair when loaded ────────────────────────────
        pp = pos + tb - ts
        if pp > 12:
            exit_qty = min(pp - 8, max_sell - ts)
            if exit_qty > 0:
                orders.append(Order("TOMATOES", fair, -exit_qty))
                ts += exit_qty
        elif pp < -12:
            exit_qty = min(-pp - 8, max_buy - tb)
            if exit_qty > 0:
                orders.append(Order("TOMATOES", fair, exit_qty))
                tb += exit_qty

        # ── Phase 3: Multi-level passive quotes ──────────────────────────
        pp = pos + tb - ts
        inv_frac = pp / lim

        # Strong inventory skew
        raw_skew = inv_frac * 7 + (inv_frac ** 3) * 18
        inv_skew = int(round(raw_skew))

        # Combined skew: inventory + counter-trend
        total_skew = inv_skew + ct_adj

        buy_room = max_buy - tb
        sell_room = max_sell - ts
        abs_pp = abs(pp)

        if spread >= 10:
            # Inner level: 2 ticks inside BBO (proven in v22)
            inner_bid = best_bid + 2 - total_skew
            inner_ask = best_ask - 2 - total_skew

            # Outer level: 4 ticks inside BBO (was 5, now tighter for more fills)
            outer_bid = best_bid + 4 - total_skew
            outer_ask = best_ask - 4 - total_skew

            # Clamp: inner must be inside spread
            inner_bid = min(inner_bid, best_ask - 1)
            inner_ask = max(inner_ask, best_bid + 1)
            
            # Outer must not cross inner
            outer_bid = min(outer_bid, inner_bid - 1)
            outer_ask = max(outer_ask, inner_ask + 1)
            
            # Outer must be inside spread
            outer_bid = max(outer_bid, best_bid)
            outer_ask = min(outer_ask, best_ask)

            # Size allocation per level
            if pp > 0:
                # Long: throttle buys, max sells
                if abs_pp > 30:
                    ib_sz = 0; ob_sz = 0
                elif abs_pp > 15:
                    ib_sz = min(3, buy_room); ob_sz = 0
                else:
                    ib_sz = min(10, buy_room)
                    ob_sz = min(max(buy_room - ib_sz, 0), 12)
                is_sz = min(15, sell_room)
                os_sz = min(max(sell_room - is_sz, 0), 20)
            elif pp < 0:
                # Short: throttle sells, max buys
                if abs_pp > 30:
                    is_sz = 0; os_sz = 0
                elif abs_pp > 15:
                    is_sz = min(3, sell_room); os_sz = 0
                else:
                    is_sz = min(10, sell_room)
                    os_sz = min(max(sell_room - is_sz, 0), 12)
                ib_sz = min(15, buy_room)
                ob_sz = min(max(buy_room - ib_sz, 0), 20)
            else:
                # Flat: balanced across levels
                ib_sz = min(12, buy_room)
                ob_sz = min(max(buy_room - ib_sz, 0), 15)
                is_sz = min(12, sell_room)
                os_sz = min(max(sell_room - is_sz, 0), 15)

            # Post inner
            if ib_sz > 0:
                orders.append(Order("TOMATOES", inner_bid, ib_sz))
            if is_sz > 0:
                orders.append(Order("TOMATOES", inner_ask, -is_sz))
            # Post outer
            if ob_sz > 0 and outer_bid >= best_bid:
                orders.append(Order("TOMATOES", outer_bid, ob_sz))
            if os_sz > 0 and outer_ask <= best_ask:
                orders.append(Order("TOMATOES", outer_ask, -os_sz))

        else:
            # Tight spread: single level
            our_bid = best_bid + 1 - total_skew
            our_ask = best_ask - 1 - total_skew
            if our_ask <= our_bid:
                our_bid = fair - 1
                our_ask = fair + 1
            our_bid = min(our_bid, best_ask - 1)
            our_ask = max(our_ask, best_bid + 1)

            if pp > 0:
                buy_cap = 0 if abs_pp > 25 else min(8, buy_room)
                sell_cap = sell_room
            elif pp < 0:
                sell_cap = 0 if abs_pp > 25 else min(8, sell_room)
                buy_cap = buy_room
            else:
                buy_cap = min(12, buy_room)
                sell_cap = min(12, sell_room)

            if buy_cap > 0:
                orders.append(Order("TOMATOES", our_bid, buy_cap))
            if sell_cap > 0:
                orders.append(Order("TOMATOES", our_ask, -sell_cap))

        return orders, mid

    def bid(self):
        return 0 , 