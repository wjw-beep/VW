from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .engine import run_leaf
from .hypergraph import compile_hypergraph
from .loader import load_problem
from .planner import pairwise_interaction, replan, solve


def _serialize(data: Any, output: str | None) -> None:
    serialized = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)
    if output is None:
        print(serialized)
    else:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialized + "\n", encoding="utf-8")
        print(f"Wrote {path}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="viableweb")
    parser.add_argument("command", choices=["validate", "plan", "replay", "graph", "interaction", "replan"])
    parser.add_argument("--config", required=True)
    parser.add_argument("--output")
    parser.add_argument("--leaf")
    parser.add_argument("--left")
    parser.add_argument("--right")
    parser.add_argument("--observed-node")
    args = parser.parse_args()
    problem = load_problem(args.config)
    if args.command == "validate":
        payload = {"name": problem.name, "valid": True, "nodes": len(problem.nodes), "leaves": len(problem.leaves), "capabilities": len(problem.capabilities)}
    elif args.command == "graph":
        payload = compile_hypergraph(problem).to_dict()
    elif args.command == "interaction":
        if not args.left or not args.right:
            parser.error("interaction requires --left and --right")
        payload = {"left": args.left, "right": args.right, "interaction": pairwise_interaction(problem, args.left, args.right)}
    else:
        plan = solve(problem)
        if args.command == "plan":
            payload = plan.to_dict()
        elif args.command == "replan":
            if not args.observed_node:
                parser.error("replan requires --observed-node")
            payload = {"initial": {"portfolio": list(plan.portfolio), "policy": plan.policy}, "conditioned": replan(problem, plan, args.observed_node).to_dict()}
        else:
            if args.leaf is None or args.leaf not in problem.leaves:
                parser.error("replay requires --leaf with a configured leaf ID")
            trace = run_leaf(problem, plan.portfolio, plan.policy, problem.leaves[args.leaf], include_log=True)
            payload = {"plan": {"portfolio": list(plan.portfolio), "policy": plan.policy, "objective": plan.objective, "certificates": {k: v.to_dict() for k, v in plan.certificates.items()}}, "replay": trace.to_dict(), "mode": "controlled_scenario_replay"}
    _serialize(payload, args.output)


if __name__ == "__main__":
    main()
