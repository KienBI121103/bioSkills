#!/usr/bin/env python3
"""
Generate plugin marketplace manifests for Claude Code and Codex CLI.

Each skill category becomes its own installable plugin, plus one "bioskills"
plugin that bundles every category. Re-run after adding or removing a
category or skill:

    python3 build-plugins.py

Writes:
    .claude-plugin/marketplace.json      Claude Code marketplace
    .agents/plugins/marketplace.json     Codex marketplace
    .codex-plugin/plugin.json            Codex manifest for the all-in-one plugin
    <category>/.codex-plugin/plugin.json Codex manifest for each category plugin
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MARKETPLACE = "bioskills"
VERSION = "1.0.0"
OWNER = {"name": "KienBI121103", "url": "https://github.com/KienBI121103/bioSkills"}
AUTHOR = {"name": "GPTomics", "url": "https://github.com/GPTomics/bioSkills"}
REPO = "https://github.com/KienBI121103/bioSkills"
LICENSE = "MIT"
CODEX_CATEGORY = "Research"


def categories():
    """Top-level folders that hold <category>/<skill>/SKILL.md."""
    for d in sorted(ROOT.iterdir()):
        if d.is_dir() and not d.name.startswith(".") and any(d.glob("*/SKILL.md")):
            yield d


def overview(cat_dir):
    """First sentence of the category README's Overview section."""
    readme = cat_dir / "README.md"
    if readme.exists():
        text = readme.read_text(encoding="utf-8")
        m = re.search(r"## Overview\s+(.+?)\n\s*\n", text, re.S)
        if m:
            para = " ".join(m.group(1).split())
            return re.split(r"(?<=\.)\s", para, maxsplit=1)[0].rstrip(".")
    return f"{cat_dir.name.replace('-', ' ').capitalize()} skills"


def title(name):
    return name.replace("-", " ").title()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def codex_manifest(name, description, skills, display_name):
    return {
        "name": name,
        "version": VERSION,
        "description": description,
        "author": AUTHOR,
        "homepage": REPO,
        "repository": REPO,
        "license": LICENSE,
        "keywords": ["bioinformatics", "genomics", "skills"],
        "skills": skills,
        "interface": {
            "displayName": display_name,
            "shortDescription": description[:120],
            "developerName": AUTHOR["name"],
            "category": CODEX_CATEGORY,
        },
    }


def codex_entry(name, path):
    return {
        "name": name,
        "source": {"source": "local", "path": path},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": CODEX_CATEGORY,
    }


def main():
    cats = list(categories())
    total = sum(len(list(c.glob("*/SKILL.md"))) for c in cats)
    all_desc = f"All {total} bioSkills across {len(cats)} bioinformatics categories"

    claude_plugins = [{
        "name": MARKETPLACE,
        "source": "./",
        "description": all_desc,
        "version": VERSION,
        "category": "bioinformatics",
        "skills": [f"./{c.name}" for c in cats],
    }]
    codex_plugins = [codex_entry(MARKETPLACE, "./")]
    write_json(ROOT / ".codex-plugin/plugin.json",
               codex_manifest(MARKETPLACE, all_desc, [f"./{c.name}/" for c in cats], "bioSkills (all)"))

    for c in cats:
        n = len(list(c.glob("*/SKILL.md")))
        desc = f"{overview(c)} ({n} skills)"
        claude_plugins.append({
            "name": c.name,
            "source": f"./{c.name}",
            "description": desc,
            "version": VERSION,
            "category": "bioinformatics",
            "skills": ["./"],
        })
        codex_plugins.append(codex_entry(c.name, f"./{c.name}"))
        # Codex does not scan the plugin root itself, so list each skill folder
        skill_dirs = [f"./{s.parent.name}/" for s in sorted(c.glob("*/SKILL.md"))]
        write_json(c / ".codex-plugin/plugin.json",
                   codex_manifest(c.name, desc, skill_dirs, f"bioSkills: {title(c.name)}"))

    write_json(ROOT / ".claude-plugin/marketplace.json", {
        "name": MARKETPLACE,
        "description": "Bioinformatics agent skills, installable per category or all at once",
        "owner": OWNER,
        "plugins": claude_plugins,
    })
    write_json(ROOT / ".agents/plugins/marketplace.json", {
        "name": MARKETPLACE,
        "interface": {"displayName": "bioSkills"},
        "plugins": codex_plugins,
    })
    print(f"{len(cats)} category plugins + 1 all-in-one plugin, {total} skills")


if __name__ == "__main__":
    main()
