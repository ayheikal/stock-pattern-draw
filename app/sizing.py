"""Position sizing: how many shares to buy so a stop-out costs a fixed, small part of the capital."""
from __future__ import annotations

import math


def size_position(capital: float, risk_pct: float, max_pos_pct: float, entry: float, stop: float) -> dict:
    """Shares to buy at `entry` with a protective `stop` below it (long only).

    Two limits apply and the smaller wins:
      risk  - losing (entry - stop) per share must not exceed risk_pct of capital
      size  - the position must not exceed max_pos_pct of capital
    """
    budget = capital * risk_pct / 100
    per_share = entry - stop
    if entry <= 0 or capital <= 0:
        return {"ok": False, "reason": "Capital and price must be positive."}
    if per_share <= 0:
        return {"ok": False, "reason": "The stop must be below the buy price."}
    by_risk = math.floor(budget / per_share)
    by_size = math.floor(capital * max_pos_pct / 100 / entry)
    shares = min(by_risk, by_size)
    if shares < 1:
        reason = (f"Even one share would risk {per_share:,.2f}, more than your {risk_pct:g}% budget ({budget:,.2f})."
                  if by_risk < 1 else f"One share costs more than your {max_pos_pct:g}% limit per stock.")
        return {"ok": False, "reason": reason}
    risk = shares * per_share
    cost = shares * entry
    return {
        "ok": True,
        "shares": shares,
        "entry": round(entry, 4),
        "stop": round(stop, 4),
        "stopPct": round((stop - entry) / entry * 100, 2),
        "cost": round(cost, 2),
        "costPct": round(cost / capital * 100, 1),
        "risk": round(risk, 2),
        "riskPct": round(risk / capital * 100, 2),
        "budget": round(budget, 2),
        "limitedBy": "risk" if by_risk <= by_size else "size",
    }


def position_risk(capital: float, side: str, qty: float, last: float, stop: float | None) -> dict:
    """What an open position means for the account: its weight, and what hitting the stop would cost."""
    value = qty * last
    out = {"value": round(value, 2), "weightPct": round(value / capital * 100, 1), "risk": None, "riskPct": None}
    if stop is not None:
        loss = (last - stop) * qty if side == "long" else (stop - last) * qty
        out["risk"] = round(max(loss, 0.0), 2)
        out["riskPct"] = round(max(loss, 0.0) / capital * 100, 2)
    return out
