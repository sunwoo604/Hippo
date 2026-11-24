# Hippo project memory

This repository is linked to Hippo. Its memory is in {{memory_dir}}/.

## Features
{{features}}

## Unmerged patch notes
These describe recent changes the feature docs don't include yet.
{{patches}}

## How to use it
- Before working on a feature, read its docs in the memory folder:
  features/<slug>/overview.md and technical.md (key files are in feature.json).
- Before changing how something works at an architectural level, check
  features/<slug>/decisions.json. Those are choices the user explicitly agreed to.
- Read an unmerged patch note (patches/<file>) when its features overlap your task.
- Never edit anything in the memory folder; Hippo's daily job updates it.
- When you finish a task that changed code, Hippo will ask you to record a patch
  note with `{{hippo}} patch new`. Record a decision in it only if the user
  explicitly agreed to it.
