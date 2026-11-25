"""Shared O*NET skill normalization and occupation categories."""

from __future__ import annotations

import re


_ALIASES = {
    "api design": "api design",
    "application programming interface": "api design",
    "application programming interfaces": "api design",
    "application programming interfaces api": "api design",
    "atlassian jira": "bug tracking",
    "rest api": "api design",
    "rest apis": "api design",
    "restful api": "api design",
    "restful apis": "api design",
    "bug tracking": "bug tracking",
    "jira": "bug tracking",
    "data visualization": "data visualization",
    "data visualization software": "data visualization",
    "power bi": "data visualization",
    "powerbi": "data visualization",
    "tableau": "data visualization",
    "microsoft power bi": "data visualization",
    "excel": "excel",
    "microsoft excel": "excel",
    "ms excel": "excel",
    "javascript": "javascript",
    "javascript framework software": "javascript",
    "js": "javascript",
    "git": "git",
    "github": "git",
    "microsoft sql server": "sql",
    "mysql": "sql",
    "postgres": "sql",
    "postgresql": "sql",
    "sql": "sql",
    "sql server": "sql",
    "sqlite": "sql",
    "selenium": "test automation",
    "pytest": "test automation",
    "python": "python",
    "python programming": "python",
    "manual testing": "testing fundamentals",
    "testing fundamentals": "testing fundamentals",
    "test automation": "test automation",
}

_CANONICAL_LABELS = {
    "api design": "API Design",
    "bug tracking": "Bug Tracking",
    "data visualization": "Data Visualization",
    "excel": "Excel",
    "javascript": "JavaScript",
    "sql": "SQL",
    "testing fundamentals": "Testing Fundamentals",
    "test automation": "Test Automation",
}

_CATEGORY_NAMES = {
    "11": "Management",
    "13": "Business and Financial Operations",
    "15": "Computer and Mathematical",
    "17": "Architecture and Engineering",
    "19": "Life, Physical, and Social Science",
    "21": "Community and Social Service",
    "23": "Legal",
    "25": "Educational Instruction and Library",
    "27": "Arts, Design, Entertainment, Sports, and Media",
    "29": "Healthcare Practitioners and Technical",
    "31": "Healthcare Support",
    "33": "Protective Service",
    "35": "Food Preparation and Serving Related",
    "37": "Building and Grounds Cleaning and Maintenance",
    "39": "Personal Care and Service",
    "41": "Sales and Related",
    "43": "Office and Administrative Support",
    "45": "Farming, Fishing, and Forestry",
    "47": "Construction and Extraction",
    "49": "Installation, Maintenance, and Repair",
    "51": "Production",
    "53": "Transportation and Material Moving",
    "55": "Military Specific",
}


def normalize_skill_name(value: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", " ", value.casefold()).strip()


def skill_key(value: str) -> str:
    normalized = normalize_skill_name(value)
    return _ALIASES.get(normalized, normalized)


def canonical_skill_label(value: str) -> str:
    return _CANONICAL_LABELS.get(skill_key(value), value.strip())


def occupation_category(code: str) -> tuple[str, str]:
    category_code = code[:2]
    return category_code, _CATEGORY_NAMES.get(category_code, "Other Occupations")
