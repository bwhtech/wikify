"""App install hooks."""

from __future__ import annotations

from wikify.seed import seed_section_types


def after_install() -> None:
	seed_section_types()
