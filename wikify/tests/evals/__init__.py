"""Live-agent evals (0.3) — real model, real `AgentRunner`, DB-outcome assertions.

NOT part of `run-tests` (costs real tokens). Run manually from the bench root:

    bench --site pdf.localhost execute wikify.tests.evals.run --kwargs "{'scenario': 'all'}"
    bench --site pdf.localhost execute wikify.tests.evals.run --kwargs "{'scenario': 'fix_broken_table'}"

Pass `keep=1` to leave the fixture documents behind for inspection.
"""

from __future__ import annotations


def run(scenario: str = "all", keep: int = 0) -> dict:
	from wikify.tests.evals.harness import run_scenarios

	return run_scenarios(scenario, keep=bool(keep))
