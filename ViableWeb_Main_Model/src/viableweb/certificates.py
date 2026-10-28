from __future__ import annotations

from .models import Action, Certificate, Node, Problem


def build_certificate(problem: Problem, node: Node, action: Action) -> Certificate:
    deadlines = [o.deadline for o in problem.contract.obligations if o.fact in action.postconditions]
    deadline = min(deadlines, default=float("inf"))
    completion = node.time + action.duration
    expiry_margin = min((problem.capabilities[k].expires_at - node.time for k in action.requires), default=float("inf"))
    margin = min(deadline - completion, expiry_margin)
    prov = tuple(problem.capabilities[k].evidence_key for k in sorted(action.requires))
    return Certificate(node=node.id, action=action.id, trigger=node.observation, guards=tuple(sorted(action.requires)), deadline=deadline, projected_completion=completion, temporal_margin=margin, postconditions=dict(action.postconditions), provenance=prov)


def static_certificate_ok(cert: Certificate, epsilon: float) -> bool:
    return cert.temporal_margin >= -epsilon
