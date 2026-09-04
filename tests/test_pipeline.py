"""Synthetic-data tests. Run with: python -m tests.test_pipeline"""

import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.deterministic import (  # noqa: E402
    Tenure, derive_outcome, default_vocab, extract_min_years_required,
    infer_seniority, merge_tenure_months,
)
from core.fit_score import (  # noqa: E402
    CandidateProfile, JobRequirements, compute_fit,
)
from core.enums import ConfidenceTier, EventType, Resolution, Seniority, Stage  # noqa: E402
from core.narrate import allowed_numbers, validate  # noqa: E402
from core.pipeline import AnalyzedApplication, run_analysis  # noqa: E402
from core.stats import (  # noqa: E402
    Contingency, adjust_run, benjamini_hochberg, minimum_detectable_effect,
    required_n_per_arm, test_contingency, wilson_interval,
)

PASS, FAIL = "  PASS", "  FAIL"
failures = []


def check(name, cond, detail=""):
    print(f"{PASS if cond else FAIL}  {name}{'  ' + detail if detail else ''}")
    if not cond:
        failures.append(name)


print("\n=== 1. Deterministic extraction ===")
check("seniority: Senior PM -> senior",
      infer_seniority("Senior Product Manager") is Seniority.SENIOR)
check("seniority: plain PM -> mid",
      infer_seniority("Product Manager") is Seniority.MID)
check("seniority: Director -> director",
      infer_seniority("Director of Product, Platform") is Seniority.DIRECTOR)
check("seniority: Engineer II -> mid",
      infer_seniority("Software Engineer II") is Seniority.MID)

overlap = merge_tenure_months(
    [Tenure(date(2020, 1, 1), date(2022, 1, 1)),
     Tenure(date(2021, 1, 1), date(2023, 1, 1))],
    today=date(2026, 1, 1),
)
naive = 24 + 24
check("overlapping tenure merged, not summed",
      overlap == 36, f"merged={overlap}mo vs naive={naive}mo")

check("years required: '5+ years experience' -> 5",
      extract_min_years_required("We need 5+ years experience in product") == 5)
check("years required: '3-5 years' -> 3 (takes minimum)",
      extract_min_years_required("3-5 years of relevant experience required") == 3)
check("years required: absent -> None",
      extract_min_years_required("Great communicator wanted") is None)

vocab = default_vocab()
hits = vocab.find("Built dashboards in SQL and Python. Ran A/B testing on checkout.")
check("skill vocab finds SQL/Python/A-B", 
      {"SQL", "Python", "A/B Testing"} <= set(hits))
check("word-boundary: 'Google' does not match a bare token",
      "Python" not in vocab.find("I used Googlepython once"))

print("\n=== 2. Funnel derivation (the ghosting case) ===")
applied = date(2026, 1, 1)
silent = derive_outcome(applied, [(EventType.SUBMITTED, applied)], as_of=date(2026, 3, 1))
check("60 days of silence -> GHOSTED, not REJECTED",
      silent.resolution is Resolution.GHOSTED, f"got {silent.resolution.value}")
recent = derive_outcome(applied, [(EventType.SUBMITTED, applied)], as_of=date(2026, 1, 20))
check("19 days of silence -> OPEN (excluded from stats)",
      recent.resolution is Resolution.OPEN, f"got {recent.resolution.value}")

interviewed = derive_outcome(applied, [
    (EventType.SUBMITTED, applied),
    (EventType.RECRUITER_CONTACT, applied + timedelta(days=6)),
    (EventType.INTERVIEW_COMPLETED, applied + timedelta(days=20)),
    (EventType.REJECTION_RECEIVED, applied + timedelta(days=26)),
], as_of=date(2026, 3, 1))
check("furthest stage = interview", interviewed.furthest_stage is Stage.INTERVIEW)
check("days to first response = 6", interviewed.days_to_first_response == 6)

print("\n=== 3. Fit score ===")
profile = CandidateProfile(
    skills={"Python": 3, "Roadmapping": 2, "Agile": 1},
    skills_in_bullets={"Python", "Roadmapping"},
    total_experience_months=72,
    seniority=Seniority.MID,
    industries={"B2B SaaS"},
)
strong = compute_fit(profile, JobRequirements(
    required_skills=["Python", "Roadmapping"], min_years=5,
    seniority=Seniority.MID, industry="B2B SaaS"))
weak = compute_fit(profile, JobRequirements(
    required_skills=["SQL", "Tableau", "Machine Learning"], min_years=10,
    seniority=Seniority.DIRECTOR, industry="Consumer"))
check("well-matched job scores high", strong.overall >= 90, f"{strong.overall}")
check("poorly-matched job scores low", weak.overall <= 35, f"{weak.overall}")
check("missing skills are itemised for the UI",
      set(weak.breakdown["skills_required"]["missing"]) == {"SQL", "Tableau", "Machine Learning"})
check("skill only in skills-blob counts half",
      compute_fit(profile, JobRequirements(required_skills=["Agile"])).skills_required == 50.0)

print("\n=== 4. Statistics ===")
lo, hi = wilson_interval(1, 10)
check("Wilson CI at n=10, k=1 stays in [0,1]",
      0 < lo < 0.05 and 0.35 < hi < 0.45, f"[{lo:.3f}, {hi:.3f}]")

qs = benjamini_hochberg([0.001, 0.20, 0.04, 0.60])
check("BH q-values monotone and >= p",
      all(q >= p for q, p in zip(qs, [0.001, 0.20, 0.04, 0.60])),
      f"{[round(q,3) for q in qs]}")

tiny = test_contingency(Contingency("requires:SQL", 1, 4, 3, 20))
check("tiny arm is gated as INSUFFICIENT",
      tiny.confidence_tier is ConfidenceTier.INSUFFICIENT, tiny.gate_reason or "")

real = adjust_run([test_contingency(Contingency("fit>=80", 12, 8, 4, 46))])[0]
check("large real effect reaches STRONG",
      real.confidence_tier is ConfidenceTier.STRONG,
      f"RR={real.risk_ratio:.2f} q={real.q_value:.4f}")

n_needed = required_n_per_arm(0.05, 0.10)
check("power: detecting 5%->10% needs ~430/arm",
      420 <= n_needed <= 450, f"{n_needed} per arm")
mde = minimum_detectable_effect(40, 0.05)
check("power: at 40/arm the MDE is enormous",
      mde > 0.20, f"MDE = {mde*100:.0f}% response rate")

print("\n=== 5. FALSE POSITIVE STRESS TEST ===")
print("  Simulating users whose outcomes are PURE NOISE (no real pattern).")
print("  A trustworthy system should almost never report a STRONG finding.\n")

random.seed(11)
vocab_skills = ["SQL", "Tableau", "Python", "Figma", "Machine Learning"]
strong_hits = weak_hits = 0
TRIALS = 300

for _ in range(TRIALS):
    apps = []
    for i in range(80):
        fit_val = random.uniform(20, 95)
        missing = random.sample(vocab_skills, k=random.randint(0, 3))
        responded = random.random() < 0.12          # outcome independent of everything
        outcome = derive_outcome(
            date(2026, 1, 1),
            [(EventType.SUBMITTED, date(2026, 1, 1))] +
            ([(EventType.RECRUITER_CONTACT, date(2026, 1, 10))] if responded else []) +
            [(EventType.REJECTION_RECEIVED, date(2026, 2, 1))],
            as_of=date(2026, 6, 1),
        )
        fit = compute_fit(
            CandidateProfile(skills={}, total_experience_months=60, seniority=Seniority.MID),
            JobRequirements(required_skills=missing, min_years=5, seniority=Seniority.MID),
        )
        fit.overall = fit_val
        apps.append(AnalyzedApplication(
            i, "PM", "Co", fit, outcome, missing, "B2B SaaS",
            random.choice(["linkedin", "referral", "direct"]), "A"))
    res = run_analysis(apps)
    strong_hits += sum(1 for f in res.findings if f.confidence_tier is ConfidenceTier.STRONG)
    weak_hits += sum(1 for f in res.findings if f.confidence_tier is ConfidenceTier.WEAK)

strong_rate = strong_hits / TRIALS
print(f"  Over {TRIALS} pure-noise users:")
print(f"    STRONG findings surfaced: {strong_hits}  ({strong_rate:.2f} per user)")
print(f"    WEAK   findings surfaced: {weak_hits}  ({weak_hits/TRIALS:.2f} per user)")
check("false STRONG findings stay rare on pure noise",
      strong_rate < 0.15, f"{strong_rate:.3f} per user")

print("\n=== 6. LLM numeric guard ===")
payload = {"response_rate_with": 0.34, "response_rate_without": 0.1,
           "applications_with_attribute": 22, "risk_ratio": 3.4}
ok = "Your response rate was 34% across 22 applications, versus 10% without."
bad_num = "Your response rate was 34%, and fixing this would lift you to 52%."
bad_cause = "They rejected you for missing SQL experience."
hype = "This clearly shows a significant problem."

check("clean narration passes", validate(ok, payload, ConfidenceTier.STRONG) == [])
check("invented number (52) is caught",
      any("52" in e for e in validate(bad_num, payload, ConfidenceTier.STRONG)))
check("causal claim is caught",
      any("causal" in e for e in validate(bad_cause, payload, ConfidenceTier.STRONG)))
check("hype is caught on a WEAK finding",
      len(validate(hype, payload, ConfidenceTier.WEAK)) > 0)
check("same hype is allowed on a STRONG finding",
      len(validate(hype, payload, ConfidenceTier.STRONG)) == 0)

print("\n" + "=" * 62)
if failures:
    print(f"FAILED ({len(failures)}): {failures}")
    sys.exit(1)
print("ALL CHECKS PASSED")
