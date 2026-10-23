"""Export one of your Spotify playlists (or a position range of it) to a seed text file.

The seed file lists one 'Artist - Title' per line, plus optional 'criteria:', 'name:' and 'song_count:'
settings, and is easy to edit by hand. It is the input for suggest_playlist.py.
"""

import argparse
import re
from pathlib import Path

from modules.paths import APP_ROOT, TOKEN_CACHE_PATH
from modules.spotify_api import extract_playlist_id, get_playlist_items, get_playlist_name, load_auth, spotify_session

SEEDS_PATH = APP_ROOT / "seeds"
SEED_HEADER = """\
# Seed file: one "Artist - Title" per line. Lines starting with # are ignored.
# Source: Spotify playlist "{name}" ({id}), positions {start}-{end}

# Optional settings, remove the leading # to use them:
# criteria: describe what the new playlist should be like
# name: name for the new playlist
# song_count: how many songs to suggest
"""


def build_song_lines(items: list[dict], start: int, end: int | None) -> list[str]:
    """Convert Spotify playlist items into 'Artist - Title' lines; start and end are 1-based playlist positions."""
    songs = []
    for entry in items[start - 1 : end]:
        track = entry.get("item")
        # Removed or unavailable entries come back as null, and episodes are not songs
        if not track or track.get("type") != "track":
            continue

        artists = ", ".join(artist["name"] for artist in track["artists"])
        songs.append(f"{artists} - {track['name']}")

    return songs


def export_seed(session, playlist: str, start: int, end: int | None) -> tuple[str, str, int]:
    """Read a playlist (or a position range of it) and return its name, the seed text and the song count."""
    playlist_id = extract_playlist_id(link=playlist)
    playlist_name = get_playlist_name(session=session, playlist_id=playlist_id)
    items = get_playlist_items(session=session, playlist_id=playlist_id)
    songs = build_song_lines(items=items, start=start, end=end)
    header = SEED_HEADER.format(name=playlist_name, id=playlist_id, start=start, end=end or "end")
    return playlist_name, header + "\n" + "\n".join(songs) + "\n", len(songs)


def main(playlist: str, output_path: Path | None, start: int, end: int | None) -> None:
    """Export a Spotify playlist, or a position range of it, into a seed text file."""
    auth = load_auth(Path.home() / ".spotipy")
    session = spotify_session(
        client_id=auth["CLIENT_ID"], client_secret=auth["CLIENT_SECRET"], cache_path=TOKEN_CACHE_PATH
    )

    playlist_name, seed_text, song_count = export_seed(session=session, playlist=playlist, start=start, end=end)

    if output_path is None:
        file_name = re.sub(r'[<>:"/\\|?*]', "_", playlist_name)
        output_path = SEEDS_PATH / f"{file_name}.txt"

    output_path.parent.mkdir(exist_ok=True, parents=True)
    with Path.open(output_path, "w", encoding="utf-8") as seed_file:
        seed_file.write(seed_text)

    print(f"Wrote {song_count} songs to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export one of your Spotify playlists into a seed text file.")
    parser.add_argument("playlist", help="Playlist URL, URI or ID")
    parser.add_argument("--output", type=Path, help="Seed file path (default: seeds/<playlist name>.txt)")
    parser.add_argument("--start", type=int, default=1, help="First playlist position to include (1-based)")
    parser.add_argument("--end", type=int, help="Last playlist position to include (inclusive)")
    args = parser.parse_args()

    main(playlist=args.playlist, output_path=args.output, start=args.start, end=args.end)
