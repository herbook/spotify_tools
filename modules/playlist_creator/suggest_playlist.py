"""Create a Spotify playlist of AI-suggested songs based on a seed file.

Sends the seed songs and your criteria (genre, BPM, style, mood, ...) to Google Gemini, looks up each
suggested song on Spotify to weed out invented ones, skips the seed songs and duplicates, and adds
the matches to a new private playlist. A log of added and skipped songs, with reasons, goes to 'log\\playlist_creator'.
"""

import argparse
import re
import time
from datetime import datetime
from pathlib import Path

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from modules.playlist_creator.seed import read_seed
from modules.spotify_api import (
    add_songs_to_playlist,
    create_playlist,
    exact_track_key,
    get_song_uris,
    load_auth,
    spotify_session,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_COUNT = 30
DEFAULT_CRITERIA = "similar to the seed songs"
MAX_ADD_BATCH = 100  # Spotify accepts at most 100 items per add request
MAX_GEMINI_ATTEMPTS = 5
GEMINI_TIMEOUT_SECONDS = 500  # Longest silence allowed while waiting for Gemini's response data
SYSTEM_INSTRUCTION = (
    "You are a music expert who builds playlists. Suggest only real songs that you are confident exist, "
    "using the exact artist and title. Never suggest a track that is already in the seed list; "
    "a different remix or version of a seed song is fine."
)
SIMILARITY_GUIDE = """\
How to judge whether a song is similar, in order of importance:
1. The user's criteria, which override everything else.
2. Genre and subgenre (for example boom bap, not just hip hop).
3. Mood, vibe and energy level.
4. Tempo (BPM range) and groove, including beat or rap style.
5. Era and production sound (for example dusty and sample-based versus polished and modern).
6. Vocals: delivery or flow, singer type and language.
7. Instrumentation and lyrical themes.
Where the criteria say nothing, infer what the seed songs have in common and match that.
Popularity is irrelevant: do not favour well-known songs, deep cuts are fine.
Prefer original studio versions; avoid live versions and covers unless the criteria ask for them.
Unless the criteria say otherwise, suggest at most three songs per artist."""


class Suggestion(BaseModel):
    """One song proposed by the model."""

    artist: str
    title: str
    reason: str


class Suggestions(BaseModel):
    """The model's answer: a list of proposed songs and a short summary of the playlist."""

    songs: list[Suggestion]
    summary: str


def build_prompt(songs: list[str], criteria: str, count: int) -> str:
    """Build the request text from the seed songs and the criteria."""
    seed_lines = "\n".join(f"- {song}" for song in songs)
    return (
        f"Seed songs:\n{seed_lines}\n\n"
        f"Criteria: {criteria}\n\n"
        f"{SIMILARITY_GUIDE}\n\n"
        f"Suggest {count} songs that match the criteria and are similar to the seed songs. "
        "For each song give the artist, the exact title and one short sentence on why it fits. "
        "For a remix or other version, put the mix name in the title as it appears on Spotify, "
        "for example 'Title - Artist Remix'. "
        "Finally, write a summary of two or three sentences on the overall character of the playlist "
        "and how it relates to the given seed."
    )


def suggest_songs(api_key: str, model: str, prompt: str) -> Suggestions:
    """Ask Gemini for song suggestions, showing a progress line while it answers; returns the parsed Suggestions."""
    client = genai.Client(
        api_key=api_key,
        # Server errors and overload (503) are usually temporary, so retry with growing delays; a 429 is a quota
        # that can stay used up for hours, so it is not retried
        http_options=types.HttpOptions(
            timeout=GEMINI_TIMEOUT_SECONDS * 1000,
            retry_options=types.HttpRetryOptions(
                attempts=6, initial_delay=2.0, max_delay=60.0, http_status_codes=[500, 502, 503, 504]
            ),
        ),
    )

    def request() -> Suggestions:
        print(f"Asking {model} (waiting up to {GEMINI_TIMEOUT_SECONDS}s for data)...", flush=True)
        started = time.monotonic()
        stream = client.models.generate_content_stream(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=Suggestions,
                # Thought chunks only drive the progress line; their text is not shown
                thinking_config=types.ThinkingConfig(include_thoughts=True),
            ),
        )
        answer = []
        for chunks, chunk in enumerate(stream, start=1):
            print(f"\rReceiving: {chunks} chunks, {time.monotonic() - started:.0f}s", end="", flush=True)
            content = chunk.candidates[0].content if chunk.candidates else None
            parts = (content.parts if content else None) or []
            answer.extend(part.text for part in parts if part.text and not part.thought)
        print()
        return Suggestions.model_validate_json("".join(answer))

    # The SDK retries only HTTP status errors, not dropped connections or timeouts
    for attempt in range(1, MAX_GEMINI_ATTEMPTS):
        try:
            return request()
        except httpx.TransportError as e:
            wait = min(5 * 2 ** (attempt - 1), 60)
            print(f"\nNetwork error from Gemini: {e}. Retrying {attempt}/{MAX_GEMINI_ATTEMPTS - 1} in {wait}s...")
            time.sleep(wait)
    return request()


def quota_message(error: errors.ClientError, model: str) -> str:
    """Explain a Gemini 429 error: which quota ran out and when it can be used again."""
    entries = error.details.get("error", {}).get("details", []) if isinstance(error.details, dict) else []
    retry_delay = next((entry["retryDelay"] for entry in entries if "retryDelay" in entry), "")
    violation = next((v for entry in entries for v in entry.get("violations", [])), {})
    message = f"Gemini quota exceeded for {model} ({violation.get('quotaId', 'unknown quota')}"
    if "quotaValue" in violation:
        message += f", limit {violation['quotaValue']}"
    message += ")."
    if retry_delay:
        hours, rest = divmod(int(float(retry_delay.removesuffix("s"))), 3600)
        message += f" Retry in about {f'{hours}h {rest // 60}m' if hours else f'{rest // 60}m {rest % 60}s'}."
    return f"{message} The quota is per model, so another one, e.g. --model gemini-3.5-flash-lite, may still work."


def suggest_or_exit(api_key: str, model: str, prompt: str) -> Suggestions:
    """Run suggest_songs, but stop with a clear message when Gemini's quota is used up."""
    try:
        return suggest_songs(api_key=api_key, model=model, prompt=prompt)
    except errors.ClientError as e:
        if e.code != 429:
            raise
        message = quota_message(error=e, model=model)
        raise SystemExit(message) from e


def seed_keys(songs: list[str]) -> set[str]:
    """Return exact-song keys for the 'Artist - Title' seed lines, so those exact songs are never added."""
    keys = set()
    for line in songs:
        artist, separator, title = line.partition(" - ")
        if separator:
            keys.add(exact_track_key(track={"artists": [{"name": artist.split(",")[0].strip()}], "name": title}))
    return keys


def result_text(entry: str) -> str:
    """Return the part of a get_song_uris log entry after 'query -> '."""
    return entry.partition(" -> ")[2] or entry


def table_cell(text: str) -> str:
    """Make text safe for a Markdown table cell."""
    return " ".join(text.split()).replace("|", "\\|")


def build_log(  # noqa: PLR0913
    *,
    seed_name: str,
    criteria: str,
    model: str,
    playlist_name: str | None,
    summary: str,
    rows: list[tuple[str, str, str, str]],
) -> str:
    """Build the Markdown log: a short summary and a table with one row per suggested song."""
    added = sum(1 for status, *_ in rows if status == "ADDED")
    lines = [
        "# AI playlist log",
        "",
        f"- **Seed:** {seed_name}",
        f"- **Criteria:** {criteria}",
        f"- **Model:** {model}",
        f"- **Playlist:** {playlist_name or 'not created'}",
        f"- **Songs:** {added} added, {len(rows) - added} skipped",
        f"- **Summary from Gemini:** {' '.join(summary.split())}",
        "",
        "| # | Status | Suggested | Spotify result | Gemini's reason |",
        "|---|--------|-----------|----------------|-----------------|",
    ]
    for number, (status, suggested, result, reason) in enumerate(rows, start=1):
        cells = " | ".join(table_cell(cell) for cell in (suggested, result, reason))
        lines.append(f"| {number} | {status} | {cells} |")
    return "\n".join(lines) + "\n"


def main(seed_path: Path, criteria: str | None, count: int | None, name: str | None, model: str) -> None:
    """Create a private Spotify playlist of songs that Gemini suggests for the seed file."""
    seed = read_seed(path=seed_path)
    if not seed.songs:
        raise SystemExit(f"No songs found in {seed_path}")

    criteria = criteria or seed.settings.get("criteria") or DEFAULT_CRITERIA
    count = count or int(seed.settings.get("count") or DEFAULT_COUNT)
    name = name or seed.settings.get("name") or f"AI suggestions [{seed_path.stem}]"

    auth = load_auth(Path.home() / ".spotipy")
    gemini_key = auth.get("GEMINI_API_KEY")
    if not gemini_key:
        raise SystemExit("Add a GEMINI_API_KEY=... line to ~/.spotipy")

    answer = suggest_or_exit(
        api_key=gemini_key, model=model, prompt=build_prompt(songs=seed.songs, criteria=criteria, count=count)
    )
    suggestions = answer.songs
    print(f"{model} suggested {len(suggestions)} songs\n")

    session = spotify_session(client_id=auth["CLIENT_ID"], client_secret=auth["CLIENT_SECRET"])
    seen = seed_keys(songs=seed.songs)
    uris = []
    rows = []
    for suggestion in suggestions:
        suggested = f"{suggestion.artist} - {suggestion.title}"
        found, not_found = get_song_uris(
            session=session, playlist=[f"{suggestion.artist} {suggestion.title}"], seen=seen, key_func=exact_track_key
        )
        for entry, uri in found:
            uris.append(uri)
            rows.append(("ADDED", suggested, result_text(entry), suggestion.reason))
        for entry in not_found:
            rows.append(("SKIPPED", suggested, result_text(entry), suggestion.reason))

    if uris:
        playlist_id = create_playlist(
            session=session,
            user_id=session.me()["id"],
            playlist_name=name,
            description=re.split(r"(?<=[.!?])\s+", answer.summary.strip())[0][:300],
        )
        for i in range(0, len(uris), MAX_ADD_BATCH):
            add_songs_to_playlist(session=session, playlist_id=playlist_id, song_uri=uris[i : i + MAX_ADD_BATCH])
        print(f"\nPlaylist created: {name} ({len(uris)} songs)")
    else:
        print("No songs matched on Spotify, so no playlist was created")

    start_time = datetime.now().strftime("%Y%m%d_%H%M")
    log_path = PROJECT_ROOT / "log" / "playlist_creator"
    log_path.mkdir(exist_ok=True, parents=True)
    log_file = log_path / f"{start_time}_[{seed_path.stem}]_ai_playlist_log.md"
    log_text = build_log(
        seed_name=seed_path.name,
        criteria=criteria,
        model=model,
        playlist_name=name if uris else None,
        summary=answer.summary,
        rows=rows,
    )
    log_file.write_text(log_text, encoding="utf-8")

    added = sum(1 for status, *_ in rows if status == "ADDED")
    print(f"\nSummary from Gemini: {answer.summary}")
    print(f"\n{added} added, {len(rows) - added} skipped")
    print(f"Log: {log_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a Spotify playlist of AI-suggested songs from a seed file.")
    parser.add_argument("seed", type=Path, help="Seed text file: one 'Artist - Title' per line")
    parser.add_argument("--criteria", help="What the playlist should be like (overrides 'criteria:' in the seed file)")
    parser.add_argument("--count", type=int, help=f"Songs to suggest (default {DEFAULT_COUNT})")
    parser.add_argument("--name", help="Playlist name (overrides 'name:' in the seed file)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Gemini model (default {DEFAULT_MODEL})")
    args = parser.parse_args()

    main(seed_path=args.seed, criteria=args.criteria, count=args.count, name=args.name, model=args.model)
