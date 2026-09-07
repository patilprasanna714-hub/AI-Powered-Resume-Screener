"""
scoring_logic.py
Owner: Chiranthan — Matching & Queue Processing

Pure, unit-testable functions implementing the weighted match score defined
in the technical spec (section 6.4):

    Total Match Score = (Skill Score x 0.50) + (Title Score x 0.30) + (Experience Score x 0.20)

No AWS SDK calls live in this file on purpose — it stays 100% deterministic
and testable in isolation from DynamoDB/SQS.
"""

from difflib import SequenceMatcher
from typing import Iterable, Optional, Dict, Any, List, Tuple


# ---------------------------------------------------------------------------
# Skill scoring (50% weight)
# ---------------------------------------------------------------------------
def normalize_skill(skill: str) -> str:
    """Lowercase + strip so 'AWS Lambda' and 'aws lambda' match."""
    return skill.strip().lower()


def compute_skill_score(required_skills: Iterable[str],
                         candidate_skills: Iterable[str]) -> Tuple[float, List[str], List[str]]:
    """
    Skill overlap = (required skills matched) / (total required skills) * 100.
    Preferred/extra skills are surfaced separately and never push this above 100.

    Returns: (score, matched_skills, missing_skills)
    """
    required_norm = {normalize_skill(s) for s in required_skills if s.strip()}
    candidate_norm = {normalize_skill(s) for s in candidate_skills if s.strip()}

    if not required_norm:
        # No required skills configured on the JD -> nothing to penalize against.
        return 100.0, sorted(candidate_norm), []

    matched = required_norm & candidate_norm
    missing = required_norm - candidate_norm

    score = (len(matched) / len(required_norm)) * 100.0
    return round(score, 2), sorted(matched), sorted(missing)


# ---------------------------------------------------------------------------
# Title scoring (30% weight)
# ---------------------------------------------------------------------------
def compute_title_score(candidate_titles: Iterable[str],
                         target_title: str,
                         synonym_map: Optional[Dict[str, List[str]]] = None,
                         similarity_floor: float = 0.55) -> float:
    """
    Exact taxonomy match       -> 100
    Approved synonym match     -> 85
    Controlled fuzzy similarity-> scaled 0-70 (only counted if above similarity_floor)
    No match at all            -> 0
    """
    if not target_title:
        return 100.0  # nothing to align against

    target_norm = target_title.strip().lower()
    synonym_map = synonym_map or {}
    approved_synonyms = {s.lower() for s in synonym_map.get(target_norm, [])}

    best_fuzzy = 0.0
    for raw_title in candidate_titles:
        title_norm = raw_title.strip().lower()
        if title_norm == target_norm:
            return 100.0
        if title_norm in approved_synonyms:
            return 85.0
        ratio = SequenceMatcher(None, title_norm, target_norm).ratio()
        best_fuzzy = max(best_fuzzy, ratio)

    if best_fuzzy >= similarity_floor:
        return round(best_fuzzy * 70.0, 2)
    return 0.0


# ---------------------------------------------------------------------------
# Experience scoring (20% weight)
# ---------------------------------------------------------------------------
def compute_experience_score(candidate_years: Optional[float],
                              required_years: Optional[float]) -> Optional[float]:
    """
    min(candidate_years / required_years, 1) * 100.
    Returns None if required_years is not specified -> caller must redistribute weights.
    """
    if required_years is None or required_years <= 0:
        return None
    candidate_years = candidate_years or 0.0
    ratio = min(candidate_years / required_years, 1.0)
    return round(ratio * 100.0, 2)


# ---------------------------------------------------------------------------
# Total score with weight redistribution policy
# ---------------------------------------------------------------------------
DEFAULT_WEIGHTS = {"skills": 0.50, "title": 0.30, "experience": 0.20}


def compute_total_score(skill_score: float,
                         title_score: float,
                         experience_score: Optional[float],
                         weights: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """
    Combines the three components. If experience_score is None (no minimum
    experience configured on the job), redistribute its 20% weight
    proportionally across skills (50->62.5%) and title (30->37.5%),
    per spec section 6.4 policy: "mark N/A and redistribute weights by policy."
    """
    weights = dict(weights or DEFAULT_WEIGHTS)

    if experience_score is None:
        remaining = weights["skills"] + weights["title"]
        w_skills = weights["skills"] / remaining
        w_title = weights["title"] / remaining
        total = (skill_score * w_skills) + (title_score * w_title)
        breakdown = {
            "skills": skill_score,
            "title": title_score,
            "experience": None,
            "weights_used": {"skills": round(w_skills, 4), "title": round(w_title, 4), "experience": 0.0},
            "total": round(total, 2),
        }
        return breakdown

    total = (skill_score * weights["skills"]) + \
            (title_score * weights["title"]) + \
            (experience_score * weights["experience"])

    return {
        "skills": skill_score,
        "title": title_score,
        "experience": experience_score,
        "weights_used": weights,
        "total": round(total, 2),
    }


def build_score_explanation(matched_skills: List[str], missing_skills: List[str],
                             breakdown: Dict[str, Any], threshold: float) -> Dict[str, Any]:
    """Human-readable, auditable explanation stored alongside the score (spec 9.1 / 16)."""
    return {
        "matched_skills": matched_skills,
        "missing_skills": missing_skills,
        "component_scores": {
            "skills": breakdown["skills"],
            "title": breakdown["title"],
            "experience": breakdown["experience"],
        },
        "weights_used": breakdown["weights_used"],
        "total_score": breakdown["total"],
        "threshold": threshold,
        "system_recommendation": "SHORTLIST" if breakdown["total"] >= threshold else "NO_MATCH",
    }
