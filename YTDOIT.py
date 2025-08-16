#!/usr/bin/env python3
# transcript_grabber.py
"""
Grab one video’s transcript or every video in a playlist,
save it to disk, and put it on the clipboard.
"""

import re
import time
import os
from pathlib import Path
from typing import List, Dict
from urllib.parse import urlparse, parse_qs

import pyperclip
from yt_dlp import YoutubeDL
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound


class TranscriptGrabber:
    RAW_PATH = Path("~/Documents/VS rough/rawTranscript.txt").expanduser()
    TXT_PATH = Path("~/Documents/VS rough/transcript.txt").expanduser()

    VIDEO_ID_RX = re.compile(
        r"""^(?:https?://)?            # optional scheme
            (?:www\.)?                # optional www.
            (?:youtu\.be/|            # youtu.be/<id>
            youtube\.com/(?:watch\?v=|embed/|v/))  # youtube.com/watch?v=<id> etc.
            (?P<id>[A-Za-z0-9_-]{11}) # the 11-char ID
        """,
        re.VERBOSE,
    )

    def __init__(self, delay: int = 1):
        """delay = seconds to wait between fetching playlist items (be nice)."""
        self.delay = delay

    # ---------- low-level helpers ---------- #

    def video_id_from_url(self, url: str) -> str | None:
        m = self.VIDEO_ID_RX.match(url)
        return m.group("id") if m else None

    def fetch_transcript_text(self, vid_id: str) -> str:
        """Returns plain transcript text for a single video (English)."""
        try:
            transcript = YouTubeTranscriptApi.get_transcript(vid_id, languages=["en"])
        except (TranscriptsDisabled, NoTranscriptFound):
            print(f"[warning] No transcript for {vid_id}")
            return ""
        # The API returns a list of {'text': str, 'start': float, 'duration': float}
        return " ".join(chunk["text"].strip() for chunk in transcript)

    def write_file(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    # ---------- playlist helpers ---------- #

    def clean_playlist_url(self, url: str) -> str:
        parsed = urlparse(url)
        playlist_id = parse_qs(parsed.query).get("list", [""])[0]
        return f"https://www.youtube.com/playlist?list={playlist_id}"

    def get_playlist_items(self, playlist_url: str) -> List[Dict[str, str]]:
        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            "extract_flat": True,
        }
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(playlist_url, download=False)

        items: List[Dict[str, str]] = []
        for entry in info.get("entries", []):
            if entry:  # skip None placeholders
                items.append(
                    {
                        "id": entry.get("id"),
                        "title": entry.get("title"),
                        "url": f"https://youtu.be/{entry.get('id')}",
                    }
                )
        return items

    # ---------- public high-level methods ---------- #

    def grab_single(self, url: str) -> str:
        vid_id = self.video_id_from_url(url)
        if not vid_id:
            raise ValueError("Could not parse a YouTube video ID from that URL.")

        text = self.fetch_transcript_text(vid_id)
        self.write_file(self.RAW_PATH, text)
        self.write_file(self.TXT_PATH, text)
        pyperclip.copy(text)
        # pyperclip.copy(self.TXT_PATH.read_text(encoding="utf-8"))
        print("[✓] Transcript copied to clipboard and written to disk.")
        return text

    def grab_playlist(self, playlist_url: str) -> str:
        playlist_url = self.clean_playlist_url(playlist_url)
        videos = self.get_playlist_items(playlist_url)
        if not videos:
            raise RuntimeError("Could not fetch playlist metadata.")

        print(f"[i] Found {len(videos)} videos — fetching transcripts…")
        combined: List[str] = []
        for idx, video in enumerate(videos, start=1):
            print(f"  • ({idx}/{len(videos)}) {video['title']}")
            text = self.fetch_transcript_text(video["id"])
            combined.append(
                f"\n{'-'*40}\n{video['title']}\n{'-'*40}\n{text}\n"
            )
            time.sleep(self.delay)

        final_text = "\n".join(combined)
        self.write_file(self.TXT_PATH, final_text)
        pyperclip.copy(final_text)
        print("[✓] Full playlist transcript copied to clipboard and written to disk.")
        return final_text


# ---------- CLI entry point ---------- #

if __name__ == "__main__":
    tg = TranscriptGrabber(delay=15)  # adjust delay if you wish
    while True:
        try:
            url = input("YouTube URL (or q to quit) >> ").strip()
            if url.lower() == "q":
                break
            if "list" in url:
                tg.grab_playlist(url)
            else:
                tg.grab_single(url)
        except Exception as e:
            print(f"[error] {e}")
