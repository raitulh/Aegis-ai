"""Policy version diffing: identify changed requirements/controls and what must be re-audited."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from engines.policy.dsl import ControlDSL, PolicyDSL


@dataclass
class PolicyDiff:
    added_controls: list[str]
    removed_controls: list[str]
    changed_controls: list[dict[str, Any]]
    unchanged_controls: list[str]

    @property
    def affected_control_ids(self) -> list[str]:
        return sorted(set(self.added_controls) | {c["id"] for c in self.changed_controls})

    def summary(self) -> dict[str, Any]:
        return {
            "added": self.added_controls,
            "removed": self.removed_controls,
            "changed": self.changed_controls,
            "unchanged": len(self.unchanged_controls),
            "requires_reaudit": bool(self.added_controls or self.changed_controls),
            "affected_controls": self.affected_control_ids,
        }


def _fingerprint(c: ControlDSL) -> dict[str, Any]:
    return {
        "requirement": c.requirement,
        "test_type": c.test_type,
        "severity": c.severity,
        "threshold": c.threshold,
        "condition": c.condition,
    }


def diff_policies(old: PolicyDSL, new: PolicyDSL) -> PolicyDiff:
    old_map = {c.id: c for c in old.controls}
    new_map = {c.id: c for c in new.controls}
    added = sorted(set(new_map) - set(old_map))
    removed = sorted(set(old_map) - set(new_map))
    changed: list[dict[str, Any]] = []
    unchanged: list[str] = []
    for cid in sorted(set(old_map) & set(new_map)):
        of, nf = _fingerprint(old_map[cid]), _fingerprint(new_map[cid])
        if of != nf:
            fields = [k for k in nf if of.get(k) != nf.get(k)]
            changed.append(
                {
                    "id": cid,
                    "changed_fields": fields,
                    "before": {k: of[k] for k in fields},
                    "after": {k: nf[k] for k in fields},
                }
            )
        else:
            unchanged.append(cid)
    return PolicyDiff(
        added_controls=added, removed_controls=removed, changed_controls=changed, unchanged_controls=unchanged
    )
