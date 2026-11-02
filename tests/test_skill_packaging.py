"""Regression checks for importable bioSkills packages (no network required)."""

import importlib
import json
from pathlib import Path, PurePosixPath
import stat
import tempfile
import unittest
import zipfile


class PortableArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"
        self.root.mkdir()
        (self.root / "LICENSE").write_text("MIT test license\n", encoding="utf-8")
        self.output = Path(self.temp.name) / "export.zip"
        self.packager = importlib.import_module("package_skills")

    def write_skill(self, category="sequence-io", leaf="read-sequences", name="bio-read-sequences"):
        folder = self.root / category / leaf
        folder.mkdir(parents=True, exist_ok=True)
        body = "\n# Read sequences\n\nRead [the guide](usage-guide.md), then run `scripts/read.sh`.\n"
        (folder / "SKILL.md").write_text(
            "---\n"
            f"name: {name}\n"
            "description: Read biological sequence files when parsing FASTA data.\n"
            "tool_type: python\n"
            "primary_tool: Bio.SeqIO\n"
            "workflow: true\n"
            "depends_on:\n  - bio-prerequisite\n  - bio-quality-control\n"
            "---\n" + body,
            encoding="utf-8",
        )
        (folder / "usage-guide.md").write_text("# Usage\n", encoding="utf-8")
        (folder / "scripts").mkdir(exist_ok=True)
        script = folder / "scripts/read.sh"
        script.write_bytes(b"#!/bin/sh\nprintf 'FASTA\\n'\n")
        script.chmod(0o755)
        (folder / "examples").mkdir(exist_ok=True)
        (folder / "examples/input.bin").write_bytes(b"\x00\xff\x01\x80")
        return folder, body

    def read_metadata(self, archive, path):
        import yaml

        text = archive.read(path).decode("utf-8")
        header, body = text[4:].split("---\n", 1)
        return yaml.safe_load(header), body

    def test_plugin_can_be_discovered_with_all_resources_and_valid_frontmatter(self):
        import yaml

        source, body = self.write_skill()
        originals = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.packager.build_archive(self.root, self.output)

        with zipfile.ZipFile(self.output) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), len(set(names)))
            for name in names:
                path = PurePosixPath(name)
                self.assertFalse(path.is_absolute())
                self.assertNotIn("..", path.parts)
                self.assertNotIn("\\", name)
            self.assertIn("plugin.json", names)
            self.assertIn("bioskills-export.json", names)
            self.assertEqual(b"MIT test license\n", archive.read("LICENSE"))
            codex = json.loads(archive.read(".codex-plugin/plugin.json"))
            self.assertEqual("./skills/", codex["skills"])
            claude = json.loads(archive.read(".claude-plugin/plugin.json"))
            self.assertIn(claude["skills"], ("./skills/", ["./skills/"]))
            entry = "skills/bio-read-sequences/SKILL.md"
            metadata, exported_body = self.read_metadata(archive, entry)
            self.assertEqual("bio-read-sequences", metadata["name"])
            self.assertEqual(body, exported_body)
            self.assertNotIn("tool_type", metadata)
            self.assertEqual("python", metadata["metadata"]["tool_type"])
            self.assertEqual("Bio.SeqIO", metadata["metadata"]["primary_tool"])
            self.assertTrue(all(isinstance(value, str) for value in metadata["metadata"].values()))
            self.assertEqual(["bio-prerequisite", "bio-quality-control"], yaml.safe_load(metadata["metadata"]["depends_on"]))
            self.assertEqual(True, yaml.safe_load(metadata["metadata"]["workflow"]))
            for relative in ("usage-guide.md", "scripts/read.sh", "examples/input.bin"):
                self.assertEqual((source / relative).read_bytes(), archive.read("skills/bio-read-sequences/" + relative))
            script_mode = archive.getinfo("skills/bio-read-sequences/scripts/read.sh").external_attr >> 16
            self.assertTrue(script_mode & stat.S_IXUSR, "Executable helper permissions must survive packaging")

        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(originals, after, "Exporting must not rewrite source skills")

    def test_category_selection_exports_only_requested_skills(self):
        self.write_skill()
        self.write_skill("single-cell", "clustering", "bio-single-cell-clustering")
        self.packager.build_archive(self.root, self.output, categories=["single-cell"])
        with zipfile.ZipFile(self.output) as archive:
            manifests = [name for name in archive.namelist() if name.endswith("/SKILL.md")]
        self.assertEqual(["skills/bio-single-cell-clustering/SKILL.md"], manifests)

    def test_angle_bracket_description_is_portable_without_losing_scientific_notation(self):
        source, body = self.write_skill()
        description = (
            "Interpret HGVS NM_000001.1:c.10G>A, VCF <DEL>, >=10 and <=20; "
            "map A -> B, C <- D, E <-> F."
        )
        manifest = source / "SKILL.md"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                "description: Read biological sequence files when parsing FASTA data.",
                "description: " + description,
            ),
            encoding="utf-8",
        )
        self.packager.build_archive(self.root, self.output)
        with zipfile.ZipFile(self.output) as archive:
            metadata, exported_body = self.read_metadata(
                archive, "skills/bio-read-sequences/SKILL.md"
            )
        self.assertNotRegex(metadata["description"], r"[<>]")
        self.assertEqual(
            "Interpret HGVS NM_000001.1:c.10G&gt;A, VCF &lt;DEL&gt;, ≥10 and ≤20; "
            "map A → B, C ← D, E ↔ F.",
            metadata["description"],
        )
        self.assertEqual(description, metadata["metadata"]["bioskills_original_description"])
        self.assertEqual(body, exported_body)

    def test_escaped_description_over_limit_is_rejected_without_truncation(self):
        source, _ = self.write_skill()
        description = "x" * 1023 + ">"
        manifest = source / "SKILL.md"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                "description: Read biological sequence files when parsing FASTA data.",
                "description: " + description,
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            self.packager.build_archive(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_single_skill_archive_has_one_named_skill_and_its_resources(self):
        source, body = self.write_skill()
        self.write_skill("single-cell", "clustering", "bio-single-cell-clustering")
        self.packager.build_archive(self.root, self.output, skill_name="bio-read-sequences")
        with zipfile.ZipFile(self.output) as archive:
            manifests = [name for name in archive.namelist() if name.endswith("/SKILL.md")]
            self.assertEqual(["bio-read-sequences/SKILL.md"], manifests)
            self.assertFalse(any(name.endswith("plugin.json") for name in archive.namelist()))
            metadata, exported_body = self.read_metadata(archive, manifests[0])
            self.assertEqual("bio-read-sequences", metadata["name"])
            self.assertEqual(body, exported_body)
            self.assertEqual((source / "examples/input.bin").read_bytes(), archive.read("bio-read-sequences/examples/input.bin"))

    def test_invalid_skill_names_cannot_create_unsafe_archive_members(self):
        for name in ("../outside", "Bad-Name", "bio--read", "a" * 65):
            with self.subTest(name=name):
                self.write_skill(name=name)
                with self.assertRaises(ValueError):
                    self.packager.build_archive(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_missing_or_empty_descriptions_are_rejected(self):
        source, _ = self.write_skill()
        text = (source / "SKILL.md").read_text(encoding="utf-8")
        line = "description: Read biological sequence files when parsing FASTA data.\n"
        for replacement in ("", "description: ''\n"):
            with self.subTest(replacement=replacement):
                (source / "SKILL.md").write_text(text.replace(line, replacement), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.packager.build_archive(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_malformed_yaml_is_rejected_before_writing_an_archive(self):
        source, _ = self.write_skill()
        manifest = source / "SKILL.md"
        text = manifest.read_text(encoding="utf-8")
        manifest.write_text(
            text.replace(
                "description: Read biological sequence files when parsing FASTA data.",
                "description: Read FASTA: parse sequence records.",
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            self.packager.build_archive(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_duplicate_yaml_keys_are_rejected_before_writing_an_archive(self):
        source, _ = self.write_skill()
        manifest = source / "SKILL.md"
        text = manifest.read_text(encoding="utf-8")
        manifest.write_text(
            text.replace(
                "name: bio-read-sequences\n",
                "name: bio-read-sequences\nname: bio-different-name\n",
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            self.packager.build_archive(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_duplicate_skill_names_are_rejected_before_writing_an_archive(self):
        self.write_skill()
        self.write_skill("another-category", "another-reader", "bio-read-sequences")
        with self.assertRaises(ValueError):
            self.packager.build_archive(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_unknown_and_empty_category_selections_are_rejected(self):
        self.write_skill()
        for categories in (["missing-category"], [], [""]):
            with self.subTest(categories=categories):
                with self.assertRaises(ValueError):
                    self.packager.build_archive(self.root, self.output, categories=categories)
        self.assertFalse(self.output.exists())

    def test_unknown_skill_selection_is_rejected(self):
        self.write_skill()
        with self.assertRaises(ValueError):
            self.packager.build_archive(self.root, self.output, skill_name="bio-missing")
        self.assertFalse(self.output.exists())

    def test_symbolic_links_are_rejected_even_when_they_target_a_sibling_file(self):
        source, _ = self.write_skill()
        (source / "linked-guide.md").symlink_to("usage-guide.md")
        with self.assertRaises(ValueError):
            self.packager.build_archive(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_archive_cannot_be_written_inside_a_source_skill(self):
        source, _ = self.write_skill()
        output = source / "accidental-export.zip"
        with self.assertRaises(ValueError):
            self.packager.build_archive(self.root, output)
        self.assertFalse(output.exists())

    def test_failed_rebuild_preserves_the_previous_usable_archive(self):
        source, _ = self.write_skill()
        self.packager.build_archive(self.root, self.output)
        previous = self.output.read_bytes()
        manifest = source / "SKILL.md"
        text = manifest.read_text(encoding="utf-8")
        manifest.write_text(
            text.replace(
                "description: Read biological sequence files when parsing FASTA data.",
                "description: ''",
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            self.packager.build_archive(self.root, self.output)
        self.assertEqual(previous, self.output.read_bytes())
        with zipfile.ZipFile(self.output) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn("skills/bio-read-sequences/SKILL.md", archive.namelist())


if __name__ == "__main__":
    unittest.main()
