"""
music/api_clients/spotify.py

Spotify API client for track search and metadata lookup.

Used by music/views.py to enrich Track records with BPM, key,
and Spotify ID. Never called at import time — instantiated lazily
so missing credentials don't crash startup.

Required settings:
    SPOTIFY_CLIENT_ID
    SPOTIFY_CLIENT_SECRET
"""

import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class SpotifyError(Exception):
    pass


class SpotifyClient:
    TOKEN_URL = "https://accounts.spotify.com/api/token"
    BASE_URL  = "https://api.spotify.com/v1"

    def __init__(self):
        self.client_id     = getattr(settings, "SPOTIFY_CLIENT_ID", "")
        self.client_secret = getattr(settings, "SPOTIFY_CLIENT_SECRET", "")

        if not self.client_id or not self.client_secret:
            raise SpotifyError(
                "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set."
            )

        self.access_token = self._get_access_token()

    def _get_access_token(self):
        try:
            response = requests.post(
                self.TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(self.client_id, self.client_secret),
                timeout=10,
            )
            token = response.json().get("access_token")
            if not token:
                raise SpotifyError("Spotify did not return an access token.")
            return token
        except requests.RequestException as exc:
            raise SpotifyError(f"Spotify token request failed: {exc}") from exc

    def _headers(self):
        return {"Authorization": f"Bearer {self.access_token}"}

    def search_track(self, query, limit=5):
        """
        Search for tracks by title/artist. Returns list of track dicts:
            id, name, artists, tempo, key, preview_url
        """
        try:
            response = requests.get(
                f"{self.BASE_URL}/search",
                headers=self._headers(),
                params={"q": query, "type": "track", "limit": limit},
                timeout=10,
            )
            items = response.json().get("tracks", {}).get("items", [])
            return [
                {
                    "id":          t["id"],
                    "name":        t["name"],
                    "artists":     ", ".join(a["name"] for a in t["artists"]),
                    "preview_url": t.get("preview_url"),
                }
                for t in items
            ]
        except requests.RequestException as exc:
            raise SpotifyError(f"Spotify search failed: {exc}") from exc

    def get_audio_features(self, spotify_id):
        """
        Fetch audio features (tempo, key) for a track by Spotify ID.
        Returns dict with tempo (BPM) and key (integer 0-11, Camelot mapping).
        """
        try:
            response = requests.get(
                f"{self.BASE_URL}/audio-features/{spotify_id}",
                headers=self._headers(),
                timeout=10,
            )
            data = response.json()
            return {
                "tempo": round(data.get("tempo", 0)),
                "key":   _spotify_key_to_label(data.get("key", -1), data.get("mode", 1)),
            }
        except requests.RequestException as exc:
            raise SpotifyError(f"Spotify audio features request failed: {exc}") from exc


# Spotify key integers → musical key labels
_PITCH_CLASSES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

def _spotify_key_to_label(key_int, mode):
    """Convert Spotify's key integer + mode to a readable key label."""
    if key_int == -1:
        return ""
    label = _PITCH_CLASSES[key_int % 12]
    return label if mode == 1 else f"{label}m"