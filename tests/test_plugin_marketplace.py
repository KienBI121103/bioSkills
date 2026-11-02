"""Regression checks for the native plugin marketplace import layout."""

import json
from pathlib import Path, PurePosixPath
import unittest


REPO = Path(__file__).resolve().parents[1]


class RepositoryMarketplaceTests(unittest.TestCase):
    def test_declared_skills_are_safe_explicit_skill_directories(self):
        marketplace = json.loads(
            (REPO / ".claude-plugin/marketplace.json").read_text(encoding="utf-8")
        )
        errors = []
        bundled = set()
        for plugin in marketplace["plugins"]:
            source = (REPO / plugin["source"]).resolve()
            self.assertTrue(source.is_relative_to(REPO))
            paths = plugin["skills"]
            self.assertTrue(paths, plugin["name"])
            for raw in paths:
                relative = PurePosixPath(raw)
                if (
                    not raw.startswith("./")
                    or not relative.parts
                    or relative.is_absolute()
                    or ".." in relative.parts
                    or "\\" in raw
                ):
                    errors.append(f"{plugin['name']}: unsafe skill path {raw!r}")
                    continue
                skill = (source / raw).resolve()
                if not skill.is_relative_to(source) or not (skill / "SKILL.md").is_file():
                    errors.append(f"{plugin['name']}: {raw!r} has no direct SKILL.md")
                if plugin["name"] == "bioskills":
                    bundled.add(skill / "SKILL.md")
        self.assertEqual([], errors, "\n".join(errors[:20]))
        expected = {path.resolve() for path in REPO.glob("*/*/SKILL.md")}
        self.assertEqual(expected, bundled, "The bundle must include every source skill")


if __name__ == "__main__":
    unittest.main()
