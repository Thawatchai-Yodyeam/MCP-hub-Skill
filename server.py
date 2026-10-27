"""Skills Hub MCP Server
====================
A lightweight, high-performance MCP server for managing agent skills on-demand.
Solves context-window bloat by replacing full system-prompt skill dumps with
progressive, sub-millisecond retrieval (list -> search -> get).
"""

import json
import os
import re
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from fastmcp import FastMCP

# Ensure UTF-8 stdout/stderr for Windows console environments
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Initialize FastMCP Server
mcp = FastMCP(
    "Skills Hub",
    instructions=(
        "On-demand skill retrieval hub. Reduces LLM token bloat by loading skills progressively. "
        "Workflow: search_skills(query) or list_available_skills() -> "
        "get_skill(skill_name, section='checklist') for just the actionable steps. "
        "Never repeat full skill documents back to the user."
    ),
)

# Configuration defaults
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

IGNORED_FILENAMES = {"readme.md", "license.md", "changelog.md"}
FRONTMATTER_RE = re.compile(r"^---[ \t]*\n(.*?)\n---[ \t]*(?:\n(.*))?$", re.DOTALL)
MAX_SEARCH_LIMIT = 50


def load_config() -> Dict[str, Any]:
    default_config = {
        "skills_directory": str(BASE_DIR / "skills"),
        "search_limit": 5,
        "max_description_length": 150,
        "token_saver_directive": True,
    }
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user_config = json.load(f)
            if isinstance(user_config, dict):
                default_config.update(user_config)
        except (OSError, ValueError) as e:
            print(f"[skills-hub] Ignoring invalid config.json: {e}", file=sys.stderr)

    # Support environment variable override
    env_dir = os.environ.get("SKILLS_DIR")
    skills_path = Path(env_dir) if env_dir else Path(default_config["skills_directory"])
    if not skills_path.is_absolute():
        # Resolve relative to BASE_DIR if relative path
        skills_path = BASE_DIR / skills_path
    default_config["skills_directory"] = str(skills_path.resolve())

    return default_config


CONFIG = load_config()
SKILLS_DIR = Path(CONFIG["skills_directory"])
DEFAULT_SEARCH_LIMIT = int(CONFIG["search_limit"])
MAX_DESCRIPTION_LENGTH = int(CONFIG["max_description_length"])


def split_frontmatter(raw_text: str) -> tuple[Dict[str, Any], str]:
    """Split YAML frontmatter from a markdown body. Returns ({}, text) when absent or invalid."""
    fm_match = FRONTMATTER_RE.match(raw_text)
    if fm_match:
        try:
            parsed_fm = yaml.safe_load(fm_match.group(1))
            return (parsed_fm if isinstance(parsed_fm, dict) else {}), (fm_match.group(2) or "").strip()
        except yaml.YAMLError:
            pass
    return {}, raw_text.strip()


def first_paragraph_line(body: str, max_len: int) -> str:
    """Fallback description: first line that is not a header, rule, fence, quote, or list item."""
    for line in body.splitlines():
        clean_line = line.strip()
        if clean_line and not clean_line.startswith(("#", "---", "```", ">", "-", "*")):
            return clean_line[:max_len]
    return ""


def truncate(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else text[: max_len - 3].rstrip() + "..."


class SkillIndexer:
    """Manages skill indexing with SQLite FTS5 in-memory database."""

    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self._lock = threading.Lock()
        self._file_mtimes: Dict[str, float] = {}
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_db()
        self.sync_index()

    def _init_db(self):
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE COLLATE NOCASE,
                category TEXT,
                description TEXT,
                tags TEXT,
                filepath TEXT UNIQUE,
                content TEXT,
                sections_json TEXT,
                mtime REAL
            )
        """)
        # FTS rowid mirrors skills.id so rows can be joined/deleted without name lookups
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS skills_fts USING fts5(
                name,
                category,
                description,
                tags,
                content,
                tokenize='unicode61 remove_diacritics 2'
            )
        """)
        self.conn.commit()

    @staticmethod
    def _parse_sections(markdown_text: str) -> Dict[str, str]:
        """Extract H2/H3 sections from markdown text, ignoring headers inside code fences."""
        sections: Dict[str, str] = {}
        current_section = "overview"
        lines: List[str] = []
        in_fence = False

        def flush():
            text = "\n".join(lines).strip()
            if not text:
                return
            key = current_section.lower()
            # Merge duplicate headers instead of silently overwriting
            sections[key] = f"{sections[key]}\n\n{text}" if key in sections else text

        for line in markdown_text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("```", "~~~")):
                in_fence = not in_fence
            header_match = None if in_fence else re.match(r"^(#{2,3})\s+(.*?)\s*#*$", stripped)
            if header_match:
                flush()
                lines = []
                current_section = header_match.group(2).strip()
            else:
                lines.append(line)

        flush()
        return sections

    @staticmethod
    def _parse_markdown_file(file_path: Path) -> Optional[Dict[str, Any]]:
        """Parse YAML frontmatter and markdown body from file."""
        # Ignore documentation and license files
        if file_path.name.lower() in IGNORED_FILENAMES:
            return None

        try:
            raw_text = file_path.read_text(encoding="utf-8")
            mtime = file_path.stat().st_mtime
        except (OSError, UnicodeDecodeError):
            return None

        frontmatter, body = split_frontmatter(raw_text)

        # Extract or fallback fields
        if file_path.stem.lower() == "skill":
            name = frontmatter.get("name") or file_path.parent.name
            parent_cat = file_path.parent.parent.name
            category = frontmatter.get("category") or (parent_cat if parent_cat and parent_cat != "skills" else file_path.parent.name)
        else:
            name = frontmatter.get("name") or file_path.stem
            parent_cat = file_path.parent.name
            category = frontmatter.get("category") or (parent_cat if parent_cat and parent_cat != "skills" else "general")

        description = str(frontmatter.get("description") or "").strip()
        if description in ("", "---"):
            description = first_paragraph_line(body, MAX_DESCRIPTION_LENGTH)

        raw_tags = frontmatter.get("tags") or []
        if isinstance(raw_tags, list):
            tags_str = ", ".join(str(t) for t in raw_tags)
        else:
            tags_str = str(raw_tags)

        sections = SkillIndexer._parse_sections(body)

        return {
            "name": str(name).strip(),
            "category": str(category).strip().lower(),
            "description": description,
            "tags": tags_str,
            "filepath": str(file_path.resolve()),
            "content": body,
            "sections_json": json.dumps(sections, ensure_ascii=False),
            "mtime": mtime,
        }

    def _scan_files(self) -> Dict[str, Path]:
        current_files: Dict[str, Path] = {}
        # Sorted for deterministic collision naming across restarts
        for p in sorted(self.skills_dir.rglob("*.md")):
            # Filter out hidden folders (.git, .obsidian, etc.) below the skills root only,
            # so a skills dir that itself lives under e.g. ~/.gemini still works
            if any(part.startswith(".") for part in p.relative_to(self.skills_dir).parts):
                continue
            current_files[str(p.resolve())] = p
        return current_files

    def _delete_by_filepath(self, cursor: sqlite3.Cursor, fpath: str):
        cursor.execute("SELECT id FROM skills WHERE filepath = ?", (fpath,))
        row = cursor.fetchone()
        if row:
            cursor.execute("DELETE FROM skills WHERE id = ?", (row["id"],))
            cursor.execute("DELETE FROM skills_fts WHERE rowid = ?", (row["id"],))

    def sync_index(self):
        """Re-index files if modified or added, remove deleted files. Sub-millisecond if unchanged."""
        if not self.skills_dir.exists():
            self.skills_dir.mkdir(parents=True, exist_ok=True)

        with self._lock:
            current_files = self._scan_files()
            cursor = self.conn.cursor()

            # Check for deleted files
            for fpath in list(self._file_mtimes):
                if fpath not in current_files:
                    self._delete_by_filepath(cursor, fpath)
                    del self._file_mtimes[fpath]

            # Check for new or updated files
            for fpath_str, path_obj in current_files.items():
                try:
                    mtime = path_obj.stat().st_mtime
                except OSError:
                    continue

                if self._file_mtimes.get(fpath_str) == mtime:
                    continue

                # Record mtime even for unparseable files so they aren't re-read on every call
                self._file_mtimes[fpath_str] = mtime
                # Drop the old record by path first, so a renamed skill doesn't leave a stale entry
                self._delete_by_filepath(cursor, fpath_str)

                parsed = self._parse_markdown_file(path_obj)
                if not parsed or not parsed["name"]:
                    continue

                # Check for name collisions across different files
                cursor.execute("SELECT 1 FROM skills WHERE name = ?", (parsed["name"],))
                if cursor.fetchone():
                    parsed["name"] = f"{parsed['category']}/{parsed['name']}"
                    cursor.execute("SELECT 1 FROM skills WHERE name = ?", (parsed["name"],))
                    if cursor.fetchone():
                        print(f"[skills-hub] Skipping duplicate skill '{parsed['name']}' in {fpath_str}", file=sys.stderr)
                        continue

                cursor.execute("""
                    INSERT INTO skills (name, category, description, tags, filepath, content, sections_json, mtime)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    parsed["name"],
                    parsed["category"],
                    parsed["description"],
                    parsed["tags"],
                    parsed["filepath"],
                    parsed["content"],
                    parsed["sections_json"],
                    parsed["mtime"],
                ))
                cursor.execute("""
                    INSERT INTO skills_fts (rowid, name, category, description, tags, content)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    cursor.lastrowid,
                    parsed["name"],
                    parsed["category"],
                    parsed["description"],
                    parsed["tags"],
                    parsed["content"],
                ))

            self.conn.commit()

    def list_skills(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        self.sync_index()
        with self._lock:
            cursor = self.conn.cursor()
            if category and category.strip():
                cursor.execute(
                    "SELECT name, category, description, tags FROM skills WHERE LOWER(category) = LOWER(?) ORDER BY name",
                    (category.strip(),)
                )
            else:
                cursor.execute("SELECT name, category, description, tags FROM skills ORDER BY category, name")
            return [dict(row) for row in cursor.fetchall()]

    def get_categories_summary(self) -> List[Dict[str, Any]]:
        self.sync_index()
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT c.category, c.count, GROUP_CONCAT(r.name, ', ') AS sample_skills
                FROM (SELECT category, COUNT(*) AS count FROM skills GROUP BY category) c
                JOIN (
                    SELECT category, name, ROW_NUMBER() OVER (PARTITION BY category ORDER BY name) AS rn
                    FROM skills
                ) r ON r.category = c.category AND r.rn <= 3
                GROUP BY c.category
                ORDER BY c.category
            """)
            return [dict(row) for row in cursor.fetchall()]

    def _fts_search(self, cursor: sqlite3.Cursor, fts_expr: str, limit: int) -> List[Dict[str, Any]]:
        try:
            # Weighted search: name (10x), category (5x), description (5x), tags (8x), content (1x)
            cursor.execute("""
                SELECT s.name, s.category, s.description, s.tags, s.sections_json,
                       bm25(skills_fts, 10.0, 5.0, 5.0, 8.0, 1.0) as rank
                FROM skills_fts
                JOIN skills s ON skills_fts.rowid = s.id
                WHERE skills_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (fts_expr, limit))
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.OperationalError:
            return []

    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        self.sync_index()
        clean_query = query.strip()
        if not clean_query:
            return []

        # Quote every token so FTS5 operators (AND/OR/NOT/NEAR) and punctuation are treated literally
        tokens = re.findall(r"\w+", clean_query)
        if not tokens:
            return []
        quoted = [f'"{t}"' for t in tokens]
        quoted[-1] += "*"  # Prefix matching on last token

        with self._lock:
            cursor = self.conn.cursor()
            # Strict match first (all terms), then relax to any term
            results = self._fts_search(cursor, " AND ".join(quoted), limit)
            if not results and len(quoted) > 1:
                results = self._fts_search(cursor, " OR ".join(quoted), limit)

            # Fallback to LIKE keyword search (e.g. scripts without word boundaries like Thai)
            if not results:
                like_query = f"%{clean_query}%"
                cursor.execute("""
                    SELECT name, category, description, tags, sections_json, 99.0 as rank
                    FROM skills
                    WHERE name LIKE ? OR description LIKE ? OR tags LIKE ? OR content LIKE ?
                    ORDER BY name
                    LIMIT ?
                """, (like_query, like_query, like_query, like_query, limit))
                results = [dict(row) for row in cursor.fetchall()]

            return results

    def get_skill(self, name: str) -> Optional[Dict[str, Any]]:
        self.sync_index()
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM skills WHERE name = ?", (name.strip(),))
            row = cursor.fetchone()
            return dict(row) if row else None


# Instantiate indexer
indexer = SkillIndexer(SKILLS_DIR)

TOKEN_DIRECTIVE = """
══════════════════════════════════════════════════════════════════════════════
🛑 TOKEN-SAVING DIRECTIVE FOR AI AGENT:
1. Do NOT quote, regurgitate, or repeat this full skill document back to the user.
2. Extract ONLY the relevant checklist items or actionable steps for your current task.
3. Keep the conversation context clean and concise for subsequent operations.
══════════════════════════════════════════════════════════════════════════════
""".strip()

READ_ONLY = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}


def load_sections(row: Dict[str, Any]) -> Dict[str, str]:
    try:
        return json.loads(row.get("sections_json") or "{}")
    except ValueError:
        return {}


def match_section(sections: Dict[str, str], query: str) -> Optional[str]:
    """Find the best section key: exact > prefix > substring (either direction)."""
    target = query.strip().lower()
    keys = list(sections)
    for pick in (
        lambda k: k == target,
        lambda k: k.startswith(target),
        lambda k: target in k,
        lambda k: k != "overview" and k in target,
    ):
        for k in keys:
            if pick(k):
                return k
    return None


@mcp.tool(annotations=READ_ONLY)
def list_available_skills(
    category: Optional[str] = None,
    limit: int = 30,
    summary_only: bool = False
) -> str:
    """List available skills. Automatically switches to compact category overview when total > 25 to save tokens.

    Args:
        category: Optional category filter (e.g. 'web-development', 'devops', 'performance')
        limit: Maximum number of skills to list when displaying details (default: 30)
        summary_only: If True, returns only categories and sample skill names.
    """
    limit = max(1, limit)
    category = category.strip() if category else None

    # If no category filter and high skill count (or explicitly requested summary_only), show category overview
    if not category:
        categories = indexer.get_categories_summary()
        total_skills = sum(c["count"] for c in categories)
        if summary_only or total_skills > 25:
            lines = [
                f"### Skills Hub Overview ({total_skills} skills across {len(categories)} categories)",
                "",
                "Use `category='<name>'` to list specific skills, or `search_skills('query')` to find relevant tools.",
                ""
            ]
            for cat in categories:
                sample = f" (e.g., {cat['sample_skills']})" if cat.get("sample_skills") else ""
                lines.append(f"- **`[{cat['category'].upper()}]`** ({cat['count']} skills){sample}")

            lines.append("")
            lines.append("💡 *Tip: Call `list_available_skills(category='<name>')` to expand a category, or `search_skills('keywords')`.*")
            return "\n".join(lines)

    skills = indexer.list_skills(category=category)
    if not skills:
        if category:
            return f"No skills found in category '{category}'. Call `list_available_skills(summary_only=True)` to see categories."
        return "No skills found. Add markdown files to the skills directory or call `add_or_update_skill`."

    total = len(skills)
    display_skills = skills[:limit]
    lines = [f"### Available Skills Hub Index ({len(display_skills)} of {total}{' in ' + category if category else ''})", ""]

    current_cat = None
    for s in display_skills:
        cat = s["category"].upper()
        if cat != current_cat:
            current_cat = cat
            lines.append(f"**[{current_cat}]**")

        tags_info = f" `[{s['tags']}]`" if s.get("tags") else ""
        lines.append(f"- **`{s['name']}`**:{tags_info} {truncate(s['description'], 120)}")

    if total > limit:
        lines.append(f"\n*...and {total - limit} more skills. Call with limit={total} or filter by category.*")

    lines.append("")
    lines.append("💡 *Tip: When you need full details, call `get_skill(skill_name)` on demand.*")
    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY)
def search_skills(query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> str:
    """Search skills by keyword, tag, or topic using fast Full-Text Search (FTS5).

    Args:
        query: Keywords to search (e.g., 'docker security', 'sql index', 'api rest')
        limit: Maximum number of results to return (default from config, max 50)
    """
    limit = min(max(1, limit), MAX_SEARCH_LIMIT)
    results = indexer.search(query=query, limit=limit)
    if not results:
        return f"No skills matched the query: '{query}'. Try broader keywords or use `list_available_skills()`."

    output = [f"### Search Results for '{query}' (Found: {len(results)})", ""]

    for i, r in enumerate(results, 1):
        sec_names = [k.title() for k in load_sections(r) if k != "overview"]
        sec_hint = f" (Sections: {', '.join(sec_names)})" if sec_names else ""

        output.append(f"{i}. **`{r['name']}`** [{r['category']}]{sec_hint}")
        output.append(f"   Summary: {truncate(r['description'], MAX_DESCRIPTION_LENGTH)}")
        if r.get("tags"):
            output.append(f"   Tags: {r['tags']}")
        output.append("")

    output.append("👉 *Next step: Call `get_skill(skill_name, section='checklist')` to view specific instructions.*")
    return "\n".join(output)


@mcp.tool(annotations=READ_ONLY)
def get_skill(skill_name: str, section: Optional[str] = None) -> str:
    """Retrieve instructions and checklist for a specific skill on-demand.

    Args:
        skill_name: The name identifier of the skill (e.g., 'sql-optimization', 'api-design')
        section: Optional specific section to extract (e.g., 'checklist', 'anti-patterns') to save maximum tokens.
    """
    skill = indexer.get_skill(skill_name)
    if not skill:
        suggestions = indexer.search(skill_name, limit=3)
        names = ", ".join(f"`{s['name']}`" for s in suggestions)
        hint = f" Did you mean: {names}?" if suggestions else ""
        return f"Skill '{skill_name}' not found.{hint} Use `list_available_skills()` or `search_skills()` to find valid skill names."

    sections = load_sections(skill)
    header_info = f"# Skill: `{skill['name']}` [{skill['category']}]"

    if section and section.strip():
        key = match_section(sections, section)
        if key is None:
            available_secs = ", ".join(f"`{k}`" for k in sections if k != "overview")
            return f"Section '{section}' not found in skill '{skill['name']}'.\nAvailable sections: {available_secs or 'None'}\n\nCall `get_skill('{skill['name']}')` without a section to read the full skill."
        content_to_return = f"{header_info} > Section: `{key.title()}`\n\n{sections[key]}"
    else:
        content_to_return = f"{header_info}\n\n{skill['content']}"

    # Prepend Token Saver Directive
    if CONFIG.get("token_saver_directive", True):
        return f"{TOKEN_DIRECTIVE}\n\n{content_to_return}"
    return content_to_return


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
def add_or_update_skill(
    name: str,
    description: str,
    content: str,
    category: str = "general",
    tags: str = "",
) -> str:
    """Create a new skill or update an existing skill in the Skills Hub repository.

    If a skill with the same name already exists, its file is updated in place.

    Args:
        name: Short, hyphenated skill name (e.g. 'react-performance')
        description: 1-2 line description of what the skill covers
        content: Markdown content with instructions, checklist, and guidelines (use '## Checklist' etc. for sections)
        category: Category directory (e.g. 'frontend', 'backend', 'devops')
        tags: Comma-separated tags (e.g. 'react, vite, web')
    """
    # Clean name (strips path separators and dots, preventing directory traversal)
    clean_name = re.sub(r"[^\w\-]", "", name.lower().strip())
    clean_cat = re.sub(r"[^\w\-]", "", category.lower().strip()) or "general"

    if not clean_name:
        return "Error: Skill name cannot be empty and must contain alphanumeric characters."
    if not content.strip():
        return "Error: Skill content cannot be empty."

    existing = indexer.get_skill(clean_name)
    if existing:
        target_file = Path(existing["filepath"])
    else:
        target_dir = SKILLS_DIR / clean_cat
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / f"{clean_name}.md"

    # Split tags into clean list
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    # safe_dump quotes values containing ':' or '#' so the frontmatter always round-trips
    frontmatter = yaml.safe_dump(
        {
            "name": clean_name,
            "category": clean_cat,
            "description": " ".join(description.split()),
            "tags": tag_list,
        },
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=None,
    )
    target_file.write_text(f"---\n{frontmatter}---\n\n{content.strip()}\n", encoding="utf-8")

    # Trigger instant re-index
    indexer.sync_index()

    try:
        shown_path = target_file.relative_to(BASE_DIR)
    except ValueError:
        shown_path = target_file
    note = " (updated existing skill)" if existing else ""
    return f"Skill '{clean_name}' successfully saved to '{shown_path}' and indexed{note}."


def main():
    # Run FastMCP stdio transport
    mcp.run()


if __name__ == "__main__":
    main()
