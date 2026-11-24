"""Run the init agent: read a linked repo, write its first feature docs."""

import shutil
from datetime import datetime
from pathlib import Path

from hippo import agent, features, paths, prompts, ui
from hippo.agent import abs_rule
from hippo.project import Project

TOOLS = ["Read", "Glob", "Grep", "Write", "Edit", "Bash"]
DOC_FILES = ("overview.md", "technical.md")
# Files the init agent may not write directly: Hippo owns them.
HIPPO_OWNED = ("index.md", "meta.json", "needs-review.json", "noted-files.json", "features/*/feature.json",
               "features/*/decisions.json", "patches/**", "archive/**")


def bash_prefixes() -> list[str]:
    return [f"{paths.launcher()} feature set"]


def sandbox(p: Project) -> tuple[list[str], list[str]]:
    allow = ["Read", "Glob", "Grep", abs_rule("Edit", p.dir, "/**")]
    allow += [f"Bash({prefix} *)" for prefix in bash_prefixes()]
    deny = agent.secret_denies(p.repo)
    deny += [abs_rule("Edit", p.dir, f"/{f}") for f in HIPPO_OWNED]
    deny.append(abs_rule("Edit", p.repo, "/**"))  # belt and braces: never the repo
    return allow, deny


def build_prompt(p: Project) -> str:
    return prompts.render("init.md", repo_path=str(p.repo),
                          memory_dir=str(p.dir), hippo=str(paths.launcher()))


def backup_existing(p: Project) -> Path | None:
    """Before re-init: copy features/ to archive/, then clear generated docs.

    decisions.json files stay in place: they come from the user and init never
    recreates them.
    """
    fdir = p.dir / "features"
    generated = [f for f in fdir.glob("*/*") if f.name in ("feature.json", *DOC_FILES)]
    if not generated:
        return None
    dest = p.dir / "archive" / f"features-before-reinit-{datetime.now():%Y-%m-%dT%H%M%S}"
    shutil.copytree(fdir, dest)
    for f in generated:
        f.unlink()
    for d in fdir.iterdir():
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    return dest


def run(p: Project) -> bool:
    print("\nRunning the init agent. It reads the codebase with Claude (this uses your")
    print("plan's usage) and usually takes a few minutes.\n")
    allow, deny = sandbox(p)
    result = agent.run(build_prompt(p), cwd=p.dir, add_dirs=[p.repo], tools=TOOLS,
                       allow=allow, deny=deny, bash_prefixes=bash_prefixes(),
                       on_tool=agent.progress(p.dir, p.repo))
    agent.record("init", p.repo.name, p.uuid, result)
    features.write_index(p.dir)  # always consistent, even after a partial run

    found, problems = features.load_all(p.dir)
    missing = [f"features/{f.slug}/{doc}" for f in found for doc in DOC_FILES
               if not (p.dir / "features" / f.slug / doc).exists()]
    stats = agent.stats(result)
    print()
    if not result.ok:
        ui.say("failed", f"init agent: {result.error}{stats}")
    elif not found:
        ui.say("failed", f"init agent finished but recorded no features{stats}")
    else:
        ui.say("created", f"{len(found)} features{stats}")
    for line in problems + [f"missing {m}" for m in missing]:
        ui.say("", f"! {line}")

    ok = result.ok and bool(found) and not problems and not missing
    if ok:
        ui.next_steps([
            "Review the feature split now: run `hippo open`, then `hippo open --feature <name>`.",
            f"Fix any doc directly in {ui.tilde(p.dir / 'features')}/ (every later update builds on it).",
            "To regenerate from scratch instead, run:  hippo link --reinit",
        ])
    else:
        ui.next_steps(["Run `hippo link --reinit` to try again.",
                       f"Partial results (if any) are in {ui.tilde(p.dir)}/"])
    return ok
