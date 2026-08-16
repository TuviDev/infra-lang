"""Verify every ```infra code block in docs/tutorial.md parses cleanly.

The tutorial is a public-facing document; if a block stops parsing we fix the
tutorial, never the test.
"""

from __future__ import annotations

import re
from pathlib import Path

from infra import parse

TUTORIAL = Path("docs/tutorial.md")


def _infra_blocks() -> list[str]:
    content = TUTORIAL.read_text()
    return re.findall(r"```infra\n(.*?)```", content, re.DOTALL)


class TestTutorialBlocks:
    def test_tutorial_file_exists(self):
        assert TUTORIAL.exists()

    def test_tutorial_has_infra_blocks(self):
        blocks = _infra_blocks()
        assert len(blocks) >= 5

    def test_every_infra_block_parses(self):
        for i, block in enumerate(_infra_blocks(), start=1):
            try:
                parse(block.strip())
            except Exception as exc:  # pragma: no cover - report only
                raise AssertionError(
                    f"Tutorial block {i} does not parse: {exc}"
                ) from exc

    def test_tutorial_covers_required_lessons(self):
        content = TUTORIAL.read_text()
        for heading in [
            "Installation",
            "Lesson 1: Your first service",
            "Lesson 2: Databases and secrets",
            "Lesson 3: Reliability hints",
            "Lesson 4: Multiple environments",
            "Lesson 5: A CI/CD pipeline",
        ]:
            assert heading in content, f"Missing section: {heading}"
