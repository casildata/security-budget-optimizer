"""Mixed-integer model for choosing security controls, built in a solver-neutral form.

Decision variables
    x[c]     1 if control c is funded                                  (binary)
    y[p]     1 if the shared platform fee for p is paid                (binary)
    z        1 if a managed security service provider (MSSP) is hired  (binary)
    w[s,k]   residual annual loss expectancy (ALE) of scenario s after the first k
             controls that affect it                                   (continuous)
    u[s,k]   w[s,k-1] * x[c_k], linearized                             (continuous)

Controls overlap multiplicatively, so each one removes a share of the loss that is
still left:
    residual[s] = ALE[s] * prod_c (1 - e[c,s] * x[c])
The product is built as a chain  w[s,k] = w[s,k-1] - e[c_k,s] * u[s,k]  where
u[s,k] = w[s,k-1] * x[c_k]. A continuous-times-binary product is linearized exactly
with four McCormick (big-M) inequalities, M = ALE[s]:
    u <= M x,   u <= w_prev,   u >= w_prev - M (1 - x),   u >= 0
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .data import CSF_FUNCTIONS, Instance

OBJECTIVES = ("max_reduction", "max_net_benefit", "min_cost")

# Money enters the model in thousands of dollars. With raw dollars the big-M
# coefficients (up to ~380,000) sit next to effects as small as 0.03, and HiGHS
# reported some feasible budgets as infeasible. Solutions are converted back to dollars.
MONEY_SCALE = 1e-3


@dataclass
class LinearModel:
    """Variables and constraints stored by name; converted to arrays for a solver."""

    sense: str = "max"
    names: list = field(default_factory=list)
    lb: list = field(default_factory=list)
    ub: list = field(default_factory=list)
    integer: list = field(default_factory=list)
    objective: dict = field(default_factory=dict)
    constraints: list = field(default_factory=list)  # (name, {var: coef}, lo, hi)
    objective_offset: float = 0.0
    money_scale: float = 1.0  # objective value in dollars = solver value / money_scale
    residual_vars: dict = field(default_factory=dict)  # scenario -> var name (or None)

    def add_var(self, name, lb=0.0, ub=math.inf, integer=False):
        self.names.append(name)
        self.lb.append(lb)
        self.ub.append(ub)
        self.integer.append(integer)
        return name

    def add_binary(self, name):
        return self.add_var(name, 0.0, 1.0, integer=True)

    def add_constraint(self, name, coeffs, lo=-math.inf, hi=math.inf):
        coeffs = {v: c for v, c in coeffs.items() if c != 0}
        self.constraints.append((name, coeffs, lo, hi))

    def matrix(self):
        index = {n: i for i, n in enumerate(self.names)}
        A = np.zeros((len(self.constraints), len(self.names)))
        for row, (_, coeffs, _, _) in enumerate(self.constraints):
            for var, coef in coeffs.items():
                A[row, index[var]] += coef
        lo = np.array([c[2] for c in self.constraints], dtype=float)
        hi = np.array([c[3] for c in self.constraints], dtype=float)
        c = np.array([self.objective.get(n, 0.0) for n in self.names])
        return c, A, lo, hi


def cost_terms(inst: Instance, scale: float = 1.0) -> dict:
    terms = {f"x[{c}]": float(v) * scale for c, v in inst.controls["annual_cost"].items()}
    for p, spec in inst.policy.get("platforms", {}).items():
        terms[f"y[{p}]"] = float(spec["annual_fee"]) * scale
    if inst.policy.get("mssp"):
        terms["z"] = float(inst.policy["mssp"]["annual_fee"]) * scale
    return terms


def build_model(inst: Instance, objective: str = "max_reduction", budget: float | None = None,
                target_share: float | None = None, enforce_mandatory: bool = True) -> LinearModel:
    """
    objective
        max_reduction    maximize ALE removed, subject to `budget`
        max_net_benefit  maximize ALE removed minus spend (budget optional)
        min_cost         minimize spend to remove at least `target_share` of total ALE
    """
    if objective not in OBJECTIVES:
        raise ValueError(f"objective must be one of {OBJECTIVES}")
    if objective == "max_reduction" and budget is None:
        budget = inst.policy["budget"]
    if objective == "min_cost" and target_share is None:
        raise ValueError("min_cost needs target_share")

    pol, ctl = inst.policy, inst.controls
    m = LinearModel(sense="min" if objective == "min_cost" else "max", money_scale=MONEY_SCALE)
    x = {c: m.add_binary(f"x[{c}]") for c in ctl.index}
    y = {p: m.add_binary(f"y[{p}]") for p in pol.get("platforms", {})}
    z = m.add_binary("z") if pol.get("mssp") else None
    residual = {}
    for s, ale in inst.scenarios["ale"].items():
        ale = float(ale) * MONEY_SCALE
        w_prev = None  # None stands for the constant ALE[s]
        for k, c in enumerate(c for c in ctl.index if inst.effects.at[c, s] > 0):
            e = float(inst.effects.at[c, s])
            u = m.add_var(f"u[{s},{k}]", 0.0, ale)
            w = m.add_var(f"w[{s},{k}]", 0.0, ale)
            prev = {} if w_prev is None else {w_prev: 1.0}
            prev_const = ale if w_prev is None else 0.0
            # w = w_prev - e * u
            m.add_constraint(f"chain[{s},{k}]", {w: 1.0, u: e, **{v: -a for v, a in prev.items()}},
                             lo=prev_const, hi=prev_const)
            # McCormick envelope for u = w_prev * x[c]
            m.add_constraint(f"mc_x[{s},{k}]", {u: 1.0, x[c]: -ale}, hi=0.0)
            m.add_constraint(f"mc_w[{s},{k}]", {u: 1.0, **{v: -a for v, a in prev.items()}},
                             hi=prev_const)
            m.add_constraint(f"mc_lo[{s},{k}]", {u: 1.0, x[c]: -ale,
                                                 **{v: -a for v, a in prev.items()}},
                             lo=prev_const - ale)
            w_prev = w
        residual[s] = w_prev  # None if no control affects this scenario

    spend = cost_terms(inst, MONEY_SCALE)
    if budget is not None:
        m.add_constraint("budget", spend, hi=float(budget) * MONEY_SCALE)

    # Analyst capacity, either/or with big-M (as in a two-plant production choice):
    #   z = 0: hours <= in-house capacity        z = 1: hours <= MSSP capacity
    hours = {x[c]: float(h) for c, h in ctl["analyst_hours"].items()}
    if z is not None:
        big_m = float(ctl["analyst_hours"].clip(lower=0).sum())
        m.add_constraint("hours_inhouse", {**hours, z: -big_m}, hi=pol["inhouse_hours"])
        m.add_constraint("hours_mssp", {**hours, z: big_m},
                         hi=pol["mssp"]["hours_capacity"] + big_m)
    else:
        m.add_constraint("hours_inhouse", hours, hi=pol["inhouse_hours"])

    # Platform-bundled controls require the platform fee (fixed charge)
    for c, p in ctl["platform"].items():
        if p:
            m.add_constraint(f"platform[{c}]", {x[c]: 1.0, y[p]: -1.0}, hi=0.0)

    # At most one control from each mutually exclusive group (e.g. one EDR product)
    for g, members in ctl[ctl["exclusive_group"] != ""].groupby("exclusive_group").groups.items():
        m.add_constraint(f"exclusive[{g}]", {x[c]: 1.0 for c in members}, hi=1.0)

    # Prerequisites: if c then requires[c]
    for c, req in ctl["requires"].items():
        if req:
            m.add_constraint(f"requires[{c}->{req}]", {x[c]: 1.0, x[req]: -1.0}, hi=0.0)

    # Mandated controls (compliance / cyber-insurance)
    if enforce_mandatory:
        for c in pol.get("mandatory", []):
            m.add_constraint(f"mandatory[{c}]", {x[c]: 1.0}, lo=1.0, hi=1.0)

    # Coverage: at least k controls in each NIST CSF function
    k = pol.get("min_per_function", 0)
    if k:
        for fn in CSF_FUNCTIONS:
            members = ctl.index[ctl["csf_function"] == fn]
            m.add_constraint(f"coverage[{fn}]", {x[c]: 1.0 for c in members}, lo=float(k))

    # Defense in depth: Protect controls <= ratio x (Detect + Respond + Recover controls)
    ratio = pol.get("max_protect_ratio")
    if ratio:
        coeffs = {}
        for c, fn in ctl["csf_function"].items():
            if fn == "Protect":
                coeffs[x[c]] = 1.0
            elif fn in ("Detect", "Respond", "Recover"):
                coeffs[x[c]] = -float(ratio)
        m.add_constraint("protect_ratio", coeffs, hi=0.0)

    # Reduction = total ALE - sum of residuals; the constant is added back by `objective_offset`
    neg_residual = {w: -1.0 for w in residual.values() if w is not None}
    total = inst.total_ale * MONEY_SCALE
    uncovered = sum(float(inst.scenarios.at[s, "ale"]) * MONEY_SCALE
                    for s, w in residual.items() if w is None)
    if objective == "max_reduction":
        m.objective = neg_residual
        m.objective_offset = total - uncovered
    elif objective == "max_net_benefit":
        m.objective = {**neg_residual, **{v: -c for v, c in spend.items()}}
        m.objective_offset = total - uncovered
    else:
        # total ALE - sum(residual) >= target  <=>  sum(residual) <= total ALE - target
        m.add_constraint("target", {w: 1.0 for w in neg_residual},
                         hi=total - uncovered - target_share * total)
        m.objective = spend
    m.residual_vars = residual
    return m
