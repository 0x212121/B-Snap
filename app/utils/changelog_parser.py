from datetime import datetime
from typing import List, Dict, Tuple, Optional
import re


def parse_changelog_md(filepath: str = "CHANGELOG.md") -> List[Dict]:
    """Parse CHANGELOG.md into structured data."""
    changelog = []
    current_version = None
    current_section = None
    last_item = None

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for line in lines:
        line = line.rstrip()

        # Match version header with date
        version_match = re.match(r"^## \[(.*?)\] - (\d{4}-\d{2}-\d{2})", line)
        if version_match:
            version, raw_date = version_match.groups()
            date_obj = datetime.strptime(raw_date, "%Y-%m-%d")
            human_date = date_obj.strftime("%B %d, %Y")
            current_version = {
                "version": version,
                "date": human_date,
                "raw_date": raw_date,
                "added": [],
                "changed": [],
                "fixed": [],
                "deprecated": [],
                "removed": [],
                "security": [],
                "other": []
            }
            changelog.append(current_version)
            current_section = None
            last_item = None
            continue

        # Match Unreleased header
        unreleased_match = re.match(r"^## \[Unreleased\]", line, re.IGNORECASE)
        if unreleased_match:
            current_version = {
                "version": "Unreleased",
                "date": "Unreleased",
                "raw_date": None,
                "added": [],
                "changed": [],
                "fixed": [],
                "deprecated": [],
                "removed": [],
                "security": [],
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
            valid_sections = ["added", "changed", "fixed", "deprecated", "removed", "security"]
            current_section = section if section in valid_sections else "other"
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


def search_changelog(changelog: List[Dict], query: str) -> List[Dict]:
    """Filter changelog by search query."""
    if not query:
        return changelog
    
    query = query.lower()
    filtered = []
    
    for version in changelog:
        # Check version header
        if query in version["version"].lower() or query in version["date"].lower():
            filtered.append(version)
            continue
        
        # Check sections
        matching_sections = {}
        for section in ["added", "changed", "fixed", "deprecated", "removed", "security", "other"]:
            matching_items = []
            for item in version.get(section, []):
                if isinstance(item, dict):
                    if query in item["text"].lower():
                        matching_items.append(item)
                    else:
                        # Check subitems
                        matching_subs = [s for s in item.get("subitems", []) if query in s.lower()]
                        if matching_subs:
                            matching_items.append({
                                "text": item["text"],
                                "subitems": matching_subs
                            })
            if matching_items:
                matching_sections[section] = matching_items
        
        if matching_sections:
            new_version = version.copy()
            for section, items in matching_sections.items():
                new_version[section] = items
            filtered.append(new_version)
    
    return filtered


def paginate_changelog(
    changelog: List[Dict], 
    page: int = 1, 
    per_page: int = 5
) -> Tuple[List[Dict], int, int]:
    """Paginate changelog entries."""
    total = len(changelog)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    
    start = (page - 1) * per_page
    end = start + per_page
    
    return changelog[start:end], page, total_pages


def get_changelog_stats(changelog: List[Dict]) -> Dict:
    """Get statistics from changelog."""
    stats = {
        "total_versions": len(changelog),
        "total_added": 0,
        "total_changed": 0,
        "total_fixed": 0,
        "total_security": 0,
        "latest_version": changelog[0]["version"] if changelog else None,
        "latest_date": changelog[0]["date"] if changelog else None,
    }
    
    for version in changelog:
        stats["total_added"] += len(version.get("added", []))
        stats["total_changed"] += len(version.get("changed", []))
        stats["total_fixed"] += len(version.get("fixed", []))
        stats["total_security"] += len(version.get("security", []))
    
    return stats


def get_categories_for_version(version: Dict) -> List[Dict]:
    """Get available categories for a version with metadata."""
    categories = [
        {"key": "added", "label": "Added", "icon": "🟢", "color": "green", "bg": "bg-green-100", "text": "text-green-700", "dark_bg": "dark:bg-green-900/30", "dark_text": "dark:text-green-400"},
        {"key": "changed", "label": "Changed", "icon": "🟡", "color": "amber", "bg": "bg-amber-100", "text": "text-amber-700", "dark_bg": "dark:bg-amber-900/30", "dark_text": "dark:text-amber-400"},
        {"key": "fixed", "label": "Fixed", "icon": "🔧", "color": "blue", "bg": "bg-blue-100", "text": "text-blue-700", "dark_bg": "dark:bg-blue-900/30", "dark_text": "dark:text-blue-400"},
        {"key": "deprecated", "label": "Deprecated", "icon": "⚠️", "color": "orange", "bg": "bg-orange-100", "text": "text-orange-700", "dark_bg": "dark:bg-orange-900/30", "dark_text": "dark:text-orange-400"},
        {"key": "removed", "label": "Removed", "icon": "🗑️", "color": "red", "bg": "bg-red-100", "text": "text-red-700", "dark_bg": "dark:bg-red-900/30", "dark_text": "dark:text-red-400"},
        {"key": "security", "label": "Security", "icon": "🔒", "color": "purple", "bg": "bg-purple-100", "text": "text-purple-700", "dark_bg": "dark:bg-purple-900/30", "dark_text": "dark:text-purple-400"},
        {"key": "other", "label": "Other", "icon": "📄", "color": "gray", "bg": "bg-gray-100", "text": "text-gray-700", "dark_bg": "dark:bg-gray-700", "dark_text": "dark:text-gray-300"},
    ]
    
    return [c for c in categories if version.get(c["key"])]
