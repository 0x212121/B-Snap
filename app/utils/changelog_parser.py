from datetime import datetime
from typing import List, Dict
import re

def parse_changelog_md(filepath: str = "CHANGELOG.md") -> List[Dict]:
    changelog = []
    current_version = None
    current_section = None

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for line in lines:
        line = line.strip()

        # Match version header
        version_match = re.match(r"## \[(.*?)\] - (\d{4}-\d{2}-\d{2})", line)
        if version_match:
            version, raw_date = version_match.groups()
            date_obj = datetime.strptime(raw_date, "%Y-%m-%d")
            human_date = date_obj.strftime("%B %d, %Y")  # Contoh: July 21, 2025
            current_version = {
                "version": version,
                "date": human_date,
                "added": [],
                "changed": [],
                "fixed": [],
                "other": []
            }
            changelog.append(current_version)
            continue

        # Match section headers (### Added, Changed, etc)
        section_match = re.match(r"### (\w+)", line)
        if section_match:
            section = section_match.group(1).lower()
            current_section = section if section in current_version else "other"
            continue

        # Match list item
        if line.startswith("-") and current_section and current_version:
            item = line.lstrip("- ").strip()
            current_version[current_section].append(item)

    return changelog
