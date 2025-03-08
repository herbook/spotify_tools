import re
from pathlib import Path

from bs4 import BeautifulSoup


def clean_text_file(lines):
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
        # cleaned_song = re.sub(r" - ", " ", cleaned_song)  # Remove hyphens with spaces on both sides
        cleaned_song = re.sub(r"[\'\"]", "", cleaned_song)  # Remove single and double quotes

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

    return sorted(set(cleaned_songs))


#####

input_path = Path(r"\\BatDrive\home\Levytallenteet\misc\soittolistat")
output_path = input_path / "fixed"
output_path.mkdir(exist_ok=True, parents=True)

files = [f for f in input_path.iterdir() if f.is_file()]

for one_file in files:
    # Load the HTML file
    with Path.open(one_file, "r", encoding="ISO-8859-1") as input_file:
        if one_file.suffix in [".htm", ".html"]:
            soup = BeautifulSoup(input_file, "html.parser")

            ul_tag = soup.ul
            if ul_tag:
                string = str(ul_tag.small)
            else:
                lines = soup.find_all("a")

            lines = string.replace("<small>", "").split("<br/>")

            html_tag_pattern = re.compile(r"<.*?>")  # Regex to detect HTML tags
            lines = [line for line in lines if not html_tag_pattern.search(line)]

        else:
            lines = input_file.readlines()

        cleaned_songs = clean_text_file(lines)

    new_filename = output_path / f"{one_file.stem}_{one_file.suffix[1:]}_fixed.txt"

    with Path.open(new_filename, "w", encoding="ISO-8859-1") as output_file:
        output_file.write("\n".join(cleaned_songs))
