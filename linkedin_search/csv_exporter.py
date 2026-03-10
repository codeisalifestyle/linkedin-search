"""CSV export utilities."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from .models import PersonProfile


CSV_FIELDS = [
    "name",
    "headline",
    "location",
    "company",
    "profile_url",
    "search_type",
]


def export_profiles_csv(profiles: Iterable[PersonProfile], output_path: str | Path) -> Path:
    """Write profiles to CSV at any local path."""
    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for profile in profiles:
            writer.writerow(
                {
                    "name": profile.name,
                    "headline": profile.headline or "",
                    "location": profile.location or "",
                    "company": profile.company or "",
                    "profile_url": profile.profile_url,
                    "search_type": profile.search_type.value,
                }
            )
    return path

