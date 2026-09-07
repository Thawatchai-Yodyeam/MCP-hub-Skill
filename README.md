# Skills Hub MCP Server 🧠⚡

> **Lightweight On-Demand Agent Skills Management for Antigravity & AI Coding Agents**  
> แก้ปัญหา **Token บวม (Context Window Bloat)** จากการโหลด System Prompt สกิลทั้งหมดพร้อมกัน ด้วยสถาปัตยกรรม **Progressive On-Demand Retrieval**

---

## 🎯 ปัญหาและแนวทางแก้ไข (Problem & Solution)

### ปัญหาเดิม (Token Bloat):
เมื่อมี Agent Skills หรือ Runbooks จำนวนมาก (20-50+ สกิล) หากโหลดเข้า System Prompt พร้อมกัน:
- ❌ เปลือง Token ตั้งแต่เริ่มบทสนทนา (10,000 - 50,000+ tokens)
- ❌ Context window เต็มเร็ว ส่งผลให้โมเดลจำประวัติก่อนหน้าไม่ได้ หรือเกิดอาการหลงลืม (Attention drift)
- ❌ ค่าใช้จ่าย (API Cost) และ Latency สูงขึ้นโดยไม่จำเป็น

### แนวทางแก้ไขด้วย Skills Hub MCP:
- ✅ **Progressive Retrieval**: AI โหลดเฉพาะดัชนีสรุปย่อ 1-2 บรรทัด (`list_available_skills`) ใช้ไม่เกิน 150-200 tokens
- ✅ **Sub-millisecond Search**: ใช้ SQLite FTS5 (Full-Text Search) ในหน่วยความจำ ค้นหาตาม Keyword / Tags / BM25 ได้ทันที
- ✅ **Section-targeted Loading**: สามารถดึงเฉพาะหัวข้อ เช่น `get_skill(name, section="checklist")` เพื่อประหยัด Token สูงสุด
- ✅ **Built-in Token Directives**: ทุกครั้งที่ AI ดึงสกิล ตัว Server จะแนบ Directive สั่งให้ AI สรุปเฉพาะ Action Items ลง Scratchpad/Chat แทนการแปะเอกสารตัวเต็ม
- ✅ **Dynamic Hot-Reload**: โยนไฟล์ Markdown ใหม่เข้าโฟลเดอร์ `skills/` ระบบจะ Index อัตโนมัติทันทีโดยไม่ต้อง Restart Server

---

## 🛠️ โครงสร้างโปรเจกต์ (Project Architecture)

```
d:\MCP-hub-Skilll\
├── server.py                   # FastMCP Server (stdio) + SQLite FTS5 Engine
├── config.json                 # การตั้งค่า (skills directory, search limit)
├── mcp_config_snippet.json     # คอนฟิกพร้อมก๊อปปี้ไปใส่ใน Antigravity
├── AGENTS.md                   # Universal Directive สำหรับ Agent ทุกค่าย
├── CLAUDE.md                   # Directive สำหรับ Claude Code
├── .cursorrules                # Directive สำหรับ Cursor IDE
├── pyproject.toml              # Project metadata & dependencies
├── requirements.txt            # Python dependencies (fastmcp, pyyaml)
├── skills/                     # คลังสกิลเริ่มต้น (Default Starter Skills)
│   ├── web-development/
│   │   └── api-design.md
│   ├── performance/
│   │   └── sql-optimization.md
│   ├── devops/
│   │   └── docker-deployment.md
│   ├── code-review/
│   │   └── security-audit.md
│   ├── git-workflow/
│   │   └── conventional-commits.md
│   └── testing/
│       └── pytest-testing.md
└── scripts/
    └── import_skills.py        # ยูทิลิตี้ช่วยดึงสกิลเดิมในเครื่องเข้าคลัง
```

---

## 🔌 MCP Tools ที่ให้บริการ

| Tool Name | คำอธิบาย | พารามิเตอร์ |
| :--- | :--- | :--- |
| `list_available_skills` | แสดงรายชื่อสกิลทั้งหมดแบบกระชับ (1-2 บรรทัด/สกิล) แยกตามหมวดหมู่ | `category` (optional) |
| `search_skills` | ค้นหาสกิลด้วย BM25 Full-Text Search รองรับชื่อ, แท็ก, คำอธิบาย, เนื้อหา | `query` (required), `limit` (optional, default: 5) |
| `get_skill` | ดึงเนื้อหาสกิลตัวเต็ม หรือดึงเฉพาะ Section (เช่น `checklist`, `anti-patterns`) พร้อม Token-Saving Directive | `skill_name` (required), `section` (optional) |
| `add_or_update_skill` | สร้างหรืออัปเดตสกิลใหม่ลงในคลังได้ทันทีผ่าน AI | `name`, `description`, `content`, `category`, `tags` |

---

## 🚀 การติดตั้งและเปิดใช้งานใน Antigravity

### 1. ไฟล์การตั้งค่า Antigravity MCP
เปิดไฟล์การตั้งค่า MCP ของ Antigravity ที่:
`C:\Users\The_Grace\.gemini\config\mcp_config.json`

แล้วเพิ่มบล็อกคอนฟิกนี้ลงใน `"mcpServers"`:

```json
{
  "mcpServers": {
    "skills-hub": {
      "command": "C:\\Python314\\python.exe",
      "args": [
        "d:\\MCP-hub-Skilll\\server.py"
      ],
      "env": {
        "PYTHONUNBUFFERED": "1",
        "SKILLS_DIR": "d:\\MCP-hub-Skilll\\skills"
      }
    }
  }
}
```

*(ดูตัวอย่างไฟล์ได้ที่ [mcp_config_snippet.json](./mcp_config_snippet.json))*

---

## ✍️ วิธีเพิ่มสกิลใหม่เข้าคลัง (3 วิธี)

### วิธีที่ 1: สร้างไฟล์ Markdown โดยตรง (แนะนำ)
สร้างไฟล์ `.md` ภายใต้โฟลเดอร์ `skills/<category>/<skill-name>.md` โดยใส่ YAML Frontmatter ด้านบน:

```markdown
---
name: my-new-skill
category: frontend
description: คำอธิบายสั้นๆ 1-2 บรรทัดเกี่ยวกับสกิลนี้
tags: [react, ui, css]
---

# My New Skill Guide

คำอธิบายทั่วไป...

## Checklist
- [ ] ข้อปฏิบัติที่ 1
- [ ] ข้อปฏิบัติที่ 2

## Anti-patterns
- สิ่งที่ไม่ควรทำ...
```
*(เมื่อบันทึกไฟล์แล้ว Skills Hub จะตรวจจับและ Re-index ให้อัตโนมัติทันที ไม่ต้องรีสตาร์ท)*

---

### วิธีที่ 2: ให้ AI เพิ่มสกิลให้ผ่าน MCP Tool
AI สามารถเรียกคำสั่ง `add_or_update_skill` ได้โดยตรง เช่น:
```python
add_or_update_skill(
    name="nextjs-caching",
    description="Next.js App Router caching strategies and revalidation.",
    category="web-development",
    tags="nextjs, react, cache",
    content="""# Next.js Caching Guide
## Checklist
- [ ] Use generateStaticParams for static routes
- [ ] Use revalidateTag for on-demand ISR
"""
)
```

---

### วิธีที่ 3: ดึงสกิลเดิมที่มีในเครื่องอัตโนมัติ (Import)
รันสคริปต์ `import_skills.py` เพื่อค้นหาและคัดลอกสกิลจาก Antigravity (`~/.gemini/config/skills`):

```powershell
python scripts/import_skills.py
```
หรือระบุโฟลเดอร์เอง:
```powershell
python scripts/import_skills.py "C:\path\to\another\skills_dir"
```

---

## 🧪 การทดสอบระบบ (Verification)

สามารถรันสคริปต์ทดสอบอัตโนมัติได้ทันที:
```powershell
python scripts/test_client.py
```

ผลการทดสอบครอบคลุม:
1. การแสดงรายการสกิลแบบจำกัด Token (`list_available_skills`)
2. การค้นหาความเกี่ยวข้องด้วย FTS5 (`search_skills`)
3. การดึงเนื้อหาสกิลพร้อม Token-Saving Directive (`get_skill`)
4. การตัดดึงเฉพาะส่วนย่อย (`get_skill(..., section='checklist')`)
5. การเพิ่มสกิลแบบไดนามิกและการค้นหาเจอทันที (Hot-reload)

---

## 💡 คำแนะนำเพิ่มเติมเพื่อความคุ้มค่า Token สูงสุด (Best Practices)

### 1. การเลือกชื่อไฟล์ Directive ให้รองรับ AI หลายค่าย (Multi-Tool Compatibility)
คุณสามารถเลือกใช้ชื่อไฟล์ตามมาตรฐานของค่าย AI ที่ใช้งานได้ทันที (ในโปรเจกต์นี้มีเตรียมไว้ให้แล้วทั้งหมด):

| ชื่อไฟล์ | เครื่องมือ/ค่ายที่รองรับ | จุดเด่น |
| :--- | :--- | :--- |
| **`AGENTS.md`** ⭐ *(แนะนำ)* | **Antigravity, Cline, Roo Code, Open-source Agents** | เป็นมาตรฐานสากล (Vendor-neutral) ไม่ผูกติดกับค่ายใด และ Antigravity รองรับเป็น First-class ทันที |
| **`CLAUDE.md`** | **Claude Code (Anthropic)** | มาตรฐานหลักของ Claude Code CLI |
| **`.cursorrules`** | **Cursor IDE** | ทำงานอัตโนมัติเมื่อเปิดโปรเจกต์ใน Cursor |
| **`.windsurfrules`** | **Windsurf (Codeium)** | รองรับกฎสำหรับ Cascade/Windsurf |
| **`GEMINI.md`** | **Antigravity / Gemini CLI** | มาตรฐานดั้งเดิมของ Antigravity |

> 📌 **คำแนะนำ**: ใช้ **`AGENTS.md`** เป็นไฟล์หลักไฟล์เดียวได้เลย เพราะเป็นชื่อที่เป็นกลางที่สุด และใน Antigravity ถูกตั้งค่าให้อ่าน `AGENTS.md` เทียบเท่ากับ `GEMINI.md` ทุกประการ

### 2. ใช้ `section="checklist"` บ่อยๆ
เวลา AI ต้องการแค่ลำดับขั้นตอน การดึงเฉพาะ Section จะช่วยลดการใช้ Token ได้ถึง 70-85% เมื่อเทียบกับไฟล์เต็ม
