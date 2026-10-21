"""Read seed files: the hand-editable text format that describes what a new playlist should be based on.

A seed file has one 'Artist - Title' per line, '#' comment lines, and optional 'criteria:', 'name:'
and 'song_count:' settings. Used by suggest_playlist.py; export_playlist_seed.py writes the same format.
"""

import re
from dataclasses import dataclass
from pathlib import Path

SETTING_PATTERN = re.compile(r"^(criteria|name|song_count)\s*:\s*(.*)$", re.IGNORECASE)


@dataclass
class Seed:
    """Songs to base a playlist on, plus optional settings (criteria, name, song_count)."""

    songs: list[str]
    settings: dict[str, str]


def parse_seed(text: str) -> Seed:
    """Parse seed text: one 'Artist - Title' per line, '#' comment lines and optional 'key: value' settings."""
    songs = []
    settings = {}
    for line in text.splitlines():
        line_text = line.strip()
        if not line_text or line_text.startswith("#"):
            continue

        setting = SETTING_PATTERN.match(line_text)
        if setting:
            settings[setting.group(1).lower()] = setting.group(2).strip()
        else:
            songs.append(line_text)

    return Seed(songs=songs, settings=settings)


def read_seed(path: Path) -> Seed:
    """Read a seed file; see parse_seed for the format."""
    return parse_seed(text=path.read_text(encoding="utf-8"))
