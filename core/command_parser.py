"""
Very small rule-based parser for spoken/typed commands.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.logger import get_logger

logger = get_logger(__name__)

CATEGORY_KEYWORDS: dict[str, str] = {
    "নোটিশ": "নোটিশ",
    "অফিস আদেশ": "অফিস আদেশ",
    "আদেশ": "অফিস আদেশ",
    "স্মারক": "স্মারক",
    "প্রতিবেদন": "প্রতিবেদন",
    "রিপোর্ট": "প্রতিবেদন",
}


@dataclass
class ParsedCommand:
    category: str | None = None
    field_values: dict[str, str] = field(default_factory=dict)
    raw_text: str = ""


def detect_category(text: str) -> str | None:
    for keyword, canonical in CATEGORY_KEYWORDS.items():
        if keyword in text:
            return canonical
    return None


def extract_field_values(text: str, placeholder_names: list[str]) -> dict[str, str]:
    positions: list[tuple[int, int, str]] = []
    for name in placeholder_names:
        match = re.search(re.escape(name), text)
        if match:
            positions.append((match.end(), match.start(), name))

    positions.sort(key=lambda item: item[1])

    values: dict[str, str] = {}
    for i, (value_start, _name_start, name) in enumerate(positions):
        value_end = positions[i + 1][1] if i + 1 < len(positions) else len(text)
        values[name] = text[value_start:value_end].strip(" :।,")
    return values


def parse_command(text: str, known_placeholders: list[str]) -> ParsedCommand:
    text = text.strip()
    category = detect_category(text)
    values = extract_field_values(text, known_placeholders)
    logger.info("Parsed command: category=%s fields=%s", category, list(values.keys()))
    return ParsedCommand(category=category, field_values=values, raw_text=text)
