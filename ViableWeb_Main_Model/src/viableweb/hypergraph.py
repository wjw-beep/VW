from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Problem


@dataclass(frozen=True)
class Hyperedge:
    prerequisites: tuple[str, ...]
    result: str
    guard: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"prerequisites": list(self.prerequisites), "result": self.result, "guard": self.guard}


@dataclass(frozen=True)
class Hypergraph:
    nodes: tuple[str, ...]
    edges: tuple[Hyperedge, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"nodes": list(self.nodes), "edges": [e.to_dict() for e in self.edges]}

    def support(self, goal: str, true_bases: set[str], enabled_actions: set[str]) -> bool:
        inbound: dict[str, list[Hyperedge]] = {}
        for edge in self.edges:
            inbound.setdefault(edge.result, []).append(edge)
        memo: dict[str, bool] = {}

        def check(node: str, visiting: frozenset[str]) -> bool:
            if node in true_bases:
                return True
            if node in memo:
                return memo[node]
            if node in visiting:
                return False
            options = inbound.get(node, [])
            value = any(all(check(prerequisite, visiting | {node}) for prerequisite in edge.prerequisites) for edge in options if edge.guard.get("action") in (None, *enabled_actions))
            memo[node] = value
            return value

        return check(goal, frozenset())


def compile_hypergraph(problem: Problem) -> Hypergraph:
    nodes: set[str] = set()
    edges: list[Hyperedge] = []
    for c in problem.capabilities.values():
        nodes.add(f"cap:{c.id}")
        nodes.add(f"evidence:{c.evidence_key}")
        edges.append(Hyperedge((f"evidence:{c.evidence_key}",), f"cap:{c.id}", {"authority": c.authority, "expiry": c.expires_at}))
    for action in problem.actions.values():
        prereqs = [f"cap:{k}" for k in action.requires]
        prereqs.extend(f"fact:{k}={v}" for k, v in sorted(action.preconditions.items()))
        prereqs.append(f"authority:{action.authority}")
        result = f"action:{action.id}"
        nodes.add(result)
        nodes.update(prereqs)
        edges.append(Hyperedge(tuple(prereqs), result, {"action": action.id, "cost": action.cost}))
        for key, val in action.postconditions.items():
            target = f"fact:{key}={val}"
            nodes.add(target)
            edges.append(Hyperedge((result, f"provider_confirmation:{action.id}"), target, {"action": action.id}))
            nodes.add(f"provider_confirmation:{action.id}")
    for o in problem.contract.obligations:
        name = f"obligation:{o.id}"
        nodes.add(name)
        nodes.add(f"fact:{o.fact}=True")
        nodes.add(f"evidence:{o.evidence_key}")
        edges.append(Hyperedge((f"fact:{o.fact}=True", f"evidence:{o.evidence_key}"), name, {"deadline": o.deadline}))
    return Hypergraph(tuple(sorted(nodes)), tuple(edges))
