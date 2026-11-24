# MCP layer

**What:** `hippo mcp`, an MCP server exposing Hippo's engine functions as tools
(status, open, patch new, feature/decision reads) for agents other than Claude
Code (Cursor, Codex, Claude Desktop chat).

**Why postponed (2026-10-01):** in Claude Code, the CLI via Bash plus the skill
already covers everything; the one real MCP benefit (a validated write_patch
tool) is provided by `hippo patch new`.

**Notes:** the engine is kept separate from CLI printing so the MCP server can
be a thin wrapper over the same functions. MCP can't be an install mechanism:
the server's code has to be on the machine first.
