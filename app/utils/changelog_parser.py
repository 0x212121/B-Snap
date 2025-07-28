from datetime import datetime
from typing import List, Dict
import re

def parse_changelog_md(filepath: str = "CHANGELOG.md") -> List[Dict]:
    changelog = []
    current_version = None
    current_section = None
    last_item = None  # to track last top-level item for sub-items

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for line in lines:
        line = line.rstrip()

        # Match version header
        version_match = re.match(r"^## \[(.*?)\] - (\d{4}-\d{2}-\d{2})", line)
        if version_match:
            version, raw_date = version_match.groups()
            date_obj = datetime.strptime(raw_date, "%Y-%m-%d")
            human_date = date_obj.strftime("%B %d, %Y")
            current_version = {
                "version": version,
                "date": human_date,
                "added": [],
                "changed": [],
                "fixed": [],
                "other": []
            }
            changelog.append(current_version)
            current_section = None
            last_item = None
            continue

        # Match section headers
        section_match = re.match(r"^### (\w+)", line)
        if section_match:
            section = section_match.group(1).lower()
            current_section = section if section in current_version else "other"
            last_item = None
            continue

        if not current_section or not current_version:
            continue

        # Match sub-item (2-space indent)
        sub_item_match = re.match(r"^  - (.+)", line)
        if sub_item_match and last_item:
            sub_item = sub_item_match.group(1).strip()
            if isinstance(last_item, dict) and "subitems" in last_item:
                last_item["subitems"].append(sub_item)
            continue

        # Match top-level list item
        top_item_match = re.match(r"^- (.+)", line)
        if top_item_match:
            item_text = top_item_match.group(1).strip()
            last_item = {"text": item_text, "subitems": []}
            current_version[current_section].append(last_item)
            continue

    return changelog
