# Claude Code / Agent Directive: Skills Hub Usage

Refer to [AGENTS.md](./AGENTS.md) for full instructions.

## Quick Summary:
- Use MCP server `skills-hub` for on-demand skill retrieval.
- `search_skills(query="...")` or `list_available_skills()` to locate skills.
- `get_skill(skill_name="...", section="checklist")` to extract actionable steps without bloating context.
- Keep responses concise; never dump raw skill texts.
