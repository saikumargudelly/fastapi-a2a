"""
Tests for policy evaluator — spec-mandated invariants I1–I5, scenarios UT01–UT08 (§19.9.3).
"""
import uuid
import pytest
from fastapi_a2a.domains.access_control.evaluator import (
    Effect,
    DecisionSource,
    PolicyCandidate,
    compute_specificity_rank,
    evaluate_policy,
    PrincipalType,
)


def _make(effect: str, rank: int, priority: int = 100) -> PolicyCandidate:
    return PolicyCandidate(
        policy_id=uuid.uuid4(),
        effect=Effect(effect),
        specificity_rank=rank,
        priority=priority,
    )


# ── Invariant tests (I1–I5) ────────────────────────────────────────────────────

class TestInvariants:
    def test_I1_deny_rank1_overrides_allow_rank2(self):
        """I1: DENY at specificity_rank=1 ALWAYS overrides ALLOW at rank=2"""
        candidates = [_make("deny", rank=1), _make("allow", rank=2)]
        result = evaluate_policy(candidates)
        assert result.decision == Effect.DENY
        assert result.winning_specificity_rank == 1

    def test_I2_deny_beats_allow_same_tier(self):
        """I2: Within same (rank, priority): DENY ALWAYS beats ALLOW"""
        candidates = [_make("deny", rank=3), _make("allow", rank=3)]
        result = evaluate_policy(candidates)
        assert result.decision == Effect.DENY

    def test_I3_allow_rank1_beats_deny_rank2(self):
        """I3: ALLOW at rank=1 beats DENY at rank=2 (more specific allow wins)"""
        candidates = [_make("allow", rank=1), _make("deny", rank=2)]
        result = evaluate_policy(candidates)
        assert result.decision == Effect.ALLOW
        assert result.winning_specificity_rank == 1

    def test_I4_no_matching_policy_default_deny(self):
        """I4: No matching policy → result is DENY"""
        result = evaluate_policy([])
        assert result.decision == Effect.DENY
        assert result.source == DecisionSource.DEFAULT_DENY

    def test_I5_performance_1000_policies(self):
        """I5: Algorithm is O(n log n) for n=1000 — should complete quickly."""
        import time
        # 999 allow policies + 1 identity deny at rank=1
        candidates = [_make("allow", rank=8) for _ in range(999)]
        candidates.append(_make("deny", rank=1))
        start = time.perf_counter()
        result = evaluate_policy(candidates)
        elapsed = time.perf_counter() - start
        assert result.decision == Effect.DENY
        assert elapsed < 0.5, f"Algorithm too slow: {elapsed:.3f}s for 1000 policies"


# ── Unit test scenarios UT01–UT08 from §19.9.3 ────────────────────────────────

class TestPolicyScenarios:
    def test_UT01_identity_deny_role_allow_same_tier(self):
        """UT01: identity deny + role allow, same rank+priority → DENY (I2)"""
        candidates = [_make("deny", rank=1), _make("allow", rank=1)]
        assert evaluate_policy(candidates).decision == Effect.DENY

    def test_UT02_identity_allow_rank1_identity_deny_all_skills_rank2(self):
        """UT02: identity allow (rank=1) + identity deny for all skills (rank=2) → ALLOW (I3)"""
        candidates = [_make("allow", rank=1), _make("deny", rank=2)]
        assert evaluate_policy(candidates).decision == Effect.ALLOW

    def test_UT03_identity_deny_rank1_identity_allow_all_skills_rank2(self):
        """UT03: identity deny (rank=1) + identity allow for all skills (rank=2) → DENY (I1)"""
        candidates = [_make("deny", rank=1), _make("allow", rank=2)]
        assert evaluate_policy(candidates).decision == Effect.DENY

    def test_UT04_no_matching_policy(self):
        """UT04: No matching policy → DENY (I4)"""
        assert evaluate_policy([]).decision == Effect.DENY

    def test_UT05_wildcard_allow_identity_deny(self):
        """UT05: wildcard allow + identity deny → DENY (I1 — identity more specific)"""
        candidates = [_make("allow", rank=8), _make("deny", rank=1)]
        assert evaluate_policy(candidates).decision == Effect.DENY

    def test_UT06_role_allow_org_deny_same_rank(self):
        """UT06: role allow + org deny, same rank → DENY (I2)"""
        candidates = [_make("allow", rank=3), _make("deny", rank=3)]
        assert evaluate_policy(candidates).decision == Effect.DENY

    def test_UT07_1000_allows_one_identity_deny_rank1(self):
        """UT07: 1000 policies, all allow except one identity deny rank=1 → DENY (I1 + I5)"""
        candidates = [_make("allow", rank=i % 8 + 1) for i in range(1000)]
        candidates[42] = _make("deny", rank=1)  # One specific deny
        assert evaluate_policy(candidates).decision == Effect.DENY

    def test_UT08_acl_entry_allow_access_policy_deny_both_rank1(self):
        """UT08: acl_entry allow + access_policy deny both rank=1 → DENY (I2)"""
        allow_acl = PolicyCandidate(
            policy_id=uuid.uuid4(), effect=Effect.ALLOW, specificity_rank=1, priority=100, source="acl_entry"
        )
        deny_pol = PolicyCandidate(
            policy_id=uuid.uuid4(), effect=Effect.DENY, specificity_rank=1, priority=100, source="access_policy"
        )
        result = evaluate_policy([allow_acl, deny_pol])
        assert result.decision == Effect.DENY


# ── Specificity rank computation tests ────────────────────────────────────────

class TestSpecificityRank:
    def test_identity_exact_skill_is_rank_1(self):
        assert compute_specificity_rank(PrincipalType.IDENTITY, True) == 1

    def test_wildcard_card_level_is_rank_8(self):
        assert compute_specificity_rank(PrincipalType.WILDCARD, False) == 8

    def test_role_exact_skill_is_rank_3(self):
        assert compute_specificity_rank(PrincipalType.ROLE, True) == 3
