"""Skills Importer Utility
========================
Scan existing Antigravity or local skills folders (containing SKILL.md or *.md)
and import them into the Skills Hub repository.

A folder with a SKILL.md is imported whole (references/, scripts/, assets/ ...)
into skills/<category>/<skill-name>/. Any other .md file becomes a single-file
skill at skills/<category>/<skill-name>.md.

Usage:
    python scripts/import_skills.py
    python scripts/import_skills.py "C:\\path\\to\\custom\\skills"
"""

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

IGNORED_FILES = ["readme.md", "license.md", "changelog.md"]


def parse_frontmatter(content: str):
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if fm_match:
        try:
            fm = yaml.safe_load(fm_match.group(1))
            return fm if isinstance(fm, dict) else {}, fm_match.group(2).strip()
        except Exception:
            pass
    return {}, content.strip()


def clean_slug(value) -> str:
    """Lowercase and keep only letters, digits, '_' and '-', so values can't escape the skills folder."""
    return re.sub(r"[^\w\-]", "", str(value).lower().strip())


def is_hidden(path: Path, root: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(root).parts)


def import_skill_file(source_file: Path, source_root: Path):
    try:
        with open(source_file, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"  [!] Failed to read {source_file}: {e}")
        return False

    fm, body = parse_frontmatter(content)

    # A folder skill (<category>/<skill>/SKILL.md) is named after its folder; a plain file after the file
    is_folder_skill = source_file.name.lower() == "skill.md" and source_file.parent != source_root
    skill_path = source_file.parent if is_folder_skill else source_file.with_suffix("")

    skill_name = clean_slug(fm.get("name") or skill_path.name)
    if not skill_name:
        return False

    # Category: frontmatter first, then the folder holding the skill (unless that's the scanned root)
    folder_category = skill_path.parent.name if skill_path.parent != source_root else ""
    category = clean_slug(fm.get("category") or folder_category) or "imported"

    description = fm.get("description", "")
    if not description or str(description).strip() in ["---", ""]:
        for line in body.splitlines():
            line_str = line.strip()
            if line_str and not line_str.startswith(("#", "---", "```", ">", "-")):
                description = line_str[:120]
                break

    tags = fm.get("tags") or [category, "imported"]
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    tags = list(dict.fromkeys(tags)) if isinstance(tags, list) else []

    if is_folder_skill:
        # Copy the whole folder so bundled references/scripts keep working
        target_dir = TARGET_SKILLS_DIR / category / skill_name
        shutil.copytree(source_file.parent, target_dir, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".*"))
        # Replace the copied original (e.g. 'skill.md') with the normalized SKILL.md written below
        (target_dir / source_file.name).unlink(missing_ok=True)
        target_file = target_dir / "SKILL.md"
    else:
        target_dir = TARGET_SKILLS_DIR / category
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / f"{skill_name}.md"

    # Normalized keys first; other frontmatter keys (license, allowed-tools, ...) are kept after them.
    # safe_dump quotes values containing ':', '#', newlines, etc.
    metadata = {"name": skill_name, "category": category, "description": str(description).strip(), "tags": tags}
    metadata.update({k: v for k, v in fm.items() if k not in metadata})
    frontmatter = yaml.safe_dump(
        metadata,
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

    md_files = sorted(p for p in source_dir.rglob("*.md") if not is_hidden(p, source_dir))
    skill_dirs = {p.parent for p in md_files if p.name.lower() == "skill.md" and p.parent != source_dir}

    for skill_file in md_files:
        # Ignore readme / license
        if skill_file.name.lower() in IGNORED_FILES:
            continue
        # Other .md files inside a skill folder are bundled resources, copied along with its SKILL.md
        if skill_file.name.lower() != "skill.md" and any(parent in skill_dirs for parent in skill_file.parents):
            continue
        if import_skill_file(skill_file, source_dir):
            imported_count += 1

    print(f"[✓] Finished importing {imported_count} skills from {source_dir.name}")


def main():
    if len(sys.argv) > 1:
        custom_path = Path(sys.argv[1]).resolve()
        scan_and_import(custom_path)
    else:
        print("Starting Skills Importer...")
        for p in DEFAULT_SEARCH_PATHS:
            if p.exists():
                scan_and_import(p)

    print("\nImport process completed! You can run `python server.py` or inspect `skills/`.")


if __name__ == "__main__":
    main()
