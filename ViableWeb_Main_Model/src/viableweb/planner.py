from __future__ import annotations

from dataclasses import replace
from itertools import combinations, product
from math import prod

from .certificates import build_certificate
from .engine import acquired_capabilities, run_leaf
from .models import Distribution, ModelError, Plan, Problem


def _portfolios(problem: Problem):
    candidates = sorted(c.id for c in problem.capabilities.values() if c.acquirable and not c.initial_owned)
    if 2 ** len(candidates) > problem.solver.max_portfolios:
        raise ModelError("exact portfolio enumeration exceeds solver.max_portfolios; increase limit or reduce capabilities")
    for n in range(len(candidates) + 1):
        for values in combinations(candidates, n):
            inventory, cost, reason = acquired_capabilities(problem, values)
            if reason is None:
                yield values, cost


def _policy_space(problem: Problem, locked: dict[str, str | None] | None = None):
    locked = {} if locked is None else locked
    nodes = [n for n in problem.ordered_nodes() if n.actions and any(n.id in leaf.path for leaf in problem.leaves.values())]
    options = [(locked[n.id],) if n.id in locked else (None, *n.actions) for n in nodes]
    candidates = prod(len(opts) for opts in options)
    if candidates > problem.solver.max_policies:
        raise ModelError("exact policy enumeration exceeds solver.max_policies; increase limit or reduce branching")
    if not nodes:
        yield {}
        return
    for choices in product(*options):
        yield {node.id: selected for node, selected in zip(nodes, choices)}


def _evaluate(problem: Problem, portfolio: tuple[str, ...], acquisition_cost: float, policy: dict[str, str | None]):
    traces = {key: run_leaf(problem, portfolio, policy, leaf, include_log=False) for key, leaf in problem.leaves.items()}
    if any(not trace.admissible for trace in traces.values()):
        return None
    utilities = {d.id: sum(prob * (float(traces[lid].fulfilled) - problem.solver.usage_penalty * traces[lid].used_cost) for lid, prob in d.probabilities.items()) for d in problem.distributions}
    worst = min(utilities.values())
    score = worst - problem.solver.acquisition_penalty * acquisition_cost
    return score, worst, utilities, traces


def solve(problem: Problem, fixed_portfolio: tuple[str, ...] | None = None, locked_policy: dict[str, str | None] | None = None) -> Plan:
    choices = list(_policy_space(problem, locked_policy))
    best = None
    best_key = None
    count_portfolios = 0
    count_policies = 0
    if fixed_portfolio is None:
        portfolio_options = _portfolios(problem)
    else:
        _, cost, reason = acquired_capabilities(problem, fixed_portfolio)
        if reason is not None:
            raise ModelError(reason)
        portfolio_options = [(fixed_portfolio, cost)]
    for portfolio, acq in portfolio_options:
        count_portfolios += 1
        for policy in choices:
            count_policies += 1
            result = _evaluate(problem, portfolio, acq, policy)
            if result is None:
                continue
            score, worst, utilities, traces = result
            tiebreak = (round(score / problem.solver.epsilon) * problem.solver.epsilon, -acq, -sum(a is not None for a in policy.values()), tuple(portfolio), tuple(sorted((k, v or "") for k, v in policy.items())))
            if best is None or tiebreak > best_key:
                best = (portfolio, dict(policy), score, worst, utilities, acq, traces)
                best_key = tiebreak
    if best is None:
        raise ModelError("no admissible portfolio and policy satisfy hard constraints")
    portfolio, policy, score, worst, utilities, acq, traces = best
    certs = {node_id: build_certificate(problem, problem.nodes[node_id], problem.actions[action_id]) for node_id, action_id in policy.items() if action_id is not None}
    return Plan(portfolio=portfolio, policy=policy, objective=score, worst_case_utility=worst, distribution_utilities=utilities, acquisition_cost=acq, traces=traces, certificates=certs, portfolios_evaluated=count_portfolios, policies_evaluated=count_policies)


def portfolio_fulfillment(problem: Problem, portfolio: tuple[str, ...]) -> float:
    choices = _policy_space(problem)
    best = float("-inf")
    acq = sum(problem.capabilities[k].acquisition_cost for k in portfolio)
    for policy in choices:
        result = _evaluate(problem, portfolio, acq, policy)
        if result is None:
            continue
        _, _, _, traces = result
        mass = min(sum(prob * float(traces[lid].fulfilled) for lid, prob in d.probabilities.items()) for d in problem.distributions)
        best = max(best, mass)
    if best == float("-inf"):
        raise ModelError("portfolio has no admissible policy")
    return best


def pairwise_interaction(problem: Problem, left: str, right: str, base: tuple[str, ...] = ()) -> float:
    base_set = set(base)
    if left == right or left in base_set or right in base_set:
        raise ModelError("interaction rights must be distinct and absent from base")
    cache = {}
    for additions in ((), (left,), (right,), (left, right)):
        chosen = tuple(sorted(base_set.union(additions)))
        cache[additions] = portfolio_fulfillment(problem, chosen)
    return cache[(left, right)] - cache[(left,)] - cache[(right,)] + cache[()]


def replan(problem: Problem, previous: Plan, observed_node: str) -> Plan:
    if observed_node not in problem.nodes:
        raise ModelError(f"unrecognized observed node: {observed_node}")
    retained = {key: leaf for key, leaf in problem.leaves.items() if observed_node in leaf.path}
    if not retained:
        raise ModelError("observed history is outside modeled scenarios; escalation or new scenario compilation required")
    distributions = []
    for d in problem.distributions:
        mass = sum(d.probabilities[lid] for lid in retained)
        if mass > problem.solver.epsilon:
            distributions.append(Distribution(d.id, {lid: d.probabilities[lid] / mass for lid in retained}))
    if not distributions:
        raise ModelError("the observation has zero mass in every uncertainty model")
    chosen_path = retained[next(iter(retained))].path
    ancestors = chosen_path[:chosen_path.index(observed_node)]
    locked = {n: previous.policy.get(n) for n in ancestors if problem.nodes[n].actions}
    conditional = replace(problem, leaves=retained, distributions=tuple(distributions))
    return solve(conditional, fixed_portfolio=previous.portfolio, locked_policy=locked)
