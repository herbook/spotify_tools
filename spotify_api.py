import time
from datetime import datetime
from pathlib import Path

import pytz
import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth


def load_auth(file_path):
    auth = {}
    with Path.open(file_path, "r") as file:
        for line in file:
            key, value = line.strip().split("=", 1)
            auth[key] = value

    return auth


def spotify_session(client_id, client_secret):
    # Set up authentication
    session = spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri="http://localhost:8888/callback",
            scope="playlist-modify-private playlist-modify-public",
        )
    )

    return session


def create_playlist(session, user_id, playlist_name, description):
    playlist = session.user_playlist_create(user=user_id, name=playlist_name, public=False, description=description)
    return playlist["id"]


def get_song_uris(session, playlist, max_retries=10, delay=2):
    uris = []
    for item in playlist:
        for attempt in range(max_retries):
            try:
                results = session.search(q=item, type="track", limit=1)
                if results["tracks"]["items"]:
                    song_uri = results["tracks"]["items"][0]["uri"]
                    uris.append(song_uri)
                else:
                    print(f"Not found: {item.strip()}")
                break
            except SpotifyException as e:
                print(f"Spotify API Error: {e}")
                print(f"Timeout occurred. Retrying {attempt + 1}/{max_retries}...")
                time.sleep(delay * (2**attempt))  # Exponential backoff (2s, 4s, 8s...)

    return uris


def add_songs_to_playlist(session, playlist_id, song_uri):
    session.playlist_add_items(playlist_id, song_uri)


def run_through_playlists(session, files, log_path):
    start_time = datetime.now(tz=pytz.timezone("Europe/Helsinki")).strftime("%Y%m%d_%H%M")
    log_playlists = log_path / f"{start_time}_added_playlists_log.txt"

    with Path.open(log_playlists, "a") as playlist_log:
        for file in files:
            log_songs = log_path / f"{start_time}_[{file.stem}]_added_songs_log.txt"
            with Path.open(log_songs, "a") as song_log:
                playlist_name = f"Winamp imports [{file.stem}]"
                playlist_id = create_playlist(
                    session=session,
                    user_id=session.me()["id"],
                    playlist_name=playlist_name,
                    description=f"Created from file: {file.name}",
                )
                print(f"Playlist created: {playlist_name}")
                playlist_log.write(f"{playlist_name}: {playlist_id}\n")

                with Path.open(file, "r", encoding="ISO-8859-1") as input_file:
                    lines = input_file.readlines()

                chunk_size = 10
                for i in range(0, len(lines), chunk_size):
                    line_chunk = lines[i : i + chunk_size]
                    song_uri = get_song_uris(session=session, playlist=line_chunk)

                    add_songs_to_playlist(session=session, playlist_id=playlist_id, song_uri=song_uri)
                    for line in line_chunk:
                        song_log.write(f"{line.strip()}\n")
                    time.sleep(0.5)


input_path = Path(r"\\BatDrive\home\Levytallenteet\misc\soittolistat")
output_path = input_path / "fixed"
output_path.mkdir(exist_ok=True, parents=True)
log_path = Path.cwd() / "log"
log_path.mkdir(exist_ok=True, parents=True)

auth = load_auth(Path(r"C:\Users\Herman\.spotipy"))
session = spotify_session(client_id=auth["CLIENT_ID"], client_secret=auth["CLIENT_SECRET"])

files = [f for f in output_path.iterdir() if f.is_file()]
files = [f for f in files if "leffat" not in str(f)]

run_through_playlists(session, files, log_path)
