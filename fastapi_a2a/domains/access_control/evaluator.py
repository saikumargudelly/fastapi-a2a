"""
Policy Evaluator — Formal spec-compliant tie-break algorithm (§19.9.1).

Invariants enforced:
  I1: DENY at specificity_rank=1 ALWAYS overrides ALLOW at rank=2
  I2: Within same (rank, priority): DENY ALWAYS beats ALLOW
  I3: ALLOW at rank=1 beats DENY at rank=2 (more specific allow wins)
  I4: No matching policy → result is DENY (deny by default)
  I5: Algorithm is O(n log n) for n policies (n bounded by 1000 per agent)
"""
from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from typing import NamedTuple


class Effect(enum.StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class DecisionSource(enum.StrEnum):
    POLICY_CACHE_HIT = "policy_cache_hit"
    FULL_EVALUATION = "full_evaluation"
    DEFAULT_DENY = "default_deny"


class PrincipalType(enum.StrEnum):
    IDENTITY = "identity"
    ROLE = "role"
    ORG = "org"
    WILDCARD = "wildcard"


# Specificity rank table (§19.9.1)
# Lower rank = more specific = higher precedence
SPECIFICITY_RANK_TABLE: dict[tuple[PrincipalType, bool], int] = {
    (PrincipalType.IDENTITY, True): 1,   # exact identity, exact skill
    (PrincipalType.IDENTITY, False): 2,  # exact identity, card-level
    (PrincipalType.ROLE, True): 3,       # role, exact skill
    (PrincipalType.ROLE, False): 4,      # role, card-level
    (PrincipalType.ORG, True): 5,        # org, exact skill
    (PrincipalType.ORG, False): 6,       # org, card-level
    (PrincipalType.WILDCARD, True): 7,   # wildcard, exact skill
    (PrincipalType.WILDCARD, False): 8,  # wildcard, card-level — least specific
}

# ACL entries are always identity-level (§19.9.1)
ACL_ENTRY_SPECIFICITY_RANK_EXACT_SKILL: int = 1
ACL_ENTRY_SPECIFICITY_RANK_CARD_LEVEL: int = 2


@dataclass
class PolicyCandidate:
    """Representation of a matching access_policy or acl_entry row."""
    policy_id: uuid.UUID
    effect: Effect
    specificity_rank: int
    priority: int
    source: str = "access_policy"  # or "acl_entry"


class EvaluationResult(NamedTuple):
    decision: Effect
    source: DecisionSource
    matched_policy_ids: list[uuid.UUID]
    winning_specificity_rank: int | None
    candidate_count: int


def compute_specificity_rank(
    principal_type: PrincipalType,
    skill_id_given: bool,  # True = exact skill scope, False = card-level (all skills)
) -> int:
    """
    Compute the specificity rank for an access_policy row.
    Used at INSERT/UPDATE time to denormalize the rank for fast evaluation.
    """
    return SPECIFICITY_RANK_TABLE[(principal_type, skill_id_given)]


def evaluate_policy(candidates: list[PolicyCandidate]) -> EvaluationResult:
    """
    Formal deterministic policy evaluation algorithm from §19.9.1.

    Algorithm:
    1. Sort all candidates by (specificity_rank ASC, priority ASC) — O(n log n)
    2. Group into tiers by (specificity_rank, priority)
    3. For each tier (most specific first):
       a. If ANY policy in tier has effect=DENY → return DENY (I2)
       b. If ANY policy in tier has effect=ALLOW → return ALLOW (I3)
       c. tier is empty or skipped → continue to next tier
    4. No matching policy found → return DENY (I4 — deny by default)

    This satisfies:
    - I1: DENY at rank=1 is processed before ALLOW at rank=2 → rank=1 tier processed first
    - I2: Within same tier, DENY is checked before ALLOW
    - I3: ALLOW at rank=1 is processed before DENY at rank=2  
    - I4: Fallthrough returns DENY
    - I5: Sorting is O(n log n); grouping is O(n) → overall O(n log n)
    """
    if not candidates:
        return EvaluationResult(
            decision=Effect.DENY,
            source=DecisionSource.DEFAULT_DENY,
            matched_policy_ids=[],
            winning_specificity_rank=None,
            candidate_count=0,
        )

    # Step 1: Sort by (specificity_rank ASC, priority ASC)
    sorted_candidates = sorted(candidates, key=lambda c: (c.specificity_rank, c.priority))

    # Step 2: Group into tiers by (specificity_rank, priority)
    tiers: dict[tuple[int, int], list[PolicyCandidate]] = {}
    for candidate in sorted_candidates:
        tier_key = (candidate.specificity_rank, candidate.priority)
        tiers.setdefault(tier_key, []).append(candidate)

    # Step 3: Evaluate tiers in sorted order (most specific first)
    sorted_tier_keys = sorted(tiers.keys())

    for tier_key in sorted_tier_keys:
        tier = tiers[tier_key]
        specificity_rank = tier_key[0]

        deny_policies = [c for c in tier if c.effect == Effect.DENY]
        allow_policies = [c for c in tier if c.effect == Effect.ALLOW]

        if deny_policies:
            # I2: DENY wins within same tier
            return EvaluationResult(
                decision=Effect.DENY,
                source=DecisionSource.FULL_EVALUATION,
                matched_policy_ids=[p.policy_id for p in deny_policies],
                winning_specificity_rank=specificity_rank,
                candidate_count=len(candidates),
            )
        if allow_policies:
            return EvaluationResult(
                decision=Effect.ALLOW,
                source=DecisionSource.FULL_EVALUATION,
                matched_policy_ids=[p.policy_id for p in allow_policies],
                winning_specificity_rank=specificity_rank,
                candidate_count=len(candidates),
            )
        # Tier had no matching policies → skip (I3 handles cross-tier precedence implicitly)

    # Step 4: Default deny (I4)
    return EvaluationResult(
        decision=Effect.DENY,
        source=DecisionSource.DEFAULT_DENY,
        matched_policy_ids=[],
        winning_specificity_rank=None,
        candidate_count=len(candidates),
    )
