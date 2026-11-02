# Portable bioSkills input format

Use the open [Agent Skills specification](https://agentskills.io/specification)
for skill content. Use a plugin wrapper only when the receiving platform imports
plugins. A marketplace is a catalog of plugins, not a single skill upload.

## Build an import

Run these commands from the repository root with Python 3.10 or newer:

```bash
python3 -m pip install -r requirements-packaging.txt

# Validate source metadata and supporting files without exporting.
python3 package_skills.py --check

# Plugin ZIP with all categories.
python3 package_skills.py --output dist/bioskills.zip

# Smaller plugin ZIP with selected categories.
python3 package_skills.py --categories sequence-io,single-cell --output dist/bioskills-selected.zip

# One skill, for platforms that accept individual skill uploads.
python3 package_skills.py --skill bio-read-sequences --output dist/bio-read-sequences.zip
```

Generated ZIPs are local build artifacts. They are not committed. Build from a
clean checkout of the recorded source revision to reproduce an export.

## Plugin ZIP layout

```text
plugin.json
.claude-plugin/plugin.json
.codex-plugin/plugin.json
LICENSE
bioskills-export.json
skills/
  bio-read-sequences/
    SKILL.md
    usage-guide.md
    examples/
      ...
```

The root `plugin.json` follows the
[Agent Plugins format documented by OpenAI](https://developers.openai.com/plugins/build/plugins).
The Claude and Codex compatibility manifests both point to `./skills/`.
Supporting files keep their relative locations so instructions and examples
continue to refer to the same files.

An individual-skill ZIP instead contains one top-level directory named after the
skill, such as `bio-read-sequences/SKILL.md`, its supporting files, a license, and
export provenance. It contains no plugin manifest.

## Required invariants

- Every skill has UTF-8 `SKILL.md` with YAML frontmatter and Markdown instructions.
- `name` is unique in the selected bundle, is 1–64 lowercase letters, digits, or
  hyphens, and has no leading, trailing, or consecutive hyphens. The exported
  folder name equals this value.
- `description` is a nonempty string of at most 1,024 characters.
  Exports replace ASCII arrows and comparison operators with their Unicode
  equivalents, then escape remaining angle brackets as `&lt;` and `&gt;` for
  stricter importers. When changed, the exact original description is retained in
  `metadata.bioskills_original_description`. Descriptions are never truncated.
- Standard optional fields are `license`, `compatibility`, `metadata`, and
  `allowed-tools`. `compatibility`, when present, is at most 500 characters.
- Source-specific fields such as `tool_type`, `primary_tool`, `depends_on`, and
  `qc_checkpoints` move under `metadata`. Metadata values are strings; structured
  values are preserved as JSON strings. The Markdown body is preserved.
- Plugin skill paths are explicit, relative, nonempty, and remain inside the
  plugin. Never use `./` as a skill path in a portable import. Absolute paths,
  parent traversal, and symlinks are rejected.
- All supporting files are included, except development caches and Git metadata.
  Each skill has exactly one `SKILL.md`. Source files are never rewritten by an
  export, and output cannot be written inside a source category.
- `bioskills-export.json` records the package format version, source commit when
  available, selected skill names, original paths, and SHA-256 file hashes.

These are packaging checks, not a validation of biological methods or a promise
that every platform accepts the same upload size or number of skills.

## Choose the platform input

| Receiving platform | Input |
| --- | --- |
| Claude Code marketplace import | Repository URL; marketplace entries enumerate actual skill directories |
| Claude-compatible plugin ZIP importer | Plugin ZIP, if the importer supports plugin uploads |
| OpenAI portable plugin importer | Plugin ZIP with root `plugin.json` and `skills/` |
| Platform accepting one Agent Skill | Individual-skill ZIP |
| Agent with a local skills directory | Extract and copy the individual skill folders from `skills/` into its documented skills directory |

The reported Claude Science error rejects `skills: ["./"]` and then finds no
default `skills/` directory. Repository manifests now enumerate the actual
`<category>/<skill>/SKILL.md` directories; exported plugins use the default
`skills/` layout. Confirm which input the import screen accepts before choosing
between a repository URL, plugin ZIP, and individual-skill ZIP.

An imported copy may not update automatically. Rebuild and re-import after source
updates, and use the recorded source commit to identify the installed version.

## Maintainer checks

```bash
python3 build-plugins.py
python3 package_skills.py --check
python3 -m unittest discover -s tests -v
git diff --check
```

The regression suite checks marketplace discovery, exported layouts and metadata,
resource preservation, selected categories, individual uploads, and rejection of
unsafe paths. The repository's existing category folders remain the source of
truth. See the [Claude manifest reference](https://code.claude.com/docs/en/plugins-reference)
for custom skill directory discovery.
