"""
nlp_parser.py
Owner: Sasi — Comprehend NLP & Data Parsing

Core parsing and normalization engine for the AI-Powered Resume Screener pipeline.
Transforms raw resume text and Comprehend entity detections into a normalized,
auditable, and explainable candidate profile JSON conforming to technical spec
sections 6.2, 6.3, 7, and 7.1.

Design Constraints:
    - Pure Python 3.10+ standard library (no external heavy dependencies).
    - Fully deterministic and unit-testable in isolation.
    - Preserves confidence metrics and generates quality flags for human review.
    - Enforces PII masking helpers for secure logging (spec 6.3 & 9).
"""

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Approved Role Taxonomy & Synonym Mappings (Spec 6.3 & 6.4)
# ---------------------------------------------------------------------------

ROLE_TAXONOMY: Dict[str, str] = {
    "software engineer": "Software Engineer",
    "software developer": "Software Engineer",
    "swe": "Software Engineer",
    "backend engineer": "Backend Engineer",
    "backend developer": "Backend Engineer",
    "frontend engineer": "Frontend Engineer",
    "frontend developer": "Frontend Engineer",
    "full stack engineer": "Full Stack Developer",
    "full stack developer": "Full Stack Developer",
    "fullstack developer": "Full Stack Developer",
    "cloud engineer": "Cloud Engineer",
    "cloud architect": "Cloud Architect",
    "cloud support associate": "Cloud Support Associate",
    "cloud support engineer": "Cloud Support Associate",
    "devops engineer": "DevOps Engineer",
    "site reliability engineer": "Site Reliability Engineer",
    "sre": "Site Reliability Engineer",
    "data engineer": "Data Engineer",
    "data scientist": "Data Scientist",
    "data analyst": "Data Analyst",
    "machine learning engineer": "Machine Learning Engineer",
    "ml engineer": "Machine Learning Engineer",
    "ai engineer": "AI Engineer",
    "solutions architect": "Solutions Architect",
    "systems engineer": "Systems Engineer",
    "mechanical engineer": "Mechanical Engineer",
    "qa engineer": "QA Engineer",
    "test engineer": "QA Engineer",
    "technical support specialist": "Technical Support Specialist",
}


# ---------------------------------------------------------------------------
# Domain Skill Alias & Canonicalization Dictionary (Spec 6.3)
# Maps variations and vendor terms to canonical recruiter-friendly skill names.
# ---------------------------------------------------------------------------

SKILL_ALIASES: Dict[str, str] = {
    # Cloud & Serverless
    "aws": "AWS",
    "amazon web services": "AWS",
    "aws lambda": "Lambda",
    "lambda": "Lambda",
    "amazon dynamodb": "DynamoDB",
    "dynamodb": "DynamoDB",
    "amazon s3": "S3",
    "s3": "S3",
    "amazon sqs": "SQS",
    "sqs": "SQS",
    "amazon sns": "SNS",
    "sns": "SNS",
    "amazon textract": "Textract",
    "textract": "Textract",
    "amazon comprehend": "Comprehend",
    "comprehend": "Comprehend",
    "amazon api gateway": "API Gateway",
    "api gateway": "API Gateway",
    "amazon cognito": "Cognito",
    "cognito": "Cognito",
    "amazon ses": "SES",
    "ses": "SES",
    "cloudwatch": "CloudWatch",
    "amazon cloudwatch": "CloudWatch",
    "cloudformation": "CloudFormation",
    "aws cdk": "AWS CDK",
    "azure": "Azure",
    "gcp": "Google Cloud",
    "google cloud platform": "Google Cloud",
    "google cloud": "Google Cloud",

    # Programming Languages
    "python": "Python",
    "python3": "Python",
    "javascript": "JavaScript",
    "js": "JavaScript",
    "typescript": "TypeScript",
    "ts": "TypeScript",
    "java": "Java",
    "c++": "C++",
    "cpp": "C++",
    "c#": "C#",
    "golang": "Go",
    "go": "Go",
    "ruby": "Ruby",
    "rust": "Rust",
    "sql": "SQL",
    "html": "HTML",
    "html5": "HTML",
    "css": "CSS",
    "css3": "CSS",
    "bash": "Bash",
    "shell": "Shell Scripting",

    # Frameworks & Libraries
    "react": "React",
    "react.js": "React",
    "reactjs": "React",
    "node": "Node.js",
    "node.js": "Node.js",
    "nodejs": "Node.js",
    "express": "Express",
    "express.js": "Express",
    "expressjs": "Express",
    "fastapi": "FastAPI",
    "flask": "Flask",
    "django": "Django",
    "next.js": "Next.js",
    "nextjs": "Next.js",
    "vue": "Vue.js",
    "vue.js": "Vue.js",
    "angular": "Angular",
    "spring boot": "Spring Boot",
    "spring": "Spring Boot",

    # Databases & Storage
    "postgresql": "PostgreSQL",
    "postgres": "PostgreSQL",
    "mysql": "MySQL",
    "mongodb": "MongoDB",
    "redis": "Redis",
    "sqlite": "SQLite",

    # DevOps & Infrastructure
    "docker": "Docker",
    "docker container": "Docker",
    "kubernetes": "Kubernetes",
    "k8s": "Kubernetes",
    "terraform": "Terraform",
    "ci/cd": "CI/CD",
    "github actions": "GitHub Actions",
    "jenkins": "Jenkins",
    "git": "Git",
    "linux": "Linux",

    # Architecture & Practices
    "rest": "REST APIs",
    "restful": "REST APIs",
    "rest api": "REST APIs",
    "rest apis": "REST APIs",
    "graphql": "GraphQL",
    "microservices": "Microservices",
    "serverless": "Serverless",
    "oop": "Object-Oriented Programming",
    "agile": "Agile",
    "scrum": "Scrum",
    "tdd": "TDD",
}


# Known non-employer words that Comprehend might classify as ORGANIZATION
DISALLOWED_EMPLOYERS = {
    "bachelor", "master", "phd", "university", "institute", "college",
    "school", "academy", "curriculum", "resume", "cv", "experience",
    "education", "skills", "projects", "certifications", "linkedin", "github"
}


# ---------------------------------------------------------------------------
# PII & Deterministic Regular Expressions (Spec 6.3 & 9)
# ---------------------------------------------------------------------------

EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
)

PHONE_PATTERN = re.compile(
    r"(?:(?:\+?1\s*(?:[.-]\s*)?)?(?:\(\s*([2-9]1[02-9]|[2-9][02-8]1|[2-9][02-8][02-9])\s*\)|([2-9]1[02-9]|[2-9][02-8]1|[2-9][02-8][02-9]))\s*(?:[.-]\s*)?)?([2-9]1[02-9]|[2-9][02-9]1|[2-9][02-9]{2})\s*(?:[.-]\s*)?([0-9]{4})(?:\s*(?:#|x\.?|ext\.?|extension)\s*(\d+))?|"
    r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}|"
    r"\+?\d{2}[-.\s]?\d{4,5}[-.\s]?\d{4,5}"
)

LINKEDIN_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?linkedin\.com/in/[A-Za-z0-9_-]+/?",
    re.IGNORECASE
)

GITHUB_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_-]+/?",
    re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Date Extraction & Range Parsing Helpers (Spec 6.3)
# ---------------------------------------------------------------------------

MONTH_NAMES = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

# Regex to detect date intervals e.g. "Jan 2020 - Present", "2018 - 2021", "06/2019 to 08/2022"
DATE_RANGE_PATTERN = re.compile(
    r"(?P<start>(?:[A-Za-z]{3,9}\.?\s+)?(?:\d{1,2}/)?(?:19|20)\d{2})"
    r"\s*(?:-|–|—|to|until)\s*"
    r"(?P<end>(?:[A-Za-z]{3,9}\.?\s+)?(?:\d{1,2}/)?(?:19|20)\d{2}|present|current|now)",
    re.IGNORECASE
)


def mask_email(email: Optional[str]) -> str:
    """Mask email for privacy-safe logging per spec 6.3 & 9."""
    if not email or "@" not in email:
        return "[MASKED_EMAIL]"
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "*"
    else:
        masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{masked_local}@{domain}"


def mask_phone(phone: Optional[str]) -> str:
    """Mask phone number for privacy-safe logging per spec 6.3 & 9."""
    if not phone:
        return "[MASKED_PHONE]"
    digits = re.sub(r"\D", "", phone)
    if len(digits) <= 4:
        return "****"
    return f"***-***-{digits[-4:]}"


def parse_month_year(text: str, default_month: int = 1) -> Optional[Tuple[int, int]]:
    """Parse string text like 'Jan 2021', '06/2019', '2018' into (year, month)."""
    text = text.strip().lower().rstrip(".")
    if not text:
        return None

    # Check for "Present" / "Current"
    if text in ("present", "current", "now"):
        now = datetime.now(timezone.utc)
        return now.year, now.month

    # Check for Month Word + Year (e.g. "January 2020" or "Jan 2020")
    parts = text.split()
    if len(parts) == 2:
        month_str, year_str = parts
        month_clean = re.sub(r"[^a-z]", "", month_str)
        year_clean = re.sub(r"[^0-9]", "", year_str)
        if month_clean in MONTH_NAMES and len(year_clean) == 4:
            return int(year_clean), MONTH_NAMES[month_clean]

    # Check for MM/YYYY
    slash_match = re.match(r"^(\d{1,2})/((?:19|20)\d{2})$", text)
    if slash_match:
        m, y = int(slash_match.group(1)), int(slash_match.group(2))
        if 1 <= m <= 12:
            return y, m

    # Check for YYYY alone
    year_match = re.search(r"\b((?:19|20)\d{2})\b", text)
    if year_match:
        return int(year_match.group(1)), default_month

    return None


def merge_date_intervals(intervals: List[Tuple[datetime, datetime]]) -> List[Tuple[datetime, datetime]]:
    """
    Merge overlapping or contiguous date intervals to prevent double-counting concurrent jobs.
    Spec 6.3: 'Merge overlapping DATE ranges, cap future dates, subtract gaps where ranges are explicit.'
    """
    if not intervals:
        return []

    # Sort intervals by start date
    sorted_intervals = sorted(intervals, key=lambda x: x[0])
    merged: List[Tuple[datetime, datetime]] = [sorted_intervals[0]]

    for current_start, current_end in sorted_intervals[1:]:
        last_start, last_end = merged[-1]
        if current_start <= last_end:
            # Overlapping or contiguous interval -> extend last end date
            merged[-1] = (last_start, max(last_end, current_end))
        else:
            merged.append((current_start, current_end))

    return merged


def calculate_experience_years(raw_text: str,
                               comprehend_date_entities: Optional[List[Dict[str, Any]]] = None) -> Tuple[float, List[Dict[str, str]], List[str]]:
    """
    Extract date intervals from resume text and Comprehend DATE entities,
    merge overlapping intervals, cap future dates, and calculate net professional years.

    Returns:
        (experience_years, date_ranges_detected, flags)
    """
    now = datetime.now(timezone.utc)
    intervals: List[Tuple[datetime, datetime]] = []
    detected_ranges: List[Dict[str, str]] = []
    flags: List[str] = []

    # 1. Regex search for date intervals in raw text
    for match in DATE_RANGE_PATTERN.finditer(raw_text):
        start_raw = match.group("start").strip()
        end_raw = match.group("end").strip()

        start_parsed = parse_month_year(start_raw, default_month=1)
        end_parsed = parse_month_year(end_raw, default_month=12)

        if start_parsed and end_parsed:
            try:
                dt_start = datetime(start_parsed[0], start_parsed[1], 1, tzinfo=timezone.utc)
                # For end date, set to last day of the month roughly or 28th
                dt_end = datetime(end_parsed[0], end_parsed[1], 28, tzinfo=timezone.utc)

                # Cap future dates to current date
                if dt_end > now:
                    dt_end = now
                if dt_start > now:
                    flags.append("FUTURE_START_DATE_CAPPED")
                    continue

                if dt_start <= dt_end:
                    intervals.append((dt_start, dt_end))
                    detected_ranges.append({
                        "raw_start": start_raw,
                        "raw_end": end_raw,
                        "start": dt_start.strftime("%Y-%m"),
                        "end": dt_end.strftime("%Y-%m"),
                    })
            except (ValueError, OverflowError):
                flags.append("MALFORMED_DATE_ENCOUNTERED")

    # 2. Check Comprehend DATE entities for any single-span explicit years if no ranges found
    if not intervals and comprehend_date_entities:
        for entity in comprehend_date_entities:
            text = entity.get("Text", "")
            # Look for statements like "3 years", "5+ years", "4 yrs"
            exp_match = re.search(r"(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)", text, re.IGNORECASE)
            if exp_match:
                years = float(exp_match.group(1))
                return round(years, 1), detected_ranges, flags

    if not intervals:
        # Check raw text for explicit "X years of experience"
        exp_match = re.search(r"(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)(?:\s+of)?\s+experience", raw_text, re.IGNORECASE)
        if exp_match:
            years = float(exp_match.group(1))
            return round(years, 1), detected_ranges, flags

        flags.append("NO_EXPERIENCE_DATES_FOUND")
        return 0.0, detected_ranges, flags

    # 3. Merge overlapping intervals
    merged = merge_date_intervals(intervals)

    # 4. Sum net duration in days and convert to years
    total_days = sum((end - start).days for start, end in merged)
    years = round(total_days / 365.25, 1)

    return max(0.0, years), detected_ranges, flags


# ---------------------------------------------------------------------------
# Entity Normalization & Extraction Functions
# ---------------------------------------------------------------------------

def extract_contact_info(text: str) -> Tuple[Optional[str], Optional[str], Dict[str, str]]:
    """Extract email, phone, and links deterministically via regex."""
    # Email
    email_match = EMAIL_PATTERN.search(text)
    email = email_match.group(0).strip().lower() if email_match else None

    # Phone
    phone = None
    phone_match = PHONE_PATTERN.search(text)
    if phone_match:
        raw_phone = phone_match.group(0).strip()
        # Ensure it has at least 7 digits to avoid false positives
        if len(re.sub(r"\D", "", raw_phone)) >= 7:
            phone = raw_phone

    # Links
    links: Dict[str, str] = {}
    linkedin_match = LINKEDIN_PATTERN.search(text)
    if linkedin_match:
        links["linkedin"] = linkedin_match.group(0).strip()
    github_match = GITHUB_PATTERN.search(text)
    if github_match:
        links["github"] = github_match.group(0).strip()

    return email, phone, links


def normalize_skill_name(raw_skill: str) -> str:
    """Normalize a raw skill using alias dictionary or clean capitalization."""
    clean = raw_skill.strip().lower()
    clean = re.sub(r"\s+", " ", clean)
    if clean in SKILL_ALIASES:
        return SKILL_ALIASES[clean]
    # Fallback to title-cased token if reasonable
    return raw_skill.strip().title()


def extract_skills_from_entities_and_text(
    raw_text: str,
    comprehend_entities: Optional[List[Dict[str, Any]]] = None,
    min_confidence: float = 0.50
) -> Tuple[List[str], float, List[Dict[str, Any]]]:
    """
    Extract skills from Comprehend custom recognizer entities (type="SKILL")
    and supplement with keyword scanning against approved taxonomy.
    
    Returns:
        (normalized_skills_list, average_skill_confidence, raw_skill_entities)
    """
    skills_map: Dict[str, float] = {}  # normalized_name -> max_confidence
    raw_detected: List[Dict[str, Any]] = []

    # 1. Process Comprehend entities
    if comprehend_entities:
        for ent in comprehend_entities:
            ent_type = ent.get("Type")
            confidence = float(ent.get("Score", 0.0))

            if ent_type == "SKILL" and confidence >= min_confidence:
                raw_detected.append(ent)
                norm = normalize_skill_name(ent["Text"])
                if norm not in skills_map or confidence > skills_map[norm]:
                    skills_map[norm] = confidence

    # 2. Resilient taxonomy scanning in raw text (ensures baseline skills are captured
    # even when custom model endpoint is initializing or for standard tech terms)
    text_lower = f" {raw_text.lower()} "
    for alias, canonical in SKILL_ALIASES.items():
        # Match as whole word/phrase
        pattern = r"(?<![a-zA-Z0-9_-])" + re.escape(alias) + r"(?![a-zA-Z0-9_-])"
        if re.search(pattern, text_lower):
            if canonical not in skills_map:
                skills_map[canonical] = 0.85  # baseline confidence for dictionary match

    sorted_skills = sorted(skills_map.keys())
    avg_confidence = (
        sum(skills_map.values()) / len(skills_map) if skills_map else 0.0
    )

    return sorted_skills, round(avg_confidence, 2), raw_detected


def normalize_job_title(raw_title: str) -> str:
    """Normalize a raw title against standard role taxonomy."""
    clean = raw_title.strip().lower()
    clean = re.sub(r"\s+", " ", clean)
    if clean in ROLE_TAXONOMY:
        return ROLE_TAXONOMY[clean]
    # Check partial contains
    for key, canonical in ROLE_TAXONOMY.items():
        if key in clean:
            return canonical
    return raw_title.strip().title()


def extract_candidate_name(
    raw_text: str,
    comprehend_entities: Optional[List[Dict[str, Any]]] = None
) -> Tuple[str, float, List[str]]:
    """
    Extract candidate name:
    Spec 6.3: 'Highest-confidence PERSON near resume header; allow recruiter correction.'
    """
    flags: List[str] = []
    person_candidates: List[Tuple[str, float, int]] = []  # (name, score, offset)

    if comprehend_entities:
        for ent in comprehend_entities:
            if ent.get("Type") == "PERSON":
                score = float(ent.get("Score", 0.0))
                offset = int(ent.get("BeginOffset", 9999))
                person_candidates.append((ent["Text"].strip(), score, offset))

    # Prioritize entities within the first 500 characters (header area)
    header_candidates = [p for p in person_candidates if p[2] <= 500]
    if header_candidates:
        # Pick highest score near header
        best = max(header_candidates, key=lambda x: x[1])
        if best[1] < 0.70:
            flags.append("LOW_CONFIDENCE_NAME")
        return best[0], round(best[1], 2), flags

    if person_candidates:
        best = max(person_candidates, key=lambda x: x[1])
        flags.append("NAME_EXTRACTED_OUTSIDE_HEADER")
        if best[1] < 0.70:
            flags.append("LOW_CONFIDENCE_NAME")
        return best[0], round(best[1], 2), flags

    # Fallback: scan lines 1-3 of raw text for a reasonable 2-3 word name
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    for line in lines[:3]:
        # Simple name heuristic: 2-3 alphabetic words, no numbers/emails
        words = line.split()
        if 2 <= len(words) <= 4 and all(w.isalpha() for w in words):
            flags.append("HEURISTIC_NAME_FALLBACK")
            return line, 0.50, flags

    flags.append("NO_NAME_DETECTED")
    return "Candidate Name Not Found", 0.0, flags


def extract_employers(
    comprehend_entities: Optional[List[Dict[str, Any]]] = None
) -> Tuple[List[str], float]:
    """Extract unique employer names from ORGANIZATION entities."""
    if not comprehend_entities:
        return [], 0.0

    orgs: Dict[str, float] = {}
    for ent in comprehend_entities:
        if ent.get("Type") == "ORGANIZATION":
            name = ent.get("Text", "").strip()
            score = float(ent.get("Score", 0.0))
            lower_name = name.lower()

            if any(term in lower_name for term in DISALLOWED_EMPLOYERS):
                continue
            if len(name) < 2:
                continue

            if lower_name not in orgs or score > orgs[lower_name]:
                orgs[lower_name] = score

    # Canonicalize display
    employers = [k.title() for k in sorted(orgs.keys())]
    avg_score = sum(orgs.values()) / len(orgs) if orgs else 0.0
    return employers, round(avg_score, 2)


def extract_job_titles(
    comprehend_entities: Optional[List[Dict[str, Any]]] = None,
    raw_text: str = ""
) -> Tuple[List[str], float, List[str]]:
    """Extract and normalize candidate job titles from TITLE entities."""
    flags: List[str] = []
    titles: Dict[str, float] = {}

    if comprehend_entities:
        for ent in comprehend_entities:
            if ent.get("Type") == "TITLE":
                score = float(ent.get("Score", 0.0))
                norm = normalize_job_title(ent["Text"])
                if norm not in titles or score > titles[norm]:
                    titles[norm] = score

    # Supplementary check in raw text against taxonomy if nothing detected
    if not titles and raw_text:
        text_lower = raw_text.lower()
        for raw_k, canonical in ROLE_TAXONOMY.items():
            if re.search(r"\b" + re.escape(raw_k) + r"\b", text_lower):
                titles[canonical] = 0.80

    if not titles:
        flags.append("NO_TITLES_DETECTED")
        return [], 0.0, flags

    sorted_titles = sorted(titles.keys())
    avg_score = sum(titles.values()) / len(titles) if titles else 0.0
    if avg_score < 0.65:
        flags.append("LOW_CONFIDENCE_TITLE")

    return sorted_titles, round(avg_score, 2), flags


# ---------------------------------------------------------------------------
# Comprehensive Resume Normalization Pipeline (Main Entry)
# ---------------------------------------------------------------------------

def parse_and_normalize_resume(
    raw_text: str,
    comprehend_standard_entities: Optional[List[Dict[str, Any]]] = None,
    comprehend_skill_entities: Optional[List[Dict[str, Any]]] = None,
    job_id: str = "job_default",
    candidate_id: str = "cand_default",
    source_s3_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Main normalization entry point combining Comprehend entities and deterministic rules.

    Produces the normalized candidate profile JSON conforming to technical
    document Section 7 and Section 7.1.
    """
    all_flags: List[str] = []
    comprehend_standard_entities = comprehend_standard_entities or []
    comprehend_skill_entities = comprehend_skill_entities or []

    # Filter out DATE entities from standard entities for experience parser
    date_entities = [e for e in comprehend_standard_entities if e.get("Type") == "DATE"]

    # 1. Contact Information
    email, phone, links = extract_contact_info(raw_text)
    if not email:
        all_flags.append("MISSING_EMAIL")
    if not phone:
        all_flags.append("MISSING_PHONE")

    # 2. Candidate Name
    name, name_conf, name_flags = extract_candidate_name(raw_text, comprehend_standard_entities)
    all_flags.extend(name_flags)

    # 3. Skills
    combined_skill_ents = comprehend_skill_entities + [
        e for e in comprehend_standard_entities if e.get("Type") == "SKILL"
    ]
    skills, skill_conf, _ = extract_skills_from_entities_and_text(raw_text, combined_skill_ents)
    if not skills:
        all_flags.append("NO_SKILLS_DETECTED")

    # 4. Job Titles
    titles, title_conf, title_flags = extract_job_titles(comprehend_standard_entities, raw_text)
    all_flags.extend(title_flags)

    # 5. Employers
    employers, emp_conf = extract_employers(comprehend_standard_entities)

    # 6. Experience Years
    exp_years, detected_ranges, date_flags = calculate_experience_years(raw_text, date_entities)
    all_flags.extend(date_flags)

    # 7. Confidence Aggregation
    confidence_components = [c for c in [name_conf, skill_conf, title_conf, emp_conf] if c > 0.0]
    overall_conf = round(sum(confidence_components) / len(confidence_components), 2) if confidence_components else 0.50

    parsed_profile = {
        "name": name,
        "email": email,
        "phone": phone,
        "skills": skills,
        "job_titles": titles,
        "employers": employers,
        "experience_years": exp_years,
        "links": links,
        "date_ranges": detected_ranges,
        "confidence": {
            "overall": overall_conf,
            "name": name_conf,
            "skills": skill_conf,
            "job_titles": title_conf,
            "employers": emp_conf,
        },
        "flags": sorted(list(set(all_flags))),
    }

    candidate_record = {
        "job_id": job_id,
        "candidate_id": candidate_id,
        "source_s3_key": source_s3_key or f"jobs/{job_id}/resumes/{candidate_id}/source.txt",
        "parsed_profile": parsed_profile,
        "processing_status": "PARSED",
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    return candidate_record
