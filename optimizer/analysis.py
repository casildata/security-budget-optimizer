"""Turn a solution into business terms, verify it by exhaustive enumeration, and stress it."""

from __future__ import annotations

from dataclasses import replace

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


def _assess(inst: Instance, masks: np.ndarray) -> dict:
    """
    Apply the policy rules directly (not via the MILP matrices) to each subset of controls,
    one boolean row per subset. Platform and MSSP choices are derived: pay a platform fee
    only if one of its controls is chosen; hire the MSSP only if in-house hours are exceeded.
    `local` covers conflicts, prerequisites and hours; `policy` holds each mandate, coverage
    and ratio rule separately so a baseline can report which ones it breaks.
    """
    ctl, pol = inst.controls, inst.policy
    col = {c: i for i, c in enumerate(ctl.index)}
    local = np.ones(len(masks), dtype=bool)
    for g, members in ctl[ctl["exclusive_group"] != ""].groupby("exclusive_group").groups.items():
        local &= masks[:, [col[c] for c in members]].sum(axis=1) <= 1
    for c, req in ctl["requires"].items():
        if req:
            local &= ~masks[:, col[c]] | masks[:, col[req]]

    policy = {}
    for c in pol.get("mandatory", []):
        policy[f"mandatory {c}"] = masks[:, col[c]]
    k = pol.get("min_per_function", 0)
    fn = ctl["csf_function"].to_numpy()
    for f in CSF_FUNCTIONS:
        policy[f"{f} coverage"] = masks[:, fn == f].sum(axis=1) >= k
    ratio = pol.get("max_protect_ratio")
    if ratio:
        protect = masks[:, fn == "Protect"].sum(axis=1)
        other = masks[:, np.isin(fn, ["Detect", "Respond", "Recover"])].sum(axis=1)
        policy["Protect ratio"] = protect <= ratio * other

    hours = masks @ ctl["analyst_hours"].to_numpy(dtype=float)
    need_mssp = hours > pol["inhouse_hours"]
    if pol.get("mssp"):
        local &= hours <= np.where(need_mssp, pol["mssp"]["hours_capacity"], pol["inhouse_hours"])
    else:
        local &= ~need_mssp
    cost = masks @ ctl["annual_cost"].to_numpy(dtype=float)
    for p, spec in pol.get("platforms", {}).items():
        cost += masks[:, (ctl["platform"] == p).to_numpy()].any(axis=1) * spec["annual_fee"]
    if pol.get("mssp"):
        cost += need_mssp * pol["mssp"]["annual_fee"]
    return {"cost": cost, "local": local, "policy": policy}


def _reduction(inst: Instance, masks: np.ndarray) -> np.ndarray:
    """Exact multiplicative risk reduction of each subset."""
    ale = inst.scenarios["ale"].to_numpy(dtype=float)
    log_keep = np.log1p(-inst.effects.to_numpy())          # log(1 - e), controls x scenarios
    return (ale - np.exp(masks @ log_keep) * ale).sum(axis=1)


def feasible_subsets(inst: Instance) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Every subset (2^n) that meets all policy rules, budget aside: masks, reduction, cost."""
    n = len(inst.controls)
    masks = ((np.arange(2 ** n)[:, None] >> np.arange(n)) & 1).astype(bool)
    a = _assess(inst, masks)
    ok = a["local"] & np.logical_and.reduce(list(a["policy"].values()))
    return masks[ok], _reduction(inst, masks[ok]), a["cost"][ok]


def brute_force_max_reduction(inst: Instance, budget: float) -> tuple[float, list]:
    """
    Enumerate every subset of controls, keep those that meet the rules and the budget, and
    return the best risk reduction from the exact product. An independent check of the
    model encoding.
    """
    masks, reduction, cost = feasible_subsets(inst)
    within = cost <= budget + 1e-6
    if not within.any():
        return float("nan"), []
    best = int(np.argmax(np.where(within, reduction, -np.inf)))
    return float(reduction[best]), list(inst.controls.index[masks[best]])


def greedy_portfolio(inst: Instance, budget: float) -> dict:
    """
    The "bang for the buck" baseline: start from the mandated controls and keep adding the
    control with the most risk reduction per extra dollar (fees included) that still fits the
    budget, conflicts, prerequisites and hours. Coverage and the Protect ratio aren't
    enforced; `broken` lists the rules the result violates.
    """
    ctl = inst.controls
    chosen = np.isin(ctl.index, inst.policy.get("mandatory", []))
    spend = float(_assess(inst, chosen[None, :])["cost"][0])
    gain = float(_reduction(inst, chosen[None, :])[0])
    while True:
        new = np.flatnonzero(~chosen)
        cand = np.repeat(chosen[None, :], len(new), axis=0)
        cand[np.arange(len(new)), new] = True
        a = _assess(inst, cand)
        extra = _reduction(inst, cand) - gain
        ok = a["local"] & (a["cost"] <= budget + 1e-6) & (extra > 1e-9)
        if not ok.any():
            break
        ratio = np.where(ok, extra / np.maximum(a["cost"] - spend, 1e-9), -np.inf)
        pick = int(np.argmax(ratio))
        chosen = cand[pick]
        spend, gain = float(a["cost"][pick]), gain + float(extra[pick])
    final = _assess(inst, chosen[None, :])["policy"]
    return {"controls": list(ctl.index[chosen]), "spend": spend, "reduction": gain,
            "broken": [rule for rule, ok in final.items() if not ok[0]]}


def _triangular(rng: np.random.Generator, lo: np.ndarray, mode: np.ndarray,
                hi: np.ndarray) -> np.ndarray:
    """Inverse-CDF triangular draws; a zero-width range returns its mode."""
    u = rng.random(mode.shape)
    width = hi - lo
    c = np.divide(mode - lo, width, out=np.zeros_like(width), where=width > 0)
    left = lo + np.sqrt(u * width * (mode - lo))
    right = hi - np.sqrt((1 - u) * width * (hi - mode))
    return np.where(width > 0, np.where(u < c, left, right), mode)


def effect_uncertainty(inst: Instance, budget: float, draws: int = 200,
                       seed: int = 2026) -> dict:
    """
    Re-optimize with every effect drawn from its triangular range (reduction_min,
    reduction, reduction_max). Returns how often each control is chosen and the regret of
    the nominal portfolio: the risk reduction it gives up against that draw's best portfolio.
    """
    if inst.effect_min is None:
        raise ValueError("control_effects.csv has no reduction_min/reduction_max columns")
    rng = np.random.default_rng(seed)
    nominal = selected_controls(inst, optimal_portfolio(inst, budget))
    lo, mode, hi = (t.to_numpy() for t in (inst.effect_min, inst.effects, inst.effect_max))
    counts = pd.Series(0, index=inst.controls.index)
    regret, same = [], 0
    for _ in range(draws):
        effects = pd.DataFrame(_triangular(rng, lo, mode, hi), index=inst.effects.index,
                               columns=inst.effects.columns)
        alt = replace(inst, effects=effects)
        best = summarize(alt, optimal_portfolio(alt, budget))
        counts[best["controls"]] += 1
        kept = alt.total_ale - residual_ale(alt, nominal).sum()
        regret.append(max(0.0, best["reduction"] - kept))   # clip solver tolerance
        same += set(best["controls"]) == set(nominal)
    return {"nominal": nominal, "frequency": counts / draws, "regret": np.array(regret),
            "share_nominal_optimal": same / draws, "draws": draws}
