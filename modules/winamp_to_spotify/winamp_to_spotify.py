"""Create Spotify playlists from the cleaned Winamp playlist files.

Takes each '<name>_fixed.txt' written by playlist_parser.py, searches Spotify for every song, and
adds the best matches to a new private playlist. Writes logs of added songs and of songs that
were not found or skipped as duplicates to the 'log\\winamp_to_spotify' folder.
"""

import argparse
import time
from datetime import datetime
from pathlib import Path

from modules.spotify_api import add_songs_to_playlist, create_playlist, get_song_uris, load_auth, spotify_session
from modules.winamp_to_spotify.playlist_parser import ENCODING

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_through_playlists(files, log_path):
    auth = load_auth(Path.home() / ".spotipy")
    session = spotify_session(client_id=auth["CLIENT_ID"], client_secret=auth["CLIENT_SECRET"])

    start_time = datetime.now().strftime("%Y%m%d_%H%M")
    log_playlists = log_path / f"{start_time}_added_playlists_log.txt"

    with Path.open(log_playlists, "a", encoding="ISO-8859-1") as playlist_log:
        for file in files:
            log_songs = log_path / f"{start_time}_[{file.stem}]_added_songs_log.txt"
            log_not_found = log_path / f"{start_time}_[{file.stem}]_not_found_log.txt"
            with (
                Path.open(log_songs, "a", encoding="utf-8") as song_log,
                Path.open(log_not_found, "a", encoding="utf-8") as not_found_log,
            ):
                playlist_name = f"Winamp imports [{file.stem}]"
                playlist_id = create_playlist(
                    session=session,
                    user_id=session.me()["id"],
                    playlist_name=playlist_name,
                    description=f"Created from file: {file.name}",
                )
                print(f"Playlist created: {playlist_name}")
                playlist_log.write(f"{playlist_name}: {playlist_id}\n")

                with Path.open(file, "r", encoding=ENCODING) as input_file:
                    lines = input_file.readlines()

                chunk_size = 10
                seen_songs = set()
                for i in range(0, len(lines), chunk_size):
                    if i > 0 and i % 1000 == 0:
                        print(f"Session reinitiated at song count {i}")
                        session = spotify_session(client_id=auth["CLIENT_ID"], client_secret=auth["CLIENT_SECRET"])

                    line_chunk = lines[i : i + chunk_size]
                    found, not_found = get_song_uris(session=session, playlist=line_chunk, seen=seen_songs)

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
