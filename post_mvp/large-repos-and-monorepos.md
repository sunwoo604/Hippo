# Large repos and monorepos

**What:**
- Large repos: give the init agent the tracked file list (`git ls-files`) and
  a reading order (manifests, README, entry points, routes, migrations, then a
  sample) so it doesn't explore blindly; warn about cost/time before running.
- Monorepos: prefix slugs with the app name (`web-auth`, `api-auth`) when the
  repo holds several apps.

**Why postponed (2026-10-04):** not needed for the MVP's single-user repos.

**Safety net (added 2026-11-18):** the catch-up pass exports the whole default
branch with `git archive` for every agent run. For very large repos, export only
the folders the unexplained commits touch (`git archive <tip> -- <paths>`).
