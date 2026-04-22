"""Shared test fixtures.

The alt-text fixture caches a 500-row slice of lukeslp/bluesky-alt-text to
keep tests offline-capable after the first run. If the dataset cannot be
fetched (no network, HF rate-limited) tests fall back to a synthetic sample
so the suite still runs.
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"
ALT_TEXT_FIXTURE = FIXTURE_DIR / "bluesky_alt_text_500.jsonl"


def _synthetic_sample(n: int = 200) -> list[dict]:
    rng = random.Random(0)
    langs = ["a photo of", "描述は", "ein Bild zeigt", "foto de"]
    authors = [f"user{i}" for i in range(25)]
    rows = []
    for i in range(n):
        alt = f"{rng.choice(langs)} scene number {i} with subject {rng.randint(0, 99)}"
        rows.append(
            {
                "alt_text": alt,
                "image_alt_length": len(alt),
                "text": f"post text {i} " + rng.choice(["", "https://example.com", "! ?"]),
                "author_handle": rng.choice(authors),
                "image_index": rng.randint(0, 3),
                "image_count_in_post": rng.randint(1, 4),
            }
        )
    return rows


@pytest.fixture(scope="session")
def alt_text_sample() -> list[dict]:
    if ALT_TEXT_FIXTURE.exists():
        rows = []
        with ALT_TEXT_FIXTURE.open() as fh:
            for line in fh:
                rows.append(json.loads(line))
        return rows

    if os.environ.get("SATURN_NO_NETWORK") == "1":
        return _synthetic_sample()

    try:
        from datasets import load_dataset

        ds = load_dataset("lukeslp/bluesky-alt-text", split="train", streaming=True)
        rows = []
        for i, row in enumerate(ds):
            rows.append(dict(row))
            if i >= 499:
                break
        FIXTURE_DIR.mkdir(exist_ok=True)
        with ALT_TEXT_FIXTURE.open("w") as fh:
            for row in rows:
                fh.write(json.dumps(row, default=str) + "\n")
        return rows
    except Exception as e:  # network or auth failure — fall back
        print(f"alt_text fixture: falling back to synthetic ({e})")
        return _synthetic_sample()


@pytest.fixture
def tiny_synthetic() -> list[dict]:
    return _synthetic_sample(60)
