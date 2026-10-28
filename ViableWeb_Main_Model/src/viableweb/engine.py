from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from .certificates import build_certificate, static_certificate_ok
from .models import Action, Evidence, Instance, Leaf, Problem, TraceResult


@dataclass
class ReplayState:
    facts: dict[str, Any]
    evidence: dict[str, Evidence]
    capacity: dict[str, int]
    pending: dict[str, str] = field(default_factory=dict)
    completed: set[str] = field(default_factory=set)
    used_cost: float = 0.0
    instances: list[Instance] = field(default_factory=list)
    last_trigger: dict[str, bool] = field(default_factory=dict)


def acquired_capabilities(problem: Problem, portfolio: tuple[str, ...]) -> tuple[dict[str, int], float, str | None]:
    root_time = min(n.time for n in problem.nodes.values())
    initial = dict(problem.initial_facts)
    selected = set(portfolio)
    owned = {c.id for c in problem.capabilities.values() if c.initial_owned}
    if len(selected) != len(portfolio) or selected.intersection(owned):
        return {}, 0.0, "duplicate or initially owned capability in acquisition portfolio"
    charge = 0.0
    for k in selected:
        if k not in problem.capabilities:
            return {}, 0.0, f"unknown capability {k}"
        c = problem.capabilities[k]
        if not c.acquirable or not _cap_evidence(problem, c.id, problem.initial_evidence, root_time) or c.authority not in problem.contract.authorities:
            return {}, 0.0, f"acquisition not authorized or confirmed: {k}"
        if not _matches(initial, c.preconditions):
            return {}, 0.0, f"acquisition preconditions not met: {k}"
        charge += c.acquisition_cost
    if charge > problem.contract.budget + problem.solver.epsilon:
        return {}, charge, "acquisition exceeds episode budget"
    for k in owned:
        c = problem.capabilities[k]
        if c.authority not in problem.contract.authorities or not _cap_evidence(problem, k, problem.initial_evidence, root_time):
            return {}, charge, f"owned capability lacks confirmed authorization: {k}"
    return {k: problem.capabilities[k].capacity for k in selected.union(owned)}, charge, None


def _matches(facts: dict[str, Any], conditions: dict[str, Any]) -> bool:
    return all(k in facts and facts[k] == v for k, v in conditions.items())


def _cap_evidence(problem: Problem, cap_id: str, evidence: dict[str, Evidence], now: float) -> bool:
    c = problem.capabilities[cap_id]
    record = evidence.get(c.evidence_key)
    return record is not None and now < c.expires_at and record.valid(now, issuer=c.service, scope=c.scope)


def _obligation_checks(problem: Problem, state: ReplayState, now: float) -> None:
    for o in problem.contract.obligations:
        current = bool(state.facts.get(o.trigger_fact, False))
        if current and not state.last_trigger.get(o.id, False):
            state.instances.append(Instance(obligation=o.id, activated_at=now, deadline=o.deadline))
        state.last_trigger[o.id] = current
    obs = {o.id: o for o in problem.contract.obligations}
    for inst in state.instances:
        if inst.fulfilled_at is not None or now < inst.activated_at or now > inst.deadline:
            continue
        o = obs[inst.obligation]
        record = state.evidence.get(o.evidence_key)
        if state.facts.get(o.fact) is True and record is not None and record.valid(now, issuer=o.evidence_issuer, scope=o.evidence_scope, sources=o.evidence_sources, min_time=inst.activated_at):
            inst.fulfilled_at = now


def _postcondition_verified(problem: Problem, state: ReplayState, action: Action, now: float) -> bool:
    if not _matches(state.facts, action.postconditions):
        return False
    for o in problem.contract.obligations:
        if o.fact not in action.postconditions:
            continue
        activated = [i.activated_at for i in state.instances if i.obligation == o.id and i.activated_at <= now]
        if not activated:
            continue
        evidence = state.evidence.get(o.evidence_key)
        if evidence is None or not evidence.valid(now, issuer=o.evidence_issuer, scope=o.evidence_scope, sources=o.evidence_sources, min_time=max(activated)):
            return False
    return True


def _safety_ok(problem: Problem, state: ReplayState) -> bool:
    return _matches(state.facts, problem.contract.safety)


def _apply_result(state: ReplayState, facts: dict[str, Any], evidence: dict[str, Evidence]) -> None:
    state.facts.update(facts)
    state.evidence.update(evidence)


def _handle_event(state: ReplayState, event: Any) -> None:
    _apply_result(state, event.facts, event.evidence)
    for key, result in event.resolve.items():
        if key not in state.pending:
            continue
        status = result["status"]
        if status == "verified":
            _apply_result(state, dict(result.get("facts", {})), {str(k): Evidence.parse(str(k), v) for k, v in result.get("evidence", {}).items()})
            state.completed.add(key)
            state.pending.pop(key)
        elif status == "refuted":
            state.pending.pop(key)


def _action_guard(problem: Problem, state: ReplayState, action: Action, node_id: str, now: float, total_acq: float) -> str | None:
    cert = build_certificate(problem, problem.nodes[node_id], action)
    if not static_certificate_ok(cert, problem.solver.epsilon):
        return f"temporal certificate margin is negative: {action.id}"
    if now > cert.deadline or action.authority not in problem.contract.authorities:
        return f"deadline or authority guard failed: {action.id}"
    if not _matches(state.facts, action.preconditions):
        return f"action preconditions not supported: {action.id}"
    if action.conflict_key in state.pending or action.conflict_key in state.completed:
        return f"pending or completed conflicting write: {action.conflict_key}"
    for key, need in action.requires.items():
        cap = problem.capabilities[key]
        if state.capacity.get(key, 0) < need or cap.authority not in problem.contract.authorities or not _cap_evidence(problem, key, state.evidence, now):
            return f"capacity, expiry, authority or evidence gate failed: {key}"
    if total_acq + state.used_cost + action.cost > problem.contract.budget + problem.solver.epsilon:
        return f"episode budget exceeded by action {action.id}"
    return None


def run_leaf(problem: Problem, portfolio: tuple[str, ...], policy: dict[str, str | None], leaf: Leaf, include_log: bool = True) -> TraceResult:
    capacity, acq, err = acquired_capabilities(problem, portfolio)
    if err:
        return TraceResult(leaf.id, False, False, 0.0, acq, err, [], [], [])
    state = ReplayState(facts=deepcopy(problem.initial_facts), evidence=dict(problem.initial_evidence), capacity=capacity)
    actions: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    failure: str | None = None
    for index, node_id in enumerate(leaf.path):
        node = problem.nodes[node_id]
        event = leaf.events.get(node_id)
        if event is not None:
            _handle_event(state, event)
        _obligation_checks(problem, state, node.time)
        if include_log:
            events.append({"time": node.time, "node": node.id, "observation": node.observation, "facts": deepcopy(state.facts)})
        if not _safety_ok(problem, state):
            failure = f"hard safety invariant violated at {node_id}"
            break
        action_id = policy.get(node_id)
        if action_id is not None:
            if action_id not in node.actions:
                failure = f"action {action_id} not available at observation {node_id}"
                break
            action = problem.actions[action_id]
            if index + 1 < len(leaf.path) and node.time + action.duration > problem.nodes[leaf.path[index + 1]].time + problem.solver.epsilon:
                failure = f"action {action_id} overlaps a subsequent observation"
                break
            failure = _action_guard(problem, state, action, node_id, node.time, acq)
            if failure:
                break
            for key, units in action.requires.items():
                state.capacity[key] -= units
            state.used_cost += action.cost
            outcome = leaf.responses.get(action_id)
            status = "unknown" if outcome is None else outcome.status
            postcondition_verified = False
            if status == "verified" and outcome is not None:
                if any(e.observed_at > node.time + action.duration + problem.solver.epsilon for e in outcome.evidence.values()):
                    failure = f"future evidence used before observation: {action.id}"
                    break
                _apply_result(state, outcome.facts, outcome.evidence)
                postcondition_verified = _postcondition_verified(problem, state, action, node.time + action.duration)
                if postcondition_verified:
                    state.completed.add(action.conflict_key)
                else:
                    state.pending[action.conflict_key] = action.id
            elif status in {"pending", "unknown"}:
                state.pending[action.conflict_key] = action.id
            _obligation_checks(problem, state, node.time + action.duration)
            if include_log:
                actions.append({"node": node_id, "action": action.id, "time": node.time, "status": status, "postcondition_verified": postcondition_verified, "used_cost": state.used_cost, "pending": sorted(state.pending)})
            if not _safety_ok(problem, state):
                failure = f"hard safety invariant violated after action {action_id}"
                break
    instances = [{"obligation": x.obligation, "activated_at": x.activated_at, "deadline": x.deadline, "fulfilled_at": x.fulfilled_at} for x in state.instances]
    fulfilled = failure is None and all(i.fulfilled_at is not None and i.fulfilled_at <= i.deadline for i in state.instances)
    return TraceResult(leaf=leaf.id, admissible=failure is None, fulfilled=fulfilled, used_cost=state.used_cost, total_cost=acq + state.used_cost, failure=failure, obligations=instances, actions=actions, events=events)
