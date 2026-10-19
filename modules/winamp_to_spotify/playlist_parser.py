"""Clean old Winamp playlist exports (.htm, .html, .txt) into plain search-ready song lists.

Reads every playlist file in the input folder, strips numbering, durations, file extensions and
other noise, removes duplicates, and writes one cleaned '<name>_fixed.txt' per file to the 'fixed'
subfolder. Those files are the input for winamp_to_spotify.py.
"""

import argparse
import re
from pathlib import Path

from bs4 import BeautifulSoup

ENCODING = "cp1252"  # Winamp-era exports are Windows ANSI, which ISO-8859-1 misreads in 0x80-0x9F
LINE_BREAK = "\x1f"  # Marks the <br> between songs; newlines inside a song are only hard wrapping


def clean_text_file(lines: list[str]) -> list[str]:
    """Clean raw playlist lines into a sorted list of unique search strings."""
    html_entities = {
        "&lt;": "<",
        "&gt;": ">",
        "&amp;": "&",
        "&quot;": '"',
        "&apos;": "'",
    }

    cleaned_songs = []
    for line in lines:
        cleaned_song = line.strip()

        for key, val in html_entities.items():
            cleaned_song = re.sub(key, val, cleaned_song)  # Transform html symbols correctly

        cleaned_song = re.sub(r"\n", "", cleaned_song)  # Remove line breaks
        cleaned_song = re.sub(r"[\(\{\[\<].*?[\)\}\]\>]", "", cleaned_song)  # Remove '(...)' and '[...]' and '{...}'
        cleaned_song = re.sub(r"[\'\"]", "", cleaned_song)  # Remove single and double quotes
        cleaned_song = re.sub(r"[\u2019\u00b4]", "'", cleaned_song)  # Replace special apostrophes
        cleaned_song = re.sub(r"[\\/]", "", cleaned_song)  # Remove \ and /

        cleaned_song = re.sub(r"unknown", "", cleaned_song, flags=re.IGNORECASE)  # Remove the expression "unknown"
        cleaned_song = re.sub("-+", "-", cleaned_song)  # Strip double '-'
        cleaned_song = re.sub("-", " ", cleaned_song)  # Change '-' to ' '
        cleaned_song = re.sub(r"\s+", " ", cleaned_song)  # Remove extra spaces
        cleaned_song = cleaned_song.split(".mp3", 1)[0]  # Remove '.mp3' suffixes
        cleaned_song = cleaned_song.split(".wma", 1)[0]  # Remove '.mp3' suffixes
        cleaned_song = cleaned_song.split(".avi", 1)[0]  # Remove '.avi' suffixes
        cleaned_song = cleaned_song.split(".mpeg", 1)[0]  # Remove '.mpeg' suffixes
        cleaned_song = cleaned_song.lstrip("0123456789.- ")  # Strip numbering from the beginning
        cleaned_song = cleaned_song.strip()

        if len(cleaned_song) > 0:
            cleaned_songs.append(cleaned_song)

    unique_songs = {}
    for song in cleaned_songs:
        unique_songs.setdefault(song.casefold(), song)  # First spelling wins

    return sorted(unique_songs.values(), key=str.casefold)


def read_playlist_lines(file: Path) -> list[str]:
    """Return the raw song lines of a playlist file; HTML exports are read from the <ul><small> block or the song links.

    Raises ValueError if an HTML file has neither.
    """
    with Path.open(file, "r", encoding=ENCODING) as input_file:
        if file.suffix.lower() not in [".htm", ".html"]:
            return input_file.readlines()

        soup = BeautifulSoup(input_file, "html.parser")

    ul_tag = soup.ul
    small_tag = ul_tag.small if ul_tag else None
    if small_tag is not None:
        for br_tag in small_tag.find_all("br"):
            br_tag.replace_with(LINE_BREAK)

        return small_tag.get_text().split(LINE_BREAK)

    # Exports without a list put each song in a link: <a href="file:///...">Artist - Title [m:ss]</a>
    links = soup.find_all("a")
    if not links:
        msg = f"No <ul><small> song list or song links found in {file.name}"
        raise ValueError(msg)

    return [link.get_text() for link in links]


def main(input_path: Path, output_path: Path) -> None:
    """Convert every playlist file in input_path into a cleaned text file in output_path."""
    output_path.mkdir(exist_ok=True, parents=True)

    files = [f for f in input_path.iterdir() if f.is_file()]

    for one_file in files:
        try:
            lines = read_playlist_lines(file=one_file)
        except ValueError as e:
            print(f"Skipped: {e}")
            continue

        cleaned_songs = clean_text_file(lines=lines)

        new_filename = output_path / f"{one_file.stem}_{one_file.suffix[1:]}_fixed.txt"

        with Path.open(new_filename, "w", encoding=ENCODING) as output_file:
            output_file.write("\n".join(cleaned_songs))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clean Winamp playlist exports into search-ready song lists.")
    parser.add_argument(
        "input_path", type=Path, help="Folder with the Winamp exports; output goes to its 'fixed' subfolder"
    )
    args = parser.parse_args()

    main(input_path=args.input_path, output_path=args.input_path / "fixed")
