"""Seed the Section Type taxonomy on existing sites (Slice 6)."""

from __future__ import annotations

from wikify.seed import seed_section_types


def execute() -> None:
	seed_section_types()
