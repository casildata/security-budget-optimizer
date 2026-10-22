"""Solve a LinearModel with SciPy/HiGHS (default) or PuLP/CBC, plus LP-relaxation duals."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, linprog, milp

from .model import LinearModel


@dataclass
class Solution:
    status: str
    objective: float
    values: dict
    duals: dict | None = None

    @property
    def optimal(self) -> bool:
        return self.status == "Optimal"


def solve_scipy(model: LinearModel, time_limit: float = 60.0) -> Solution:
    c, A, lo, hi = model.matrix()
    sign = -1.0 if model.sense == "max" else 1.0
    res = milp(sign * c,
               constraints=LinearConstraint(A, lo, hi),
               bounds=Bounds(model.lb, model.ub),
               integrality=np.array(model.integer, dtype=int),
               options={"time_limit": time_limit, "mip_rel_gap": 1e-9})
    if res.status != 0 or res.x is None:
        return Solution("Infeasible" if res.status == 2 else f"Failed ({res.message})",
                        math.nan, {})
    values = dict(zip(model.names, _clean(res.x, model.integer)))
    objective = (sign * res.fun + model.objective_offset) / model.money_scale
    return Solution("Optimal", objective, values)


def pulp_unavailable() -> str | None:
    """Why the PuLP/CBC backend can't run here, or None if a supported PuLP is installed."""
    try:
        import pulp
    except ImportError:
        return "PuLP not installed"
    major = int(pulp.__version__.split(".")[0])
    if major >= 4:  # 4.0 replaced the LpVariable(name, lowBound=...) API used below
        return f'PuLP {pulp.__version__} not supported; install "pulp>=2.7,<4"'
    return None


def solve_pulp(model: LinearModel) -> Solution:
    """Same model through PuLP's default CBC solver (optional dependency, PuLP 2.7-3.x)."""
    reason = pulp_unavailable()
    if reason:
        raise ImportError(reason)
    import pulp

    prob = pulp.LpProblem("security_budget",
                          pulp.LpMaximize if model.sense == "max" else pulp.LpMinimize)
    v = {}
    for name, lb, ub, is_int in zip(model.names, model.lb, model.ub, model.integer):
        v[name] = pulp.LpVariable(name.replace("[", "_").replace("]", "").replace("->", "_"),
                                  lowBound=lb, upBound=None if math.isinf(ub) else ub,
                                  cat="Integer" if is_int else "Continuous")
    prob += pulp.lpSum(coef * v[n] for n, coef in model.objective.items())
    for i, (name, coeffs, lo, hi) in enumerate(model.constraints):
        expr = pulp.lpSum(coef * v[n] for n, coef in coeffs.items())
        safe = f"c{i}_" + "".join(ch if ch.isalnum() else "_" for ch in name)
        if lo == hi:
            prob += expr == lo, safe
        else:
            if not math.isinf(hi):
                prob += expr <= hi, safe + "_hi"
            if not math.isinf(lo):
                prob += expr >= lo, safe + "_lo"
    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    status = pulp.LpStatus[prob.status]
    if status != "Optimal":
        return Solution(status, math.nan, {})
    values = dict(zip(model.names, _clean([v[n].varValue for n in model.names], model.integer)))
    return Solution("Optimal", (pulp.value(prob.objective) + model.objective_offset) / model.money_scale,
                    values)


def lp_relaxation(model: LinearModel) -> Solution:
    """
    Solve with integrality dropped and return shadow prices: the change in the
    objective per unit increase in each constraint's bound (in model units; money is in
    $ thousands, so a budget dual is still "dollars of objective per extra dollar"). For an integer program
    these are indicative only (marginal values at the fractional LP optimum).
    """
    c, A, lo, hi = model.matrix()
    sign = -1.0 if model.sense == "max" else 1.0
    rows_ub, b_ub, rows_eq, b_eq, labels_ub, labels_eq = [], [], [], [], [], []
    for i, (name, _, l, h) in enumerate(model.constraints):
        if l == h:
            rows_eq.append(A[i]); b_eq.append(h); labels_eq.append(name)
            continue
        if not math.isinf(h):
            rows_ub.append(A[i]); b_ub.append(h); labels_ub.append((name, +1))
        if not math.isinf(l):
            rows_ub.append(-A[i]); b_ub.append(-l); labels_ub.append((name, -1))
    res = linprog(sign * c,
                  A_ub=np.array(rows_ub) if rows_ub else None, b_ub=b_ub or None,
                  A_eq=np.array(rows_eq) if rows_eq else None, b_eq=b_eq or None,
                  bounds=list(zip(model.lb, [None if math.isinf(u) else u for u in model.ub])),
                  method="highs")
    if res.status != 0:
        return Solution("Infeasible", math.nan, {})
    duals = {}
    # HiGHS marginals are d(minimized objective)/d(rhs); convert to the model's sense
    for (name, direction), mval in zip(labels_ub, res.ineqlin.marginals):
        duals[name] = duals.get(name, 0.0) + sign * direction * mval
    for name, mval in zip(labels_eq, res.eqlin.marginals if rows_eq else []):
        duals[name] = sign * mval
    return Solution("Optimal", (sign * res.fun + model.objective_offset) / model.money_scale,
                    dict(zip(model.names, res.x)), duals)


def _clean(xs, integer):
    return [float(round(v)) if is_int else float(v) for v, is_int in zip(xs, integer)]
