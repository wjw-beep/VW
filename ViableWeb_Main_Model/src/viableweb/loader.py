from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import Action, Capability, Contract, Distribution, Event, Evidence, Leaf, ModelError, Node, Obligation, Outcome, Problem, SolverSettings


def _mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ModelError(f"{location} must be a mapping")
    return value


def _list(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise ModelError(f"{location} must be a list")
    return value


def _entries(items: list[dict[str, Any]], constructor: Any, kind: str) -> dict[str, Any]:
    output = {}
    for item in items:
        item = _mapping(item, kind)
        obj = constructor(item)
        if obj.id in output:
            raise ModelError(f"duplicate {kind} {obj.id}")
        output[obj.id] = obj
    return output


def _flag(raw: dict[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ModelError(f"{key} must be a YAML boolean")
    return value


def _evidences(raw: dict[str, Any]) -> dict[str, Evidence]:
    return {str(k): Evidence.parse(str(k), _mapping(v, f"evidence {k}")) for k, v in _mapping(raw, "evidence").items()}


def _event(raw: dict[str, Any]) -> Event:
    return Event(facts=dict(raw.get("facts", {})), evidence=_evidences(raw.get("evidence", {})), resolve=dict(raw.get("resolve", {})))


def _outcome(raw: dict[str, Any]) -> Outcome:
    return Outcome(status=str(raw["status"]), facts=dict(raw.get("facts", {})), evidence=_evidences(raw.get("evidence", {})))


def load_problem(path: str | Path) -> Problem:
    with Path(path).open("r", encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    raw = _mapping(raw, "root")
    cr = _mapping(raw["contract"], "contract")
    contract = Contract(budget=float(cr["budget"]), authorities=frozenset(map(str, cr["authorities"])), safety=dict(cr.get("safety", {})), obligations=tuple(Obligation(id=str(o["id"]), trigger_fact=str(o["trigger_fact"]), fact=str(o["fact"]), deadline=float(o["deadline"]), evidence_key=str(o["evidence_key"]), evidence_issuer=str(o["evidence_issuer"]), evidence_scope=str(o["evidence_scope"]), evidence_sources=tuple(map(str, o["evidence_sources"]))) for o in _list(cr["obligations"], "obligations")))
    capabilities = _entries(_list(raw["capabilities"], "capabilities"), lambda c: Capability(id=str(c["id"]), service=str(c["service"]), scope=str(c["scope"]), acquisition_cost=float(c["acquisition_cost"]), capacity=int(c["capacity"]), expires_at=float(c["expires_at"]), authority=str(c["authority"]), evidence_key=str(c["evidence_key"]), initial_owned=_flag(c, "initial_owned", False), acquirable=_flag(c, "acquirable", True), preconditions=dict(c.get("preconditions", {}))), "capability")
    actions = _entries(_list(raw["actions"], "actions"), lambda a: Action(id=str(a["id"]), service=str(a["service"]), scope=str(a["scope"]), authority=str(a["authority"]), cost=float(a["cost"]), duration=float(a["duration"]), requires={str(k): int(v) for k, v in a.get("requires", {}).items()}, preconditions=dict(a.get("preconditions", {})), postconditions=dict(a.get("postconditions", {})), conflict_key=str(a["conflict_key"])), "action")
    nodes = _entries(_list(raw["nodes"], "nodes"), lambda n: Node(id=str(n["id"]), parent=None if n.get("parent") is None else str(n["parent"]), time=float(n["time"]), observation=str(n["observation"]), actions=tuple(map(str, n.get("actions", [])))), "node")
    leaves = _entries(_list(raw["leaves"], "leaves"), lambda l: Leaf(id=str(l["id"]), path=tuple(map(str, l["path"])), events={str(k): _event(v) for k, v in l.get("events", {}).items()}, responses={str(k): _outcome(v) for k, v in l.get("responses", {}).items()}), "leaf")
    distributions = tuple(Distribution(id=str(d["id"]), probabilities={str(k): float(v) for k, v in d["probabilities"].items()}) for d in _list(raw["distributions"], "distributions"))
    sr = raw["solver"]
    settings = SolverSettings(acquisition_penalty=float(sr["acquisition_penalty"]), usage_penalty=float(sr["usage_penalty"]), max_portfolios=int(sr["max_portfolios"]), max_policies=int(sr["max_policies"]), epsilon=float(sr["epsilon"]))
    problem = Problem(name=str(raw["name"]), initial_facts=dict(raw["initial_facts"]), initial_evidence=_evidences(raw["initial_evidence"]), contract=contract, capabilities=capabilities, actions=actions, nodes=nodes, leaves=leaves, distributions=distributions, solver=settings)
    validate_problem(problem)
    return problem


def validate_problem(p: Problem) -> None:
    if p.contract.budget < 0 or p.solver.max_portfolios < 1 or p.solver.max_policies < 1 or p.solver.epsilon <= 0 or p.solver.usage_penalty < 0 or p.solver.acquisition_penalty < 0:
        raise ModelError("invalid budget or solver settings")
    if not p.nodes or not p.leaves or not p.distributions:
        raise ModelError("nodes, leaves, and distributions must be nonempty")
    roots = [n for n in p.nodes.values() if n.parent is None]
    if len(roots) != 1:
        raise ModelError("exactly one root node is required")
    for n in p.nodes.values():
        if n.parent is not None and (n.parent not in p.nodes or n.time <= p.nodes[n.parent].time):
            raise ModelError(f"invalid node parent or non-increasing time: {n.id}")
        if len(n.actions) != len(set(n.actions)) or any(a not in p.actions for a in n.actions):
            raise ModelError(f"invalid actions for node {n.id}")
    for a in p.actions.values():
        if a.cost < 0 or a.duration < 0 or not a.conflict_key or not a.requires:
            raise ModelError(f"invalid action parameters {a.id}")
        for k, units in a.requires.items():
            if k not in p.capabilities or units < 1:
                raise ModelError(f"invalid capability requirement {a.id}:{k}")
        if not any(p.capabilities[k].service == a.service and p.capabilities[k].scope == a.scope for k in a.requires):
            raise ModelError(f"action lacks issuing-service operation right: {a.id}")
    for c in p.capabilities.values():
        if c.acquisition_cost < 0 or c.capacity < 1 or c.expires_at <= 0:
            raise ModelError(f"invalid capability {c.id}")
        if c.initial_owned and c.acquirable:
            raise ModelError(f"owned capability must not also be purchasable: {c.id}")
        if c.evidence_key not in p.initial_evidence:
            raise ModelError(f"missing provider evidence for {c.id}")
    obligation_ids = [o.id for o in p.contract.obligations]
    if len(set(obligation_ids)) != len(obligation_ids):
        raise ModelError("duplicate obligation")
    for o in p.contract.obligations:
        if o.deadline < roots[0].time or not o.evidence_sources:
            raise ModelError(f"invalid obligation {o.id}")
    for l in p.leaves.values():
        if not l.path or l.path[0] != roots[0].id or any(n not in p.nodes for n in l.path):
            raise ModelError(f"invalid path in leaf {l.id}")
        for i, nid in enumerate(l.path):
            if i > 0 and p.nodes[nid].parent != l.path[i - 1]:
                raise ModelError(f"leaf {l.id} violates tree path")
        if any(n not in l.path for n in l.events):
            raise ModelError(f"event references off-path node in {l.id}")
        if any(a not in p.actions for a in l.responses):
            raise ModelError(f"response references unknown action in {l.id}")
        for r in l.responses.values():
            if r.status not in {"verified", "pending", "unknown", "refuted"}:
                raise ModelError(f"invalid response status in {l.id}")
        for event in l.events.values():
            if any(r.get("status") not in {"verified", "refuted", "pending", "unknown"} for r in event.resolve.values()):
                raise ModelError(f"invalid resolution in {l.id}")
    for d in p.distributions:
        if set(d.probabilities) != set(p.leaves) or any(v < 0 for v in d.probabilities.values()) or abs(sum(d.probabilities.values()) - 1.0) > p.solver.epsilon:
            raise ModelError(f"distribution {d.id} does not sum to one over all leaves")
    if len({d.id for d in p.distributions}) != len(p.distributions):
        raise ModelError("duplicate distribution")
    for evidence in list(p.initial_evidence.values()) + [e for leaf in p.leaves.values() for event in leaf.events.values() for e in event.evidence.values()] + [e for leaf in p.leaves.values() for outcome in leaf.responses.values() for e in outcome.evidence.values()]:
        if evidence.status not in {"verified", "refuted", "pending", "unknown"} or evidence.observed_at >= evidence.valid_until or not evidence.issuer or not evidence.scope or not evidence.source:
            raise ModelError(f"invalid evidence record: {evidence.key}")
    for leaf in p.leaves.values():
        for node_id, event in leaf.events.items():
            if any(e.observed_at > p.nodes[node_id].time + p.solver.epsilon for e in event.evidence.values()):
                raise ModelError(f"event contains future evidence: {leaf.id}:{node_id}")
            for response in event.resolve.values():
                for e in _evidences(response.get("evidence", {})).values():
                    if e.observed_at > p.nodes[node_id].time + p.solver.epsilon:
                        raise ModelError(f"resolution contains future evidence: {leaf.id}:{node_id}")
