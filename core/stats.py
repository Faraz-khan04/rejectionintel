"""
Pattern detection with honesty gates.

THE CENTRAL PROBLEM
-------------------
A user with 80 applications and a 12% response rate has ~10 successes. If we
test 25 candidate attributes against that, we will find 1-2 "significant"
patterns purely by chance. Ship those as advice and the product is a random
number generator with a confident voice.

Three defences, all mandatory:

1. Minimum cell counts. No test runs on a 2x2 table with a tiny arm.
2. Benjamini-Hochberg FDR correction across ALL tests in a run. Not
   Bonferroni — too conservative at this n, we would surface nothing.
3. Wilson score intervals on every reported rate, so the UI shows the width
   of our ignorance instead of a point estimate.

And a fourth that is a product decision, not a statistical one: a run that
finds nothing must SAY it found nothing. INSUFFICIENT is a first-class,
displayable result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from scipy import stats as sp

from .enums import ConfidenceTier

STATS_VERSION = "0.1.0"

# --- Gates -----------------------------------------------------------------
MIN_ARM_SIZE = 8          # applications in each arm of a 2x2
MIN_SUCCESSES_TOTAL = 3   # total positive outcomes across both arms
MIN_RESOLVED = 25         # applications needed before ANY analysis runs
FDR_ALPHA = 0.10          # 10% false discovery rate — deliberately looser
WEAK_Q_CEILING = 0.35     # above this, we do not show it at all
MIN_RISK_RATIO = 1.5      # effect must be at least this big to be actionable


@dataclass
class Contingency:
    """2x2: exposure (has attribute) x outcome (got response).

    Carries the application ids in each arm so the UI can show exactly the rows
    a statistic was computed from. Re-deriving membership on the client would
    risk showing 23 rows next to a finding that says n=24 — and a number you
    can't reconcile is worse than no number.
    """
    dimension: str          # internal key, e.g. "fit_score >= 80"
    exposed_success: int
    exposed_fail: int
    unexposed_success: int
    unexposed_fail: int
    exposed_ids: list[int] = field(default_factory=list)
    unexposed_ids: list[int] = field(default_factory=list)
    # What a person is shown. The internal key never reaches the screen —
    # "fit_score >= 80" is a variable name, not an explanation.
    label: str = ""
    label_with: str = ""       # e.g. "Roles you matched well"
    label_without: str = ""    # e.g. "Roles you matched poorly"

    @property
    def n_exposed(self) -> int:
        return self.exposed_success + self.exposed_fail

    @property
    def n_unexposed(self) -> int:
        return self.unexposed_success + self.unexposed_fail

    @property
    def rate_exposed(self) -> float:
        return self.exposed_success / self.n_exposed if self.n_exposed else 0.0

    @property
    def rate_unexposed(self) -> float:
        return self.unexposed_success / self.n_unexposed if self.n_unexposed else 0.0


@dataclass
class Finding:
    dimension: str
    kind: str
    n_exposed: int
    n_unexposed: int
    rate_exposed: float
    rate_unexposed: float
    ci_exposed: tuple[float, float]
    ci_unexposed: tuple[float, float]
    risk_ratio: float | None
    p_value: float | None
    q_value: float | None
    confidence_tier: ConfidenceTier
    gate_reason: str | None = None
    label: str = ""
    label_with: str = ""
    label_without: str = ""
    exposed_ids: list[int] = field(default_factory=list)
    unexposed_ids: list[int] = field(default_factory=list)


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval — correct at small n, unlike the normal approx.

    At n=10 with 1 success, the normal approximation gives a lower bound below
    zero. Wilson does not, which is why every rate in this product uses it.

    >>> lo, hi = wilson_interval(1, 10)
    >>> round(lo, 3), round(hi, 3)
    (0.018, 0.404)
    """
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def benjamini_hochberg(p_values: list[float], alpha: float = FDR_ALPHA) -> list[float]:
    """Return BH-adjusted q-values, in the original order.

    >>> qs = benjamini_hochberg([0.001, 0.20, 0.04, 0.60])
    >>> [round(q, 4) for q in qs]
    [0.004, 0.2667, 0.08, 0.6]
    """
    m = len(p_values)
    if m == 0:
        return []
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    q = [0.0] * m
    prev = 1.0
    for rank in range(m - 1, -1, -1):
        orig_idx, p = indexed[rank]
        val = min(prev, p * m / (rank + 1))
        q[orig_idx] = val
        prev = val
    return q


def _gate(c: Contingency) -> str | None:
    """Return a human-readable reason to refuse the test, or None to proceed."""
    if c.n_exposed < MIN_ARM_SIZE:
        return f"only {c.n_exposed} applications had this attribute (need {MIN_ARM_SIZE})"
    if c.n_unexposed < MIN_ARM_SIZE:
        return f"only {c.n_unexposed} applications lacked this attribute (need {MIN_ARM_SIZE})"
    total_success = c.exposed_success + c.unexposed_success
    if total_success < MIN_SUCCESSES_TOTAL:
        return f"only {total_success} responses in total (need {MIN_SUCCESSES_TOTAL})"
    return None


def test_contingency(c: Contingency, kind: str = "attribute") -> Finding:
    """Run one 2x2 test. q_value is filled in later by adjust_run()."""
    reason = _gate(c)
    ci_e = wilson_interval(c.exposed_success, c.n_exposed)
    ci_u = wilson_interval(c.unexposed_success, c.n_unexposed)

    if reason:
        return Finding(
            dimension=c.dimension, kind=kind,
            n_exposed=c.n_exposed, n_unexposed=c.n_unexposed,
            rate_exposed=c.rate_exposed, rate_unexposed=c.rate_unexposed,
            ci_exposed=ci_e, ci_unexposed=ci_u,
            risk_ratio=None, p_value=None, q_value=None,
            confidence_tier=ConfidenceTier.INSUFFICIENT, gate_reason=reason,
            label=c.label or c.dimension, label_with=c.label_with,
            label_without=c.label_without,
            exposed_ids=list(c.exposed_ids), unexposed_ids=list(c.unexposed_ids),
        )

    # Fisher's exact, not chi-square: cell counts here are small by nature.
    table = [[c.exposed_success, c.exposed_fail],
             [c.unexposed_success, c.unexposed_fail]]
    _, p = sp.fisher_exact(table)

    rr = (c.rate_exposed / c.rate_unexposed) if c.rate_unexposed > 0 else None

    return Finding(
        dimension=c.dimension, kind=kind,
        n_exposed=c.n_exposed, n_unexposed=c.n_unexposed,
        rate_exposed=c.rate_exposed, rate_unexposed=c.rate_unexposed,
        ci_exposed=ci_e, ci_unexposed=ci_u,
        risk_ratio=rr, p_value=float(p), q_value=None,
        confidence_tier=ConfidenceTier.WEAK,  # provisional; set in adjust_run
        label=c.label or c.dimension, label_with=c.label_with,
        label_without=c.label_without,
        exposed_ids=list(c.exposed_ids), unexposed_ids=list(c.unexposed_ids),
    )


def _assign_tier(f: Finding) -> ConfidenceTier:
    if f.q_value is None or f.risk_ratio is None:
        return ConfidenceTier.INSUFFICIENT

    effect = max(f.risk_ratio, 1 / f.risk_ratio) if f.risk_ratio > 0 else 0
    ci_separated = f.ci_exposed[0] > f.ci_unexposed[1] or f.ci_exposed[1] < f.ci_unexposed[0]

    if f.q_value <= FDR_ALPHA and effect >= MIN_RISK_RATIO and ci_separated:
        return ConfidenceTier.STRONG
    if f.q_value <= WEAK_Q_CEILING and effect >= MIN_RISK_RATIO:
        return ConfidenceTier.WEAK
    return ConfidenceTier.INSUFFICIENT


def adjust_run(findings: list[Finding]) -> list[Finding]:
    """Apply FDR correction across the whole run and finalise tiers.

    MUST be called on every finding from a single analysis at once. Correcting
    per-finding defeats the purpose.
    """
    testable = [f for f in findings if f.p_value is not None]
    qs = benjamini_hochberg([f.p_value for f in testable])
    for f, q in zip(testable, qs):
        f.q_value = q
    for f in findings:
        f.confidence_tier = _assign_tier(f)
        if f.confidence_tier == ConfidenceTier.INSUFFICIENT and not f.gate_reason:
            f.gate_reason = "pattern did not survive multiple-comparison correction"
    return findings


# --------------------------------------------------------------------------
# Power — call this BEFORE promising the user anything
# --------------------------------------------------------------------------

def required_n_per_arm(baseline: float, target: float,
                       alpha: float = 0.05, power: float = 0.80) -> int:
    """Applications needed per arm to detect baseline -> target.

    Use this to tell the user the truth up front. For a 5% response rate,
    detecting a doubling to 10% needs roughly 430 per arm — which is why
    per-user resume A/B testing does not work and must not be shipped as a
    feature.

    >>> required_n_per_arm(0.05, 0.10)
    435
    """
    z_a = sp.norm.ppf(1 - alpha / 2)
    z_b = sp.norm.ppf(power)
    num = (z_a + z_b) ** 2 * (baseline * (1 - baseline) + target * (1 - target))
    return math.ceil(num / (target - baseline) ** 2)


def minimum_detectable_effect(n_per_arm: int, baseline: float,
                              alpha: float = 0.05, power: float = 0.80) -> float:
    """Smallest response rate we could reliably distinguish from baseline."""
    z_a = sp.norm.ppf(1 - alpha / 2)
    z_b = sp.norm.ppf(power)
    lo, hi = baseline, 0.999
    for _ in range(60):
        mid = (lo + hi) / 2
        need = ((z_a + z_b) ** 2 *
                (baseline * (1 - baseline) + mid * (1 - mid)) / (mid - baseline) ** 2)
        if need > n_per_arm:
            lo = mid
        else:
            hi = mid
    return hi
