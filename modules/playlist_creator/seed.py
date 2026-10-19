"""Read seed files: the hand-editable text format that describes what a new playlist should be based on.

A seed file has one 'Artist - Title' per line, '#' comment lines, and optional 'criteria:', 'name:'
and 'count:' settings. Used by suggest_playlist.py; export_playlist_seed.py writes the same format.
"""

import re
from dataclasses import dataclass
from pathlib import Path

SETTING_PATTERN = re.compile(r"^(criteria|name|count)\s*:\s*(.*)$", re.IGNORECASE)


@dataclass
class Seed:
    """Songs to base a playlist on, plus optional settings (criteria, name, count)."""

    songs: list[str]
    settings: dict[str, str]


def read_seed(path: Path) -> Seed:
    """Read a seed file: one 'Artist - Title' per line, '#' comment lines and optional 'key: value' settings."""
    songs = []
    settings = {}
    with Path.open(path, "r", encoding="utf-8") as seed_file:
        for line in seed_file:
            text = line.strip()
            if not text or text.startswith("#"):
                continue

            setting = SETTING_PATTERN.match(text)
            if setting:
                settings[setting.group(1).lower()] = setting.group(2).strip()
            else:
                songs.append(text)

    return Seed(songs=songs, settings=settings)
