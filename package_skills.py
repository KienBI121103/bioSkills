#!/usr/bin/env python3
"""Export the category sources as a portable plugin or an individual Agent Skill."""

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
import zipfile

import yaml


ROOT = Path(__file__).resolve().parent
VERSION = "1.0.1"
REPOSITORY = "https://github.com/KienBI121103/bioSkills"
STANDARD_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.S)
IGNORED_PARTS = {".git", "__pycache__", ".pytest_cache", ".ipynb_checkpoints", ".DS_Store"}


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys instead of silently dropping metadata."""


def unique_mapping(loader, node):
    result = {}
    for key, value in loader.construct_pairs(node, deep=True):
        if not isinstance(key, str) or key in result:
            raise ValueError(f"Invalid or duplicate YAML key: {key!r}")
        result[key] = value
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def portable_description(description):
    # Keep HGVS substitutions and VCF symbolic alleles as escaped notation,
    # rather than incorrectly rewriting every angle bracket as an inequality.
    replacements = (("<->", "↔"), ("->", "→"), ("<-", "←"), ("<=", "≤"), (">=", "≥"),
                    ("<", "&lt;"), (">", "&gt;"))
    for before, after in replacements:
        description = description.replace(before, after)
    if len(description) > 1024:
        raise ValueError("Exported description exceeds 1024 characters after escaping; shorten the source description")
    return description


def read_frontmatter(data, source):
    text = data.decode("utf-8")
    match = FRONTMATTER.match(text)
    if not match:
        raise ValueError(f"{source}: missing YAML frontmatter")
    try:
        fields = yaml.load(match.group(1), Loader=UniqueKeyLoader)
    except (yaml.YAMLError, ValueError) as error:
        raise ValueError(f"{source}: invalid YAML: {error}") from error
    if not isinstance(fields, dict):
        raise ValueError(f"{source}: frontmatter must be a mapping")
    name = fields.get("name")
    if not isinstance(name, str) or len(name) > 64 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise ValueError(f"{source}: invalid skill name {name!r}")
    description = fields.get("description")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise ValueError(f"{source}: description must be a nonempty string of at most 1024 characters")
    for key in ("license", "compatibility", "allowed-tools"):
        if key in fields and not isinstance(fields[key], str):
            raise ValueError(f"{source}: {key} must be a string")
    if len(fields.get("compatibility", "")) > 500:
        raise ValueError(f"{source}: compatibility exceeds 500 characters")
    metadata = fields.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError(f"{source}: metadata must be a mapping")
    metadata = dict(metadata)
    for key in list(fields):
        if key not in STANDARD_FIELDS:
            if key in metadata:
                raise ValueError(f"{source}: metadata collision for {key!r}")
            metadata[key] = fields.pop(key)
    try:
        metadata = {
            key: value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
            for key, value in metadata.items()
        }
    except (TypeError, ValueError) as error:
        raise ValueError(f"{source}: metadata must contain JSON-compatible values") from error
    normalized_description = portable_description(description)
    if normalized_description != description:
        if "bioskills_original_description" in metadata:
            raise ValueError(f"{source}: reserved metadata key bioskills_original_description")
        metadata["bioskills_original_description"] = description
        fields["description"] = normalized_description
    if metadata:
        fields["metadata"] = metadata
    body = text[match.end():]
    if not body.strip():
        raise ValueError(f"{source}: missing Markdown instructions")
    header = yaml.safe_dump(fields, sort_keys=False, allow_unicode=True, width=1024)
    return name, ("---\n" + header + "---\n" + body).encode("utf-8")


def safe_member(path):
    """Validate before writing ZIP paths; do not normalize away traversal."""
    parts = path.split("/")
    if not path or any(p in {"", ".", ".."} for p in parts) or "\\" in path or ":" in path:
        raise ValueError(f"Unsafe archive path: {path!r}")
    if PurePosixPath(path).is_absolute():
        raise ValueError(f"Absolute archive path: {path!r}")


@dataclass
class Skill:
    directory: Path
    name: str
    manifest: bytes


def discover_skills(root, categories=None, skill_name=None):
    if categories is not None and skill_name is not None:
        raise ValueError("Choose categories or one skill, not both")
    available = {
        directory.name: directory
        for directory in sorted(root.iterdir())
        if directory.is_dir() and not directory.name.startswith(".") and any(directory.glob("*/SKILL.md"))
    }
    if categories is not None:
        if not categories or any(name not in available for name in categories):
            raise ValueError("Unknown or empty category selection")
        available = {name: available[name] for name in sorted(set(categories))}
    skills = []
    names = set()
    for directory in available.values():
        if directory.is_symlink():
            raise ValueError(f"Symlink category is not portable: {directory}")
        for path in sorted(directory.glob("*/SKILL.md")):
            if path.is_symlink() or path.parent.is_symlink():
                raise ValueError(f"Symlink skill is not portable: {path}")
            name, manifest = read_frontmatter(path.read_bytes(), path)
            if name in names:
                raise ValueError(f"Duplicate skill name: {name}")
            names.add(name)
            skills.append(Skill(path.parent, name, manifest))
    if skill_name is not None:
        skills = [skill for skill in skills if skill.name == skill_name]
    if not skills:
        raise ValueError("No matching skills found")
    return skills


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def source_revision(root):
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def package_files(root, skills, single=False):
    # Each entry carries the original executable mode as well as its bytes.
    files = {}
    records = []
    for skill in skills:
        prefix = skill.name if single else f"skills/{skill.name}"
        for source in sorted(skill.directory.rglob("*")):
            relative = source.relative_to(skill.directory)
            if source.is_symlink():
                raise ValueError(f"Symlink resource is not portable: {source}")
            if any(part in IGNORED_PARTS for part in relative.parts) or source.suffix == ".pyc":
                continue
            if source.is_dir():
                continue
            if not source.is_file():
                raise ValueError(f"Unsupported resource type: {source}")
            if source.name.lower() == "skill.md" and relative.as_posix() != "SKILL.md":
                raise ValueError(f"A skill must have exactly one SKILL.md: {source}")
            destination = f"{prefix}/{relative.as_posix()}"
            safe_member(destination)
            content = skill.manifest if relative.as_posix() == "SKILL.md" else source.read_bytes()
            files[destination] = (content, stat.S_IMODE(source.stat().st_mode))
        records.append({"name": skill.name, "source": skill.directory.relative_to(root).as_posix(), "path": prefix})
    if not single:
        manifest = {
            "name": "bioskills",
            "version": VERSION,
            "description": f"{len(skills)} portable bioinformatics skills",
            "author": {"name": "GPTomics", "url": "https://github.com/GPTomics/bioSkills"},
            "homepage": REPOSITORY,
            "repository": REPOSITORY,
            "license": "MIT",
        }
        files["plugin.json"] = (json_bytes({
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", **manifest,
        }), 0o644)
        files[".claude-plugin/plugin.json"] = (json_bytes({**manifest, "skills": "./skills/"}), 0o644)
        files[".codex-plugin/plugin.json"] = (json_bytes({
            **manifest, "skills": "./skills/",
            "interface": {"displayName": "bioSkills", "shortDescription": manifest["description"],
                          "developerName": "GPTomics", "category": "Research"},
        }), 0o644)
    prefix = skills[0].name + "/" if single else ""
    license_path = root / "LICENSE"
    if license_path.is_symlink():
        raise ValueError("License must not be a symlink")
    if license_path.is_file() and prefix + "LICENSE" not in files:
        files[prefix + "LICENSE"] = (license_path.read_bytes(), 0o644)
    provenance = {
        "format_version": 1, "repository": REPOSITORY, "source_revision": source_revision(root),
        "skills": records,
        "sha256": {path: hashlib.sha256(content).hexdigest() for path, (content, _) in sorted(files.items())},
    }
    if prefix + "bioskills-export.json" in files:
        raise ValueError("Reserved resource name: bioskills-export.json")
    files[prefix + "bioskills-export.json"] = (json_bytes(provenance), 0o644)
    return files


def build_archive(root, output, categories=None, skill_name=None):
    root = Path(root).resolve()
    output = Path(output)
    if output.is_symlink() or output.suffix.lower() != ".zip":
        raise ValueError("Output must be a regular .zip path")
    output = output.resolve()
    source_categories = {path.parent.parent for path in root.glob("*/*/SKILL.md")}
    if output.is_relative_to(root / ".git") or any(output.is_relative_to(path) for path in source_categories):
        raise ValueError("Output must not be inside a source category or Git metadata")
    skills = discover_skills(root, categories, skill_name)
    files = package_files(root, skills, single=skill_name is not None)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Write atomically; a failed build must not replace a previous usable export.
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".zip.tmp", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, (content, mode) in sorted(files.items()):
                safe_member(path)
                entry = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = (stat.S_IFREG | mode) << 16
                archive.writestr(entry, content)
        temporary_path.replace(output)
    finally:
        temporary_path.unlink(missing_ok=True)
    return len(skills)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--categories", help="Comma-separated category names")
    selection.add_argument("--skill", help="One skill's frontmatter name; creates an individual-skill ZIP")
    parser.add_argument("--output", type=Path, help="Output .zip path (default: dist/bioskills.zip or dist/<skill>.zip)")
    parser.add_argument("--check", action="store_true", help="Validate sources without writing an archive")
    args = parser.parse_args()
    categories = [name.strip() for name in args.categories.split(",")] if args.categories is not None else None
    try:
        if args.check:
            skills = discover_skills(ROOT, categories, args.skill)
            package_files(ROOT, skills, single=args.skill is not None)
            print(f"Validated {len(skills)} skills and supporting files")
        else:
            output = args.output or ROOT / "dist" / f"{args.skill or 'bioskills'}.zip"
            count = build_archive(ROOT, output, categories, args.skill)
            print(f"Exported {count} skills to {output}")
    except (ValueError, OSError, UnicodeError) as error:
        parser.exit(1, f"Packaging failed: {error}\n")


if __name__ == "__main__":
    main()
