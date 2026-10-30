r"""Create Spotify playlists from the cleaned Winamp playlist files.

Takes each '<name>_fixed.txt' written by playlist_parser.py, searches Spotify for every song, and
adds the best matches to a new private playlist. If all files together have fewer than 10,000 songs,
they are merged into one playlist without duplicates; otherwise each file gets its own playlist.
Writes logs of added songs and of songs that were not found or skipped as duplicates to the
'log\winamp_to_spotify' folder.
"""

import argparse
import time
from datetime import datetime
from pathlib import Path

from modules.paths import AUTH_PATH, TOKEN_CACHE_PATH
from modules.spotify_api import (
    add_songs_to_playlist,
    create_playlist,
    get_song_uris,
    load_auth,
    playlist_url,
    spotify_session,
)
from modules.winamp_to_spotify.playlist_parser import ENCODING

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_PLAYLIST_SONGS = 10_000  # Spotify's limit per playlist


def build_playlists(files, start_time):
    """Return (log name, playlist name, description, lines) for each playlist to create.

    One playlist for all files when their song count stays under Spotify's limit, otherwise one per file.
    """
    file_lines = []
    for file in files:
        with Path.open(file, "r", encoding=ENCODING) as input_file:
            file_lines.append((file, input_file.readlines()))

    total = sum(1 for _, lines in file_lines for line in lines if line.strip())
    if total < MAX_PLAYLIST_SONGS:
        all_lines = [line for _, lines in file_lines for line in lines]
        return [
            ("all_files", f"Winamp imports [all files] {start_time}", f"Created from {len(files)} files", all_lines)
        ]

    return [
        (file.stem, f"Winamp imports [{file.stem}] {start_time}", f"Created from file: {file.name}", lines)
        for file, lines in file_lines
    ]


def run_through_playlists(files, log_path):
    auth = load_auth(AUTH_PATH)
    session = spotify_session(
        client_id=auth["CLIENT_ID"], client_secret=auth["CLIENT_SECRET"], cache_path=TOKEN_CACHE_PATH
    )

    start_time = datetime.now().astimezone().strftime("%Y%m%d_%H%M")
    log_playlists = log_path / f"{start_time}_added_playlists_log.txt"

    with Path.open(log_playlists, "a", encoding="ISO-8859-1") as playlist_log:
        for log_name, playlist_name, description, lines in build_playlists(files, start_time):
            log_songs = log_path / f"{start_time}_[{log_name}]_added_songs_log.txt"
            log_not_found = log_path / f"{start_time}_[{log_name}]_not_found_log.txt"
            with (
                Path.open(log_songs, "a", encoding="utf-8") as song_log,
                Path.open(log_not_found, "a", encoding="utf-8") as not_found_log,
            ):
                playlist_id = create_playlist(
                    session=session,
                    playlist_name=playlist_name,
                    description=description,
                )
                print(f"\nPlaylist created: {playlist_name}")
                playlist_log.write(f"{playlist_name}: {playlist_url(playlist_id)}\n")

                chunk_size = 10
                seen_songs = set()
                for i in range(0, len(lines), chunk_size):
                    if i > 0 and i % 1000 == 0:
                        print(f"Session reinitiated at song count {i}")
                        session = spotify_session(
                            client_id=auth["CLIENT_ID"],
                            client_secret=auth["CLIENT_SECRET"],
                            cache_path=TOKEN_CACHE_PATH,
                        )

                    line_chunk = lines[i : i + chunk_size]
                    # Lenient: for an old playlist, a missing song costs more than a few wrong ones
                    found, not_found = get_song_uris(
                        session=session, playlist=line_chunk, seen=seen_songs, lenient=True
                    )

                    if found:
                        add_songs_to_playlist(
                            session=session, playlist_id=playlist_id, song_uri=[uri for _, uri in found]
                        )
                    for entry, _ in found:
                        song_log.write(f"{entry}\n")
                    for entry in not_found:
                        not_found_log.write(f"{entry}\n")
                    time.sleep(0.5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create Spotify playlists from cleaned Winamp playlist files.")
    parser.add_argument(
        "input_path", type=Path, help="The folder given to playlist_parser; its 'fixed' subfolder is read"
    )
    args = parser.parse_args()

    log_path = PROJECT_ROOT / "log" / "winamp_to_spotify"
    log_path.mkdir(exist_ok=True, parents=True)

    files = sorted(f for f in (args.input_path / "fixed").iterdir() if f.is_file())

    run_through_playlists(files, log_path)
