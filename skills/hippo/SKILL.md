---
name: hippo
description: Set up and use Hippo, persistent project memory for Claude Code. Use when the user asks to install or set up Hippo, give Claude memory of a project, link a repo to Hippo, see what Hippo knows about a project, or asks what Hippo is.
---

# Hippo

Hippo gives each linked project a memory: feature docs, decisions the user agreed
to, and patch notes from past sessions. Its hooks load that memory at the start of
every session in a linked repo and ask for a patch note when a task changed code.

## Setting it up

1. Check whether Hippo is available: run `hippo --version`.
   - If the command is missing and this plugin is enabled, its own `hippo` is on
     your PATH; try again in a new shell call.
   - Without the plugin, install the CLI: `pipx install git+{{REPO_URL}}`
2. Run `hippo install`.
3. Show the user the "── Next steps for you ──" block from the output word for
   word. It may be the only place they see it.
   - If it says Claude Code isn't logged in, ask the user to run
     `claude auth login` in their own terminal, then run `hippo install` again.

## Linking a project

Never run `hippo link` yourself. It reads the whole codebase with Claude, takes
a few minutes, sets up Hippo's hooks for that repo, and ends with a review the
user should do. Give the user the command to run in their own terminal, inside
the project:

    hippo link

## Looking at a project's memory

These are safe to run and show the user:
- `hippo open`: the project's features with keywords, unmerged notes, review items
- `hippo open --feature <name>`: one feature's summary, key files and docs
- `hippo open --feature <name> --decisions`: decisions the user agreed to

## While working in a linked repo

The memory arrives at session start. Don't edit files in the memory folder.
When Hippo's hook asks for a patch note, follow its instructions; record a
decision only if the user explicitly agreed to it in this session.
