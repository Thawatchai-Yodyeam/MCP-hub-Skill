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
import unicodedata
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
    instructions="On-demand skill retrieval hub. Reduces LLM token bloat by loading skills progressively."
)

# Configuration defaults
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"


DEFAULT_CONFIG: Dict[str, Any] = {
    "skills_directory": str(BASE_DIR / "skills"),
    "search_limit": 5,
    "max_description_length": 150,
    "token_saver_directive": True,
}


def load_config() -> Dict[str, Any]:
    default_config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user_config = json.load(f)
                default_config.update(user_config)
        except Exception:
            pass

    # Fall back to defaults for numbers that are missing or invalid
    for key in ("search_limit", "max_description_length"):
        try:
            default_config[key] = max(1, int(default_config[key]))
        except (TypeError, ValueError):
            default_config[key] = DEFAULT_CONFIG[key]
    default_config["token_saver_directive"] = bool(default_config["token_saver_directive"])

    # Support environment variable override
    env_dir = os.environ.get("SKILLS_DIR")
    if env_dir:
        default_config["skills_directory"] = env_dir
    else:
        # Resolve relative to BASE_DIR if relative path
        skills_path = Path(default_config["skills_directory"])
        if not skills_path.is_absolute():
            default_config["skills_directory"] = str((BASE_DIR / skills_path).resolve())

    return default_config


CONFIG = load_config()
SKILLS_DIR = Path(CONFIG["skills_directory"])
SEARCH_LIMIT: int = CONFIG["search_limit"]
MAX_DESCRIPTION_LENGTH: int = CONFIG["max_description_length"]
TOKEN_SAVER_DIRECTIVE: bool = CONFIG["token_saver_directive"]


def shorten(text: str, max_length: int = MAX_DESCRIPTION_LENGTH) -> str:
    """Trim text to max_length characters, marking the cut with '...'."""
    return text if len(text) <= max_length else text[: max(max_length - 3, 0)].rstrip() + "..."


def is_hidden(path: Path, root: Path) -> bool:
    """True if any part of path below root starts with '.' (.git, .obsidian, ...)."""
    return any(part.startswith(".") for part in path.relative_to(root).parts)


class SkillIndexer:
    """Manages skill indexing with SQLite FTS5 in-memory database."""

    # Field weights for ranking: name, category, description, tags, content (same order as skills_fts)
    FIELD_WEIGHTS = (("name", 10.0), ("category", 5.0), ("description", 5.0), ("tags", 8.0), ("content", 1.0))

    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self._lock = threading.Lock()
        with self._lock:
            self.conn = sqlite3.connect(":memory:", check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self._init_db()
            self._file_mtimes: Dict[str, float] = {}
        self.sync_index()

    def _init_db(self):
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                name TEXT PRIMARY KEY,
                category TEXT,
                description TEXT,
                tags TEXT,
                filepath TEXT,
                content TEXT,
                sections_json TEXT,
                mtime REAL
            )
        """)
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
    def _parse_sections(markdown_text: str) -> List[Dict[str, Any]]:
        """Split markdown into H2/H3 sections, in document order.

        An H2 section includes its H3 subsections. An H3 section's 'path' names its
        parent H2 (e.g. 'Frontend > Checklist'). Headings inside code fences are
        ignored. Text before the first H2/H3, minus H1 titles, becomes 'Overview'.
        """
        lines = markdown_text.splitlines()
        headings: List[tuple] = []  # (line index, level, title)
        h1_lines = set()
        fence = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if fence:
                if stripped.startswith(fence):
                    fence = None
                continue
            if stripped.startswith(("```", "~~~")):
                fence = stripped[:3]
                continue
            heading = re.match(r"^ {0,3}(#{1,3})\s+(.+?)(?:\s+#+)?\s*$", line)
            if heading and len(heading.group(1)) == 1:
                h1_lines.add(i)
            elif heading:
                headings.append((i, len(heading.group(1)), heading.group(2).strip()))

        sections: List[Dict[str, Any]] = []
        intro_end = headings[0][0] if headings else len(lines)
        intro = "\n".join(line for i, line in enumerate(lines[:intro_end]) if i not in h1_lines).strip()
        if intro and not any(title.lower() == "overview" for _, _, title in headings):
            sections.append({"title": "Overview", "level": 1, "path": "Overview", "body": intro})

        parent = None
        for n, (start, level, title) in enumerate(headings):
            # A section runs until the next heading of the same or a higher level
            end = next((i for i, lvl, _ in headings[n + 1:] if lvl <= level), len(lines))
            if level == 2:
                parent = title
            sections.append({
                "title": title,
                "level": level,
                "path": f"{parent} > {title}" if level == 3 and parent else title,
                "body": "\n".join(lines[start + 1:end]).strip(),
            })

        return sections

    @staticmethod
    def find_sections(sections: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
        """Return the sections matching query, using the strictest rule that finds any."""
        def words(text: str) -> List[str]:
            return [w.casefold() for w in SkillIndexer._query_terms(text)]

        query_words = words(query)
        if not query_words:
            return []
        squashed_query = "".join(query_words)

        rules = (
            # Same title, ignoring case and punctuation ('anti patterns' == 'Anti-Patterns')
            lambda s: words(s["title"]) == query_words,
            # Every query word starts a heading word ('anti-pattern' -> 'Anti-Patterns', 'frontend check' -> 'Frontend > Checklist')
            lambda s: all(any(w.startswith(q) for w in words(s["path"])) for q in query_words),
            # Every heading word appears in the query ('checklist items' -> 'Checklist')
            lambda s: all(any(q.startswith(w) for q in query_words) for w in words(s["path"])),
            # Substring, for scripts written without spaces between words (Thai, CJK)
            lambda s: squashed_query in "".join(words(s["path"])),
        )
        for rule in rules:
            matches = [s for s in sections if rule(s)]
            if matches:
                return matches
        return []

    @staticmethod
    def _parse_markdown_file(file_path: Path, skills_dir: Path) -> Optional[Dict[str, Any]]:
        """Parse YAML frontmatter and markdown body from file."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                raw_text = f.read()
        except Exception:
            return None

        frontmatter: Dict[str, Any] = {}
        body = raw_text

        # Frontmatter pattern
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", raw_text, re.DOTALL)
        if fm_match:
            try:
                parsed_fm = yaml.safe_load(fm_match.group(1))
                if isinstance(parsed_fm, dict):
                    frontmatter = parsed_fm
                body = fm_match.group(2).strip()
            except Exception:
                body = raw_text.strip()

        # Ignore documentation and license files
        if file_path.name.lower() in ["readme.md", "license.md", "changelog.md"]:
            return None

        # Extract or fallback fields. A folder skill (<category>/<skill>/SKILL.md) is named
        # after its folder; a plain file (<category>/<skill>.md) after the file.
        is_folder_skill = file_path.name.lower() == "skill.md" and file_path.parent != skills_dir
        skill_path = file_path.parent if is_folder_skill else file_path.with_suffix("")
        name = frontmatter.get("name") or skill_path.name
        category = frontmatter.get("category") or (skill_path.parent.name if skill_path.parent != skills_dir else "general")

        description = frontmatter.get("description", "")
        if not description or str(description).strip() in ["---", ""]:
            # Fallback to first non-header line
            for line in body.splitlines():
                clean_line = line.strip()
                if clean_line and not clean_line.startswith(("#", "---", "```", ">", "-")):
                    description = shorten(clean_line)
                    break

        raw_tags = frontmatter.get("tags", [])
        if isinstance(raw_tags, list):
            tags_str = ", ".join(str(t) for t in raw_tags)
        else:
            tags_str = str(raw_tags)

        sections = SkillIndexer._parse_sections(body)

        return {
            "name": str(name).strip(),
            "category": str(category).strip().lower(),
            "description": str(description).strip(),
            "tags": tags_str,
            "filepath": str(file_path.resolve()),
            "content": body,
            "sections_json": json.dumps(sections, ensure_ascii=False),
            "mtime": file_path.stat().st_mtime,
        }

    @staticmethod
    def _remove_file(cursor: sqlite3.Cursor, fpath: str):
        """Remove the indexed entry of a file, whatever skill name it was stored under."""
        cursor.execute("DELETE FROM skills_fts WHERE rowid IN (SELECT rowid FROM skills WHERE filepath = ?)", (fpath,))
        cursor.execute("DELETE FROM skills WHERE filepath = ?", (fpath,))

    def sync_index(self):
        """Re-index files if modified or added, remove deleted files. Cheap if nothing changed."""
        if not self.skills_dir.exists():
            self.skills_dir.mkdir(parents=True, exist_ok=True)
            return

        with self._lock:
            # Filter out hidden folders (.git, .obsidian, etc.)
            md_files = [p for p in self.skills_dir.rglob("*.md") if not is_hidden(p, self.skills_dir)]

            # A folder containing SKILL.md is one skill; other .md files inside it are bundled resources
            skill_dirs = {p.parent for p in md_files if p.name.lower() == "skill.md" and p.parent != self.skills_dir}
            current_files = {}
            for p in md_files:
                if p.name.lower() != "skill.md" and any(parent in skill_dirs for parent in p.parents):
                    continue
                current_files[str(p.resolve())] = p

            cursor = self.conn.cursor()

            # Check for deleted files
            for fpath in list(self._file_mtimes.keys()):
                if fpath not in current_files:
                    self._remove_file(cursor, fpath)
                    del self._file_mtimes[fpath]

            # Check for new or updated files. Any mtime change counts, since a checkout
            # or copy can replace a file with one that has an older timestamp.
            for fpath_str, path_obj in current_files.items():
                try:
                    mtime = path_obj.stat().st_mtime
                except OSError:
                    continue

                if self._file_mtimes.get(fpath_str) == mtime:
                    continue

                # Drop the old entry by path first, so renaming a skill doesn't leave a stale row behind
                self._remove_file(cursor, fpath_str)
                self._file_mtimes[fpath_str] = mtime

                parsed = self._parse_markdown_file(path_obj, self.skills_dir)
                if not parsed:
                    continue

                # Check for name collisions with other files
                for candidate in (
                    parsed["name"],
                    f"{parsed['category']}/{parsed['name']}",
                    path_obj.relative_to(self.skills_dir).with_suffix("").as_posix(),
                ):
                    cursor.execute("SELECT 1 FROM skills WHERE name = ?", (candidate,))
                    if not cursor.fetchone():
                        parsed["name"] = candidate
                        break
                else:
                    continue

                # Insert into storage
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

                # Insert into FTS5 under the same rowid
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
                SELECT category, COUNT(*) as count, GROUP_CONCAT(name, ', ') as sample_skills
                FROM (
                    SELECT category, name, ROW_NUMBER() OVER (PARTITION BY category ORDER BY name) as rn
                    FROM skills
                )
                WHERE rn <= 3
                GROUP BY category
                ORDER BY category
            """)
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _query_terms(query: str) -> List[str]:
        """Split a query into unique terms. Combining marks stay attached, so Thai or Hindi words aren't cut apart."""
        terms: List[str] = []
        current: List[str] = []
        for ch in query + " ":
            if unicodedata.category(ch)[0] in "LNM":
                current.append(ch)
            elif current:
                term = "".join(current)
                if term.casefold() not in (t.casefold() for t in terms):
                    terms.append(term)
                current = []
        return terms

    @staticmethod
    def _fts_expr(terms: List[str], operator: str) -> str:
        """Quote each term so words like AND/NOT aren't parsed as operators; prefix-match terms of 3+ chars."""
        return f" {operator} ".join(f'"{t}"*' if len(t) >= 3 else f'"{t}"' for t in terms)

    def _fts_search(self, cursor: sqlite3.Cursor, fts_expr: str, limit: int) -> List[Dict[str, Any]]:
        weights = ", ".join(str(w) for _, w in self.FIELD_WEIGHTS)
        try:
            cursor.execute(f"""
                SELECT s.name, s.category, s.description, s.tags, s.sections_json,
                       bm25(skills_fts, {weights}) as rank
                FROM skills_fts
                JOIN skills s ON skills_fts.rowid = s.rowid
                WHERE skills_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (fts_expr, limit))
        except sqlite3.OperationalError:
            return []
        return [dict(row) for row in cursor.fetchall()]

    def _substring_search(self, cursor: sqlite3.Cursor, terms: List[str], limit: int) -> List[Dict[str, Any]]:
        """Weighted substring match for text FTS5 can't split into words (Thai, CJK) or word fragments."""
        folded_terms = [t.casefold() for t in terms]
        cursor.execute("SELECT name, category, description, tags, sections_json, content FROM skills")
        scored = []
        for row in cursor.fetchall():
            fields = {field: (row[field] or "").casefold() for field, _ in self.FIELD_WEIGHTS}
            score = sum(
                weight
                for term in folded_terms
                for field, weight in self.FIELD_WEIGHTS
                if term in fields[field]
            )
            if score:
                result = dict(row)
                del result["content"]
                result["rank"] = -score
                scored.append(result)
        scored.sort(key=lambda r: (r["rank"], r["name"]))
        return scored[:limit]

    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        self.sync_index()
        terms = self._query_terms(query)
        if not terms or limit <= 0:
            return []

        with self._lock:
            cursor = self.conn.cursor()

            # Skills matching every term come first
            results = self._fts_search(cursor, self._fts_expr(terms, "AND"), limit)

            # Top up with skills matching only some of the terms
            if len(results) < limit and len(terms) > 1:
                seen = {r["name"] for r in results}
                for row in self._fts_search(cursor, self._fts_expr(terms, "OR"), limit + len(results)):
                    if len(results) >= limit:
                        break
                    if row["name"] not in seen:
                        results.append(row)

            # Fall back to substring matching if FTS found nothing
            if not results:
                results = self._substring_search(cursor, terms, limit)

            return results

    def get_skill(self, name: str) -> Optional[Dict[str, Any]]:
        self.sync_index()
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM skills WHERE LOWER(name) = LOWER(?)", (name.strip(),))
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


def load_sections(sections_json: Optional[str]) -> List[Dict[str, Any]]:
    try:
        return json.loads(sections_json) if sections_json else []
    except Exception:
        return []


def bundled_files(skill_file: Path, max_files: int = 20) -> str:
    """Describe the files shipped next to a folder skill's SKILL.md ('' for single-file skills)."""
    folder = skill_file.parent
    if skill_file.name.lower() != "skill.md" or folder == SKILLS_DIR.resolve() or not folder.is_dir():
        return ""
    files = sorted(
        p.relative_to(folder).as_posix()
        for p in folder.rglob("*")
        if p.is_file() and p != skill_file and not is_hidden(p, folder)
    )
    if not files:
        return ""
    listed = ", ".join(f"`{f}`" for f in files[:max_files])
    more = f" and {len(files) - max_files} more" if len(files) > max_files else ""
    return f"\n\n---\nBundled files in `{folder}`: {listed}{more}"


@mcp.tool()
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
    all_skills = indexer.list_skills()
    total_skills = len(all_skills)

    # If no category filter and high skill count (or explicitly requested summary_only), show category overview
    if not category and (summary_only or total_skills > 25):
        categories = indexer.get_categories_summary()
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
        return f"No skills found{' in category ' + category if category else ''}."

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
        lines.append(f"- **`{s['name']}`**:{tags_info} {shorten(s['description'])}")

    if total > limit:
        lines.append(f"\n*...and {total - limit} more skills. Call with limit={total} or filter by category.*")

    lines.append("")
    lines.append("💡 *Tip: When you need full details, call `get_skill(skill_name)` on demand.*")
    return "\n".join(lines)


@mcp.tool()
def search_skills(query: str, limit: int = SEARCH_LIMIT) -> str:
    """Search skills by keyword, tag, or topic using fast Full-Text Search (FTS5).
    Skills matching all keywords are listed first, followed by skills matching only some of them.

    Args:
        query: Keywords to search (e.g., 'docker security', 'sql index', 'api rest')
        limit: Maximum number of results to return (default: `search_limit` in config.json)
    """
    results = indexer.search(query=query, limit=limit)
    if not results:
        return f"No skills matched the query: '{query}'. Try broader keywords or use `list_available_skills()`."

    output = [f"### Search Results for '{query}' (Found: {len(results)})", ""]

    for i, r in enumerate(results, 1):
        # Only top-level (H2) sections, to keep results short
        sec_names = [s["title"] for s in load_sections(r.get("sections_json")) if s["level"] == 2]
        sec_hint = f" (Sections: {', '.join(sec_names)})" if sec_names else ""

        output.append(f"{i}. **`{r['name']}`** [{r['category']}]{sec_hint}")
        output.append(f"   Summary: {shorten(r['description'])}")
        if r.get("tags"):
            output.append(f"   Tags: {r['tags']}")
        output.append("")

    output.append("👉 *Next step: Call `get_skill(skill_name)` to view specific instructions.*")
    return "\n".join(output)


@mcp.tool()
def get_skill(skill_name: str, section: Optional[str] = None) -> str:
    """Retrieve instructions and checklist for a specific skill on-demand.

    Args:
        skill_name: The name identifier of the skill (e.g., 'sql-optimization', 'api-design')
        section: Optional H2/H3 heading to extract (e.g., 'checklist', 'anti-patterns') to save maximum tokens.
            An H2 section includes its H3 subsections; use 'Parent Child' words to pick a specific H3.
    """
    skill = indexer.get_skill(skill_name)
    if not skill:
        return f"Skill '{skill_name}' not found. Use `list_available_skills()` or `search_skills()` to find valid skill names."

    sections = load_sections(skill.get("sections_json"))
    header_info = f"# Skill: `{skill['name']}` [{skill['category']}]"

    if section and section.strip():
        matches = indexer.find_sections(sections, section)
        if not matches:
            available_secs = ", ".join(f"`{s['path']}`" for s in sections)
            return f"Section '{section}' not found in skill '{skill_name}'.\nAvailable sections: {available_secs or 'None'}\n\nCall `get_skill('{skill_name}')` without a section to read the full skill."

        best = matches[0]
        content_to_return = f"{header_info} > Section: `{best['path']}`\n\n{best['body']}"

        # Mention other matches, except subsections already included in an H2 match
        others = [
            s["path"] for s in matches[1:]
            if not (best["level"] == 2 and s["path"].startswith(f"{best['title']} > "))
        ]
        if others:
            content_to_return += "\n\n*Other matching sections: " + ", ".join(f"`{p}`" for p in others) + "*"
    else:
        content_to_return = f"{header_info}\n\n{skill['content']}{bundled_files(Path(skill['filepath']))}"

    if not TOKEN_SAVER_DIRECTIVE:
        return content_to_return
    return f"{TOKEN_DIRECTIVE}\n\n{content_to_return}"


@mcp.tool()
def add_or_update_skill(
    name: str,
    description: str,
    content: str,
    category: str = "general",
    tags: str = "",
) -> str:
    """Create a new skill or update an existing skill in the Skills Hub repository.

    Args:
        name: Short, hyphenated skill name (e.g. 'react-performance')
        description: 1-2 line description of what the skill covers
        content: Markdown content with instructions, checklist, and guidelines
        category: Category directory (e.g. 'frontend', 'backend', 'devops')
        tags: Comma-separated tags (e.g. 'react, vite, web')
    """
    # Clean name
    clean_name = re.sub(r"[^\w\-]", "", name.lower().strip())
    clean_cat = re.sub(r"[^\w\-]", "", category.lower().strip()) or "general"

    if not clean_name:
        return "Error: Skill name cannot be empty and must contain alphanumeric characters."

    target_dir = SKILLS_DIR / clean_cat
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / f"{clean_name}.md"

    # Split tags into clean list
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    # Format file with YAML frontmatter (safe_dump quotes values containing ':', '#', newlines, etc.)
    frontmatter = yaml.safe_dump(
        {"name": clean_name, "category": clean_cat, "description": description.strip(), "tags": tag_list},
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=None,
        width=float("inf"),
    )
    file_content = f"---\n{frontmatter}---\n\n{content.strip()}\n"

    with open(target_file, "w", encoding="utf-8") as f:
        f.write(file_content)

    # Trigger instant re-index
    indexer.sync_index()

    # SKILLS_DIR may live outside BASE_DIR (SKILLS_DIR env var), so report the path relative to it
    return f"Skill '{clean_name}' successfully saved to '{target_file.relative_to(SKILLS_DIR).as_posix()}' in the skills directory and indexed."


if __name__ == "__main__":
    # Run FastMCP stdio transport
    mcp.run()
