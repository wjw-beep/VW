from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ModelError(ValueError):
    pass


@dataclass(frozen=True)
class Evidence:
    key: str
    status: str
    issuer: str
    scope: str
    source: str
    observed_at: float
    valid_until: float

    @classmethod
    def parse(cls, key: str, raw: dict[str, Any]) -> Evidence:
        return cls(key=key, status=str(raw["status"]), issuer=str(raw["issuer"]), scope=str(raw["scope"]), source=str(raw["source"]), observed_at=float(raw["observed_at"]), valid_until=float(raw["valid_until"]))

    def valid(self, now: float, issuer: str | None = None, scope: str | None = None, sources: tuple[str, ...] = (), min_time: float | None = None) -> bool:
        return self.status == "verified" and self.observed_at <= now < self.valid_until and (min_time is None or self.observed_at >= min_time) and (issuer is None or self.issuer == issuer) and (scope is None or self.scope == scope) and (not sources or self.source in sources)


@dataclass(frozen=True)
class Obligation:
    id: str
    trigger_fact: str
    fact: str
    deadline: float
    evidence_key: str
    evidence_issuer: str
    evidence_scope: str
    evidence_sources: tuple[str, ...]


@dataclass(frozen=True)
class Contract:
    budget: float
    authorities: frozenset[str]
    safety: dict[str, Any]
    obligations: tuple[Obligation, ...]


@dataclass(frozen=True)
class Capability:
    id: str
    service: str
    scope: str
    acquisition_cost: float
    capacity: int
    expires_at: float
    authority: str
    evidence_key: str
    initial_owned: bool
    acquirable: bool
    preconditions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Action:
    id: str
    service: str
    scope: str
    authority: str
    cost: float
    duration: float
    requires: dict[str, int]
    preconditions: dict[str, Any]
    postconditions: dict[str, Any]
    conflict_key: str


@dataclass(frozen=True)
class Node:
    id: str
    parent: str | None
    time: float
    observation: str
    actions: tuple[str, ...]


@dataclass(frozen=True)
class Event:
    facts: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    resolve: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class Outcome:
    status: str
    facts: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Evidence] = field(default_factory=dict)


@dataclass(frozen=True)
class Leaf:
    id: str
    path: tuple[str, ...]
    events: dict[str, Event]
    responses: dict[str, Outcome]


@dataclass(frozen=True)
class Distribution:
    id: str
    probabilities: dict[str, float]


@dataclass(frozen=True)
class SolverSettings:
    acquisition_penalty: float
    usage_penalty: float
    max_portfolios: int
    max_policies: int
    epsilon: float


@dataclass(frozen=True)
class Problem:
    name: str
    initial_facts: dict[str, Any]
    initial_evidence: dict[str, Evidence]
    contract: Contract
    capabilities: dict[str, Capability]
    actions: dict[str, Action]
    nodes: dict[str, Node]
    leaves: dict[str, Leaf]
    distributions: tuple[Distribution, ...]
    solver: SolverSettings

    def ordered_nodes(self) -> tuple[Node, ...]:
        return tuple(sorted(self.nodes.values(), key=lambda n: (n.time, n.id)))


@dataclass(frozen=True)
class Certificate:
    node: str
    action: str
    trigger: str
    guards: tuple[str, ...]
    deadline: float
    projected_completion: float
    temporal_margin: float
    postconditions: dict[str, Any]
    provenance: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"node": self.node, "action": self.action, "trigger": self.trigger, "guards": list(self.guards), "deadline": self.deadline, "projected_completion": self.projected_completion, "temporal_margin": self.temporal_margin, "postconditions": self.postconditions, "provenance": list(self.provenance)}


@dataclass
class Instance:
    obligation: str
    activated_at: float
    deadline: float
    fulfilled_at: float | None = None


@dataclass
class TraceResult:
    leaf: str
    admissible: bool
    fulfilled: bool
    used_cost: float
    total_cost: float
    failure: str | None
    obligations: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    events: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {"leaf": self.leaf, "admissible": self.admissible, "fulfilled": self.fulfilled, "used_cost": self.used_cost, "total_cost": self.total_cost, "failure": self.failure, "obligations": self.obligations, "actions": self.actions, "events": self.events}


@dataclass
class Plan:
    portfolio: tuple[str, ...]
    policy: dict[str, str | None]
    objective: float
    worst_case_utility: float
    distribution_utilities: dict[str, float]
    acquisition_cost: float
    traces: dict[str, TraceResult]
    certificates: dict[str, Certificate]
    portfolios_evaluated: int
    policies_evaluated: int

    def to_dict(self) -> dict[str, Any]:
        return {"portfolio": list(self.portfolio), "policy": self.policy, "objective": self.objective, "worst_case_utility": self.worst_case_utility, "distribution_utilities": self.distribution_utilities, "acquisition_cost": self.acquisition_cost, "portfolios_evaluated": self.portfolios_evaluated, "policies_evaluated": self.policies_evaluated, "certificates": {k: v.to_dict() for k, v in self.certificates.items()}, "leaves": {k: v.to_dict() for k, v in self.traces.items()}}
