"""Skills Importer Utility
========================
Scan existing Antigravity or local skills folders (containing SKILL.md or *.md)
and import them into the Skills Hub repository.

Usage:
    python scripts/import_skills.py
    python scripts/import_skills.py "C:\\path\\to\\custom\\skills"
"""

import os
import re
import shutil
import sys
from pathlib import Path
import yaml

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent.parent
TARGET_SKILLS_DIR = BASE_DIR / "skills"

# Known default Antigravity / Gemini paths
DEFAULT_SEARCH_PATHS = [
    Path.home() / ".gemini" / "config" / "skills",
    Path.home() / ".gemini" / "antigravity" / "builtin" / "skills",
]


def parse_frontmatter(content: str):
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if fm_match:
        try:
            fm = yaml.safe_load(fm_match.group(1))
            return fm if isinstance(fm, dict) else {}, fm_match.group(2).strip()
        except Exception:
            pass
    return {}, content.strip()


def import_skill_file(source_file: Path, category_override: str = None):
    try:
        with open(source_file, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"  [!] Failed to read {source_file}: {e}")
        return False

    fm, body = parse_frontmatter(content)

    skill_name = fm.get("name") or (source_file.parent.name if source_file.name.lower() == "skill.md" else source_file.stem)
    skill_name = re.sub(r"[^\w\-]", "", str(skill_name).lower().strip())
    if not skill_name:
        return False

    category = category_override or fm.get("category") or "imported"
    description = fm.get("description", "")
    if not description or str(description).strip() in ["---", ""]:
        for line in body.splitlines():
            line_str = line.strip()
            if line_str and not line_str.startswith(("#", "---", "```", ">", "-")):
                description = line_str[:120]
                break

    tags = fm.get("tags", [category, "imported"])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    target_dir = TARGET_SKILLS_DIR / category
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / f"{skill_name}.md"

    # safe_dump quotes values containing ':', '#', newlines, etc.
    frontmatter = yaml.safe_dump(
        {"name": skill_name, "category": category, "description": str(description).strip(), "tags": tags if isinstance(tags, list) else []},
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=None,
        width=float("inf"),
    )
    new_file_content = f"---\n{frontmatter}---\n\n{body}\n"
    with open(target_file, "w", encoding="utf-8") as f:
        f.write(new_file_content)

    print(f"  [+] Imported '{skill_name}' -> {target_file.relative_to(BASE_DIR)}")
    return True


def scan_and_import(source_dir: Path):
    if not source_dir.exists():
        print(f"[-] Directory does not exist: {source_dir}")
        return

    print(f"\n[*] Scanning: {source_dir}")
    imported_count = 0

    # Look for SKILL.md or *.md
    for skill_file in source_dir.rglob("*.md"):
        # Ignore readme / license
        if skill_file.name.lower() in ["readme.md", "license.md", "changelog.md"]:
            continue
        category = skill_file.parent.name if skill_file.name.lower() == "skill.md" else "imported"
        if import_skill_file(skill_file, category_override=category):
            imported_count += 1

    print(f"[✓] Finished importing {imported_count} skills from {source_dir.name}")


def main():
    if len(sys.argv) > 1:
        custom_path = Path(sys.argv[1])
        scan_and_import(custom_path)
    else:
        print("Starting Skills Importer...")
        for p in DEFAULT_SEARCH_PATHS:
            if p.exists():
                scan_and_import(p)

    print("\nImport process completed! You can run `python server.py` or inspect `skills/`.")


if __name__ == "__main__":
    main()
