# Agent Directive: Skills Hub Usage & Token Economy

This project and environment utilizes the **Skills Hub MCP Server** (`skills-hub`) to prevent token bloat and context window saturation.

## Core Rules for Any AI Agent:
1. **On-Demand Skill Retrieval**:
   - Do not guess or preload all specialized workflows into the prompt.
   - When working on a specific domain (API design, SQL optimization, Docker containerization, security auditing, git workflow, testing, etc.), search the Skills Hub first:
     - Call `search_skills(query="<keywords>")` to find relevant guidelines.
     - Or call `list_available_skills()` to see available domains.
2. **Retrieve Targeted Content**:
   - Call `get_skill(skill_name="...", section="checklist")` if you only need the actionable checklist.
   - Or call `get_skill(skill_name="...")` to read the full guideline.
3. **Strict Token Economy**:
   - **Never** regurgitate or copy-paste the entire skill documentation back to the user.
   - Extract only the actionable checklist items or specific rules relevant to the current task.
   - Record them concisely in your scratchpad, implementation plan, or brief response.
