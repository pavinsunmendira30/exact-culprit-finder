"""Deterministic binary narrowing with a bounded interacting-pair fallback."""

from __future__ import annotations

from copy import deepcopy
from itertools import combinations
from typing import Callable

Replay = Callable[[dict], dict]


def revert_changes(state: dict, changes: list[dict], change_ids: set[int]) -> dict:
    candidate = deepcopy(state)
    for change in changes:
        if change["id"] not in change_ids:
            continue
        candidate[change.get("section", "config")][change["key"]] = change["before"]
    return candidate


def find_culprit(
    changes: list[dict],
    baseline: dict,
    deployed: dict,
    replay: Replay,
    threshold: float = 1.0,
    max_pair_tests: int = 36,
) -> dict:
    """Return a proven single or pair only when replay clears the SLA threshold."""
    if replay(deployed)["failure_rate"] <= threshold:
        return {"proven": False, "kind": "unproven", "culprit_ids": [], "tests": []}

    suspects = list(changes)
    tests: list[dict] = []

    def test(ids: set[int], label: str) -> dict:
        result = replay(revert_changes(deployed, changes, ids))
        entry = {
            "reverted_ids": sorted(ids),
            "label": label,
            "failure_rate": result["failure_rate"],
            "p95_ms": result["p95_ms"],
            "cleared": result["failure_rate"] <= threshold,
        }
        tests.append(entry)
        return entry

    while len(suspects) > 2:
        midpoint = len(suspects) // 2
        left, right = suspects[:midpoint], suspects[midpoint:]
        left_result = test({change["id"] for change in left}, "binary half")
        suspects = left if left_result["cleared"] else right

    for candidate in reversed(suspects):
        single_result = test({candidate["id"]}, "single confirmation")
        if single_result["cleared"]:
            return {
                "proven": True,
                "kind": "single",
                "culprit_ids": [candidate["id"]],
                "tests": tests,
            }

    pair_count = 0
    for first, second in combinations(changes, 2):
        if pair_count >= max_pair_tests:
            break
        pair_count += 1
        pair_ids = {first["id"], second["id"]}
        pair_result = test(pair_ids, "pair candidate")
        if not pair_result["cleared"]:
            continue
        first_result = test({first["id"]}, "pair member confirmation")
        second_result = test({second["id"]}, "pair member confirmation")
        if not first_result["cleared"] and not second_result["cleared"]:
            return {
                "proven": True,
                "kind": "pair",
                "culprit_ids": sorted(pair_ids),
                "tests": tests,
            }

    return {"proven": False, "kind": "unproven", "culprit_ids": [], "tests": tests}
