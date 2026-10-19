"""Reusable Spotify Web API helpers (via spotipy) for finding songs and creating playlists.

Covers authentication, best-match track search with retries and match scoring, duplicate detection,
playlist creation, adding songs to a playlist, and reading a playlist's items. It has no playlist
source of its own, so any script can use it to build Spotify playlists.
"""

import re
import time
import unicodedata
from collections.abc import Callable
from difflib import SequenceMatcher
from pathlib import Path

import spotipy
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ReadTimeout, RequestException
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

SEARCH_LIMIT = 10
MIN_MATCH_SCORE = 0.6
MIN_ARTIST_OVERLAP = 0.5  # Share of an artist name's words that must appear in the query
# Bad token, missing scope or exhausted rate limit: every following song would fail the same way
FATAL_STATUSES = (401, 403, 429)
# Labels that name the same recording rather than a different mix
SAME_SONG_LABELS = re.compile(r"\b(original (mix|version)|(\d{4} )?remaster(ed)?( \d{4})?)\b")


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
            redirect_uri="http://127.0.0.1:8888/callback",
            scope="playlist-modify-private playlist-read-private",
        ),
        # spotipy retries 429 and 5xx itself, honoring Spotify's Retry-After header
        requests_timeout=10,
        retries=5,
        status_retries=5,
        backoff_factor=1,
    )

    return session


def create_playlist(session, user_id, playlist_name, description):
    playlist = session.user_playlist_create(user=user_id, name=playlist_name, public=False, description=description)
    return playlist["id"]


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation, and collapse whitespace for comparison."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s]", " ", text.lower().replace("'", ""))
    return " ".join(text.split())


def base_title(name: str) -> str:
    """Drop suffixes like '(Remastered 2009)' or ' - Radio Edit' from a track title."""
    return re.sub(r"\s*[\(\[].*?[\)\]]|\s+-\s+.*$", "", name)


def track_key(track: dict) -> str:
    """Identify a song by its primary artist and title, ignoring album and version suffixes."""
    text = f"{track['artists'][0]['name']} {base_title(track['name'])}"
    # normalize() drops non-Latin characters entirely, so keep the raw text as the key for those
    return normalize(text) or text.casefold()


def exact_track_key(track: dict) -> str:
    """Identify a song by primary artist and full title, so a different remix counts as a different song.

    The album and labels like 'Original Mix' or 'Remastered 2011' are ignored.
    """
    text = f"{track['artists'][0]['name']} {track['name']}"
    key = " ".join(SAME_SONG_LABELS.sub(" ", normalize(text)).split())
    # normalize() drops non-Latin characters entirely, so keep the raw text as the key for those
    return key or text.casefold()


def match_score(query: str, track: dict) -> float:
    """Score how well a Spotify track matches an 'Artist Title' query, from 0 to 1.

    A track scores 0 unless one of its artists is named in the query, so a title-only hit never matches.
    """
    artists = " ".join(artist["name"] for artist in track["artists"])
    query = normalize(query)

    query_tokens = set(query.split())
    if not query_tokens:
        return 0.0

    for artist in track["artists"]:
        artist_tokens = set(normalize(artist["name"]).split())
        # normalize() drops non-Latin names entirely, so an empty name cannot be checked and is let through
        if not artist_tokens or len(artist_tokens & query_tokens) / len(artist_tokens) >= MIN_ARTIST_OVERLAP:
            break
    else:
        return 0.0

    scores = []
    # The parser strips mix names from queries, but an AI suggestion may name a specific mix, so try both titles
    for title in (base_title(track["name"]), track["name"]):
        candidate = normalize(f"{artists} {title}")
        token_overlap = len(query_tokens & set(candidate.split())) / len(query_tokens)
        scores.append(max(token_overlap, SequenceMatcher(None, query, candidate).ratio()))
    return max(scores)


def search_tracks(session: spotipy.Spotify, query: str, max_retries: int = 3, delay: int = 2) -> list[dict]:
    """Search tracks, retrying only network timeouts/connection errors with capped exponential backoff."""
    for attempt in range(max_retries + 1):
        try:
            return session.search(q=query, type="track", limit=SEARCH_LIMIT)["tracks"]["items"]
        except (ReadTimeout, RequestsConnectionError) as e:
            if attempt == max_retries:
                raise
            wait = min(delay * 2**attempt, 30)
            print(f"Network error: {e}. Retrying {attempt + 1}/{max_retries} in {wait}s...")
            time.sleep(wait)
    return []


def get_song_uris(
    session: spotipy.Spotify,
    playlist: list[str],
    seen: set[str] | None = None,
    key_func: Callable[[dict], str] = track_key,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Find the best-matching Spotify track for each query line, skipping songs already in `seen`.

    Returns (found, not_found): found holds (log entry, uri) pairs; not_found holds log entries with the reason
    (no match, error or duplicate). Pass the same `seen` set for every chunk of one playlist; it is updated in place
    with each added song's key (from `key_func`) and URI.
    """
    seen = set() if seen is None else seen
    found = []
    not_found = []
    for item in playlist:
        query = item.strip()
        if not query:
            continue

        try:
            tracks = search_tracks(session=session, query=query)
        except (SpotifyException, RequestException) as e:
            if isinstance(e, SpotifyException) and e.http_status in FATAL_STATUSES:
                raise
            print(f"Error: {query}: {e}")
            not_found.append(f"{query} -> error: {e}")
            continue

        if not tracks:
            print(f"Not found: {query}")
            not_found.append(f"{query} -> no results")
            continue

        # max() keeps the first of equal scores, so Spotify's own relevance order breaks ties
        best = max(tracks, key=lambda track: match_score(query=query, track=track))
        score = match_score(query=query, track=best)
        best_label = f"{', '.join(a['name'] for a in best['artists'])} - {best['name']}"
        if score < MIN_MATCH_SCORE:
            print(f"Not found: {query} (best: {best_label}, {score:.2f})")
            not_found.append(f"{query} -> best: {best_label} ({score:.2f})")
            continue

        key = key_func(best)
        if key in seen or best["uri"] in seen:
            print(f"Duplicate skipped: {query} ({best_label})")
            not_found.append(f"{query} -> duplicate of {best_label}")
            continue

        seen.update((key, best["uri"]))
        found.append((f"{query} -> {best_label} ({score:.2f})", best["uri"]))

    return found, not_found


def add_songs_to_playlist(session, playlist_id, song_uri):
    session.playlist_add_items(playlist_id, song_uri)


def extract_playlist_id(link: str) -> str:
    """Return the playlist ID from a Spotify URL, URI or bare ID."""
    return re.split(r"[/:]", link.split("?", 1)[0].rstrip("/"))[-1]


def get_playlist_name(session: spotipy.Spotify, playlist_id: str) -> str:
    """Return the name of a playlist."""
    return session.playlist(playlist_id, fields="name")["name"]


def get_playlist_items(session: spotipy.Spotify, playlist_id: str) -> list[dict]:
    """Return all items of a playlist the user owns or collaborates on, in playlist order."""
    items = []
    while True:
        page = session.playlist_items(playlist_id, limit=50, offset=len(items), additional_types=("track",))
        items.extend(page["items"])
        if not page["next"] or not page["items"]:
            return items
