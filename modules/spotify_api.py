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
MIN_MATCH_SCORE = 0.75
LENIENT_MIN_MATCH_SCORE = 0.6  # For sources where a missing song costs more than a wrong one, like old Winamp lists
ARTIST_NAME_RATIO = (
    0.85  # Similarity at which a run of query words counts as the artist's name, e.g. 'Chilli' for 'Chili'
)
MIN_ARTIST_SHARE = 0.5  # More than this share of an artist name's words must appear in the query
TITLE_WORD_RATIO = 0.8  # Similarity at which a query word counts as a word of the track title
MIN_TITLE_LETTERS = 3  # Shorter titles ('3', 'X') match any query containing them, so only whole-text similarity counts
HARD_VERSION_PENALTY = 1.0  # Karaoke and tribute acts are never the song that was asked for, whatever the threshold
SOFT_VERSION_PENALTY = 0.1  # Live, instrumental and cover versions only rank below the original
# Bad token, missing scope or exhausted rate limit: every following song would fail the same way
FATAL_STATUSES = (401, 403, 429)
# Labels that name the same recording rather than a different mix
SAME_SONG_LABELS = re.compile(r"\b(original (mix|version)|(\d{4} )?remaster(ed)?( \d{4})?)\b")
NAME_STOPWORDS = frozenset({"the", "a", "an", "and"})
# Words that do not tell artists apart, so they alone never identify an artist in lenient mode
GENERIC_ARTIST_WORDS = frozenset({"dj", "mc", "band", "project", "orchestra", "feat", "ft", "vs"})
TITLE_SUFFIX = re.compile(r"[\(\[].*?[\)\]]|\s+-\s+.*$")
DASH_SUFFIX = re.compile(r"\s+-\s+.*$")
BRACKET_TEXT = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")
# Bracket text such as '(Radio Edit)' labels a version; any other bracket text may be part of the song's name
VERSION_LABEL = re.compile(r"\b(feat|featuring|ft|with|remix|mix|edit|version|remaster\w*|live|from|radio|\d{4})\b")
UNWANTED_VERSIONS = re.compile(r"\b(karaoke|tribute|live|instrumental|cover|originally performed)\b")
FAKE_ARTISTS = re.compile(r"\b(karaoke|tribute)\b")
HARD_VERSIONS = frozenset({"karaoke", "tribute", "originally performed"})
# Words that make a title suffix name a remix or edit, as in '(Chill Out Mix)' or '- Radio Edit'
MIX_KEYWORDS = frozenset({"remix", "mix", "edit", "version", "dub", "rmx", "rework", "bootleg"})
# 'version' names a remix in a title ('Club Version'), but a query that says 'Album Version' wants the original
MIX_REQUEST_KEYWORDS = MIX_KEYWORDS - {"version"}
MIN_LABEL_WORD_LETTERS = 3  # Shorter words ('in', 'of', 'N') say nothing about which remix a title names
MAX_MIX_NAME_WORDS = 3  # How many words before 'Mix' or 'Remix' in a query can name the remix, like 'Chill Out'
WRONG_MIX_PENALTY = 1.0  # A different remix than the one asked for is never a match
LENIENT_WRONG_MIX_PENALTY = 0.2  # In lenient mode it still matches, but ranks below the original recording


def load_auth(file_path):
    auth = {}
    with Path.open(file_path, "r") as file:
        for line in file:
            if not line.strip():
                continue

            key, value = line.strip().split("=", 1)
            auth[key] = value

    return auth


def save_auth(file_path, auth):
    """Write the credentials as key=value lines, skipping empty values."""
    lines = [f"{key}={value}" for key, value in auth.items() if value]
    Path(file_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def spotify_session(client_id, client_secret, cache_path):
    # Set up authentication; the login token is kept in `cache_path`, not in the working directory
    session = spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri="http://127.0.0.1:8888/callback",
            scope="playlist-modify-private playlist-read-private",
            cache_path=str(cache_path),
        ),
        # spotipy retries 429 and 5xx itself, honoring Spotify's Retry-After header
        requests_timeout=10,
        retries=5,
        status_retries=5,
        backoff_factor=1,
    )

    return session


def create_playlist(session, playlist_name, description):
    # Spotify removed POST /users/{id}/playlists for development-mode apps, which now answer 403
    playlist = session.current_user_playlist_create(name=playlist_name, public=False, description=description)
    return playlist["id"]


def playlist_url(playlist_id):
    return f"https://open.spotify.com/playlist/{playlist_id}"


# Letters that NFKD does not split into a base letter plus accent
TRANSLITERATIONS = str.maketrans(
    {
        "ø": "o",
        "Ø": "O",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "ß": "ss",
        "ð": "d",
        "Ð": "D",
        "þ": "th",
        "Þ": "TH",
        "ł": "l",
        "Ł": "L",
        "đ": "d",
        "Đ": "D",
    }
)


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation, and collapse whitespace for comparison."""
    text = text.translate(TRANSLITERATIONS)
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


def is_spelled_like_any(word: str, others: list[str]) -> bool:
    """Return whether any of `others` is spelled like `word`, allowing for typos."""
    return any(SequenceMatcher(None, word, other).ratio() >= TITLE_WORD_RATIO for other in others)


def strip_artist(artist_words: list[str], query_words: list[str], *, lenient: bool = False) -> list[str] | None:
    """Return the query words left after removing the artist's name, or None when the query does not name the artist.

    First looks for a run of consecutive query words spelled like the artist's name, which tolerates typos and
    'Silver Chair' for 'Silverchair'. Failing that, the name counts as present when more than half of its words
    are in the query ('Elvis Presley' needs both words, 'Jean Michel Jarre' two of three). In lenient mode, one
    distinctive word of the name is enough, so 'Cent' finds '50 Cent'.
    """
    content = [(index, word) for index, word in enumerate(query_words) if word not in NAME_STOPWORDS]
    target = "".join(artist_words)
    best_ratio, first, last = 0.0, 0, 0
    for start in range(len(content)):
        # One word more than the name has lets a single-word name match a split spelling
        for end in range(start + 1, min(len(content), start + len(artist_words) + 1) + 1):
            run = "".join(word for _, word in content[start:end])
            ratio = SequenceMatcher(None, run, target).ratio()
            if ratio > best_ratio:
                best_ratio, first, last = ratio, content[start][0], content[end - 1][0]
    if best_ratio >= ARTIST_NAME_RATIO:
        return query_words[:first] + query_words[last + 1 :]

    shared = {word for word in artist_words if word in query_words}
    if len(shared) / len(artist_words) > MIN_ARTIST_SHARE:
        return [word for word in query_words if word not in shared]
    if not lenient:
        return None

    distinctive = [word for word in artist_words if word not in GENERIC_ARTIST_WORDS] or artist_words
    matched = [word for word in distinctive if is_spelled_like_any(word, query_words)]
    if not matched:
        return None
    return [word for word in query_words if not is_spelled_like_any(word, matched)]


def title_variants(name: str) -> list[str]:
    """List the ways a track title can be written, so a query matches any of them.

    These are the title without suffixes and brackets, without ' - ...', in full, and the text of each bracket that
    is not a version label. Bracket text counts because a song can be named only there, as in
    'Theme From M*A*S*H (Suicide Is Painless)'.
    """
    bracketed = [text for text in BRACKET_TEXT.findall(name) if not VERSION_LABEL.search(normalize(text))]
    return [base_title(name), DASH_SUFFIX.sub("", name), name, *bracketed]


def title_score(rest: list[str], title: str) -> float:
    """Score from 0 to 1 how well the query words left after the artist match a track title.

    It is the better of the share of the title's words found among the query words (so extra words in the query
    cost nothing) and the character similarity of the two texts (so typos are tolerated).
    """
    title_words = normalize(title).split()
    if not title_words:
        return 0.0
    ratio = SequenceMatcher(None, " ".join(rest), " ".join(title_words)).ratio()
    if len("".join(title_words)) < MIN_TITLE_LETTERS:
        return ratio
    found = sum(is_spelled_like_any(word, rest) for word in title_words)
    return max(found / len(title_words), ratio)


def version_penalty(track: dict, query_words: list[str]) -> float:
    """Return how much to subtract from the score for a version of the song that the query did not ask for.

    Karaoke and tribute acts are never the song that was asked for. Live, instrumental and cover versions only rank
    below the original, so they still match when nothing else does.
    """
    names = normalize(" ".join(artist["name"] for artist in track["artists"]))
    # Only the suffix and brackets are checked, so titles like 'Live and Let Die' are not mistaken for live versions
    suffix = normalize(" ".join(TITLE_SUFFIX.findall(track["name"])))
    unwanted = (set(UNWANTED_VERSIONS.findall(suffix)) | set(FAKE_ARTISTS.findall(names))) - set(query_words)
    if not unwanted:
        return 0.0
    return HARD_VERSION_PENALTY if unwanted & HARD_VERSIONS else SOFT_VERSION_PENALTY


def mix_label_words(name: str) -> set[str] | None:
    """Return the words that tell a track's remix apart, or None when its title names no remix.

    '(Chill Out Mix)' gives {'chill', 'out'} and '- Radio Edit' gives {'radio'}. Labels for the original recording,
    like 'Original Mix' or 'Remastered 2011', and other bracket text, like '(feat. X)', do not count.
    """
    label_words: set[str] = set()
    found = False
    for piece in TITLE_SUFFIX.findall(name):
        words = SAME_SONG_LABELS.sub(" ", normalize(piece)).split()
        if MIX_KEYWORDS.intersection(words):
            found = True
            label_words |= set(words) - MIX_KEYWORDS - NAME_STOPWORDS
    return label_words if found else None


def requested_mix_words(rest: list[str], name: str) -> list[str] | None:
    """Return the words naming the remix that a query asks for, or None when it asks for no remix.

    The list is empty for a plain 'Remix'. 'Original Mix' and words from the track's own title are not a request.
    """
    own_title = set(normalize(base_title(name)).split())
    words = [word for word in SAME_SONG_LABELS.sub(" ", " ".join(rest)).split() if word not in own_title]
    named: list[str] = []
    asked = False
    for position, word in enumerate(words):
        if word in MIX_REQUEST_KEYWORDS:
            asked = True
            before = words[max(0, position - MAX_MIX_NAME_WORDS) : position]
            named += [w for w in before if len(w) >= MIN_LABEL_WORD_LETTERS and w not in MIX_KEYWORDS | NAME_STOPWORDS]
    return named if asked else None


def mix_penalty(rest: list[str], name: str, *, lenient: bool = False) -> float:
    """Return how much to subtract from the score when the query asks for a specific remix that the track is not.

    A track whose remix label shares no distinctive word with the requested remix is a different remix: never a
    match, or in lenient mode one that ranks below the original recording. The original recording itself only ranks
    below the requested remix. A plain 'Remix' in the query accepts any remix.
    """
    requested = requested_mix_words(rest=rest, name=name)
    if requested is None:
        return 0.0

    label = mix_label_words(name=name)
    if label is None:
        return SOFT_VERSION_PENALTY
    distinctive = [word for word in label if len(word) >= MIN_LABEL_WORD_LETTERS]
    if not requested or not distinctive:
        return 0.0
    # Joined words also match a label written differently, like 'Chillout' for 'Chill Out'
    joined = "".join(requested)
    if any(is_spelled_like_any(word, requested) or word in joined for word in distinctive):
        return 0.0
    return LENIENT_WRONG_MIX_PENALTY if lenient else WRONG_MIX_PENALTY


def match_score(query: str, track: dict, *, lenient: bool = False) -> float:
    """Score how well a Spotify track matches an 'Artist Title' query, from 0 to 1.

    The query is a flat string, so the artist is found by looking for each of the track's artists in it. The words
    left over are scored against the track's title only, so a different song by the right artist scores low. A track
    scores 0 unless one of its artists is named in the query; `lenient` relaxes what counts as naming the artist. When
    the query names a specific remix, a track with a different remix scores 0 (in lenient mode, below the original).
    """
    query_words = normalize(query).split()
    if not query_words:
        return 0.0

    titles = title_variants(name=track["name"])
    best = 0.0
    for artist in track["artists"]:
        artist_words = [word for word in normalize(artist["name"]).split() if word not in NAME_STOPWORDS]
        # normalize() drops non-Latin names entirely, so an empty name cannot be checked and is let through
        rest = (
            strip_artist(artist_words=artist_words, query_words=query_words, lenient=lenient)
            if artist_words
            else query_words
        )
        if rest is None:
            continue
        score = max(title_score(rest=rest, title=title) for title in titles)
        best = max(best, score - mix_penalty(rest=rest, name=track["name"], lenient=lenient))
    return max(best - version_penalty(track=track, query_words=query_words), 0.0)


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
    *,
    lenient: bool = False,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Find the best-matching Spotify track for each query line, skipping songs already in `seen`.

    Returns (found, not_found): found holds (log entry, uri) pairs; not_found holds log entries with the reason
    (no match, error or duplicate). Pass the same `seen` set for every chunk of one playlist; it is updated in place
    with each added song's key (from `key_func`) and URI. `lenient` accepts weaker matches, for sources where
    including every relevant song matters more than avoiding a few wrong ones.
    """
    min_score = LENIENT_MIN_MATCH_SCORE if lenient else MIN_MATCH_SCORE
    seen = set() if seen is None else seen
    found = []
    not_found = []
    for item in playlist:
        query = item.strip()
        if not query:
            continue

        try:
            tracks = search_tracks(session=session, query=query)
            # Spotify's API search is strict, so bracket text it does not know can push the right track out of the results
            plain_query = " ".join(BRACKET_TEXT.sub(" ", query).split())
            if plain_query != query and not any(
                match_score(query=query, track=track, lenient=lenient) >= min_score for track in tracks
            ):
                tracks += search_tracks(session=session, query=plain_query)
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
        best = max(tracks, key=lambda track: match_score(query=query, track=track, lenient=lenient))
        score = match_score(query=query, track=best, lenient=lenient)
        best_label = f"{', '.join(a['name'] for a in best['artists'])} - {best['name']}"
        if score < min_score:
            print(f"Not found: {query} (best: {best_label}, score: {score:.2f})")
            not_found.append(f"{query} -> best: {best_label} (score: {score:.2f})")
            continue

        key = key_func(best)
        if key in seen or best["uri"] in seen:
            print(f"Duplicate skipped: {query} ({best_label})")
            not_found.append(f"{query} -> duplicate of {best_label}")
            continue

        seen.update((key, best["uri"]))
        found.append((f"{query} -> {best_label} (score: {score:.2f})", best["uri"]))

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
    """Return all items of a playlist, in playlist order."""
    items = []
    while True:
        try:
            page = session.playlist_items(playlist_id, limit=50, offset=len(items), additional_types=("track",))
        except SpotifyException as e:
            if e.http_status == 403:  # noqa: PLR2004
                # Development-mode apps may only read the items of playlists the user owns or collaborates on
                msg = (
                    "Spotify does not allow reading this playlist's songs. A Spotify app in Development Mode can only"
                    " read playlists you own or collaborate on, so add its songs to a playlist of your own first."
                )
                raise SystemExit(msg) from e
            raise
        items.extend(page["items"])
        if not page["next"] or not page["items"]:
            return items
