"""Position sizing: how many shares to buy so a stop-out costs a fixed, small part of the capital."""
from __future__ import annotations

import math


def size_position(capital: float, risk_pct: float, max_pos_pct: float, entry: float, stop: float,
                  fee_pct: float = 0.0, max_shares: int | None = None,
                  caps: dict[str, tuple[int, str]] | None = None) -> dict:
    """Shares to buy at `entry` with a protective `stop` below it (long only).

    The smallest of these limits wins:
      risk      - losing (entry - stop) per share, plus buying and selling costs, must not exceed risk_pct of capital
      size      - the position must not exceed max_pos_pct of capital
      liquidity - `max_shares`, when given (e.g. a share of the stock's normal daily trading)
      caps      - more limits from the rest of the portfolio, {name: (max shares, why)}: cash, total risk, sector
    """
    f = fee_pct / 100
    budget = capital * risk_pct / 100
    if entry <= 0 or capital <= 0:
        return {"ok": False, "reason": "Capital and price must be positive."}
    if stop >= entry:
        return {"ok": False, "reason": "The stop must be below the buy price."}
    per_share = entry * (1 + f) - stop * (1 - f)
    limits = {
        "risk": math.floor(budget / per_share),
        "size": math.floor(capital * max_pos_pct / 100 / (entry * (1 + f))),
    }
    if max_shares is not None:
        limits["liquidity"] = int(max_shares)
    caps = caps or {}
    limits |= {k: max(0, int(n)) for k, (n, _) in caps.items()}
    limited_by = min(limits, key=limits.get)
    shares = limits[limited_by]
    if shares < 1:
        reason = {
            "risk": f"Even one share would risk {per_share:,.2f}, more than your {risk_pct:g}% budget ({budget:,.2f}).",
            "size": f"One share costs more than your {max_pos_pct:g}% limit per stock.",
            "liquidity": "The stock trades too little for even one share within your liquidity limit.",
        }.get(limited_by) or f"No room for a new position: {caps[limited_by][1]}"
        return {"ok": False, "reason": reason, "limitedBy": limited_by}
    risk = shares * per_share
    cost = shares * entry * (1 + f)
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
        "feePct": fee_pct,
        "limitedBy": limited_by,
        "limitNote": caps[limited_by][1] if limited_by in caps else None,  # a portfolio rule made it smaller
    }


def position_risk(capital: float, side: str, qty: float, last: float, stop: float | None, fee_pct: float = 0.0) -> dict:
    """What an open position means for the account: its weight, and what hitting the stop would cost (incl. the exit fee)."""
    f = fee_pct / 100
    value = qty * last
    out = {"value": round(value, 2), "weightPct": round(value / capital * 100, 1), "risk": None, "riskPct": None}
    if stop is not None:
        move = (last - stop) if side == "long" else (stop - last)
        loss = max(move * qty, 0.0) + stop * qty * f
        out["risk"] = round(loss, 2)
        out["riskPct"] = round(loss / capital * 100, 2)
    return out
