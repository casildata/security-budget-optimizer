"""Turn a solution into business terms, and verify it by exhaustive enumeration."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .data import CSF_FUNCTIONS, Instance
from .model import cost_terms
from .solvers import Solution


def selected_controls(inst: Instance, sol: Solution) -> list:
    return [c for c in inst.controls.index if sol.values.get(f"x[{c}]", 0) > 0.5]


def residual_ale(inst: Instance, chosen: list) -> pd.Series:
    """Exact residual ALE per scenario for a set of controls: ALE * prod(1 - e)."""
    keep = (1.0 - inst.effects.loc[chosen]).prod(axis=0) if chosen else 1.0
    return inst.scenarios["ale"] * keep


def summarize(inst: Instance, sol: Solution) -> dict:
    chosen = selected_controls(inst, sol)
    spend = sum(cost * sol.values.get(var, 0) for var, cost in cost_terms(inst).items())
    after = residual_ale(inst, chosen)
    by_scenario = pd.DataFrame({
        "scenario": inst.scenarios["scenario"],
        "ale_before": inst.scenarios["ale"],
        "ale_after": after,
    })
    by_scenario["reduction"] = by_scenario["ale_before"] - by_scenario["ale_after"]
    reduction = by_scenario["reduction"].sum()
    hours = inst.controls.loc[chosen, "analyst_hours"].sum()
    return {
        "controls": chosen,
        "spend": spend,
        "reduction": reduction,
        "residual_ale": inst.total_ale - reduction,
        "net_benefit": reduction - spend,
        "rosi": (reduction - spend) / spend if spend else float("nan"),
        "analyst_hours": float(hours),
        "mssp": sol.values.get("z", 0) > 0.5,
        "platforms": [p for p in inst.policy.get("platforms", {}) if sol.values.get(f"y[{p}]", 0) > 0.5],
        "by_scenario": by_scenario,
    }


def optimal_portfolio(inst: Instance, budget: float, **kwargs) -> Solution:
    """Maximize risk reduction within budget; among ties, pick the cheapest portfolio."""
    from .model import build_model
    from .solvers import solve_scipy

    best = solve_scipy(build_model(inst, "max_reduction", budget=budget, **kwargs))
    if not best.optimal:
        return best
    share = (best.objective - 1.0) / inst.total_ale   # $1 tolerance
    cheapest = solve_scipy(build_model(inst, "min_cost", target_share=share, **kwargs))
    return cheapest if cheapest.optimal else best


def brute_force_max_reduction(inst: Instance, budget: float) -> tuple[float, list]:
    """
    Enumerate every subset of controls (2^n), apply all policy rules directly (not via the
    MILP matrices), compute the exact multiplicative residual, and return the best
    achievable risk reduction. An independent check of the model encoding. Platform and MSSP choices are derived: pay a platform fee only if
    one of its controls is chosen; hire the MSSP only if in-house hours are exceeded.
    """
    ctl, pol = inst.controls, inst.policy
    n = len(ctl)
    masks = ((np.arange(2 ** n)[:, None] >> np.arange(n)) & 1).astype(bool)
    ok = np.ones(len(masks), dtype=bool)
    col = {c: i for i, c in enumerate(ctl.index)}

    for g, members in ctl[ctl["exclusive_group"] != ""].groupby("exclusive_group").groups.items():
        ok &= masks[:, [col[c] for c in members]].sum(axis=1) <= 1
    for c, req in ctl["requires"].items():
        if req:
            ok &= ~masks[:, col[c]] | masks[:, col[req]]
    for c in pol.get("mandatory", []):
        ok &= masks[:, col[c]]
    k = pol.get("min_per_function", 0)
    fn = ctl["csf_function"].to_numpy()
    for f in CSF_FUNCTIONS:
        ok &= masks[:, fn == f].sum(axis=1) >= k
    ratio = pol.get("max_protect_ratio")
    if ratio:
        protect = masks[:, fn == "Protect"].sum(axis=1)
        other = masks[:, np.isin(fn, ["Detect", "Respond", "Recover"])].sum(axis=1)
        ok &= protect <= ratio * other

    hours = masks @ ctl["analyst_hours"].to_numpy(dtype=float)
    need_mssp = hours > pol["inhouse_hours"]
    if pol.get("mssp"):
        ok &= hours <= np.where(need_mssp, pol["mssp"]["hours_capacity"], pol["inhouse_hours"])
    else:
        ok &= ~need_mssp
    cost = masks @ ctl["annual_cost"].to_numpy(dtype=float)
    for p, spec in pol.get("platforms", {}).items():
        cost += masks[:, (ctl["platform"] == p).to_numpy()].any(axis=1) * spec["annual_fee"]
    if pol.get("mssp"):
        cost += need_mssp * pol["mssp"]["annual_fee"]
    ok &= cost <= budget + 1e-6

    ale = inst.scenarios["ale"].to_numpy(dtype=float)
    log_keep = np.log1p(-inst.effects.to_numpy())          # log(1 - e), controls x scenarios
    residual = np.exp(masks[ok] @ log_keep) * ale
    reduction = (ale - residual).sum(axis=1)
    if reduction.size == 0:
        return float("nan"), []
    best = int(np.argmax(reduction))
    chosen = list(ctl.index[masks[ok][best]])
    return float(reduction[best]), chosen
