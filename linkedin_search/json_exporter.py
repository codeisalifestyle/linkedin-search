"""JSON export utilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .models import PersonProfile


def export_profiles_json(profiles: Iterable[PersonProfile], output_path: str | Path) -> Path:
    """Write profiles to JSON at any local path."""
    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = []
    for profile in profiles:
        payload.append(
            {
                "name": profile.name,
                "headline": profile.headline,
                "location": profile.location,
                "company": profile.company,
                "profile_url": profile.profile_url,
                "search_type": profile.search_type.value,
            }
        )

    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    return path
