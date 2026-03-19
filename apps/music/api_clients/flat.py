"""
music/api_clients/flat.py

Flat.io API client for interactive chord chart notation.

Flat.io is a cloud-based music notation platform. This client
lets the music unit create and manage notation scores directly
from ChurchForce, with the score ID stored on ChordChart.flat_score_id.

Required settings:
    FLAT_API_TOKEN — your Flat.io OAuth token
"""

import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class FlatError(Exception):
    pass


class FlatClient:
    BASE_URL = "https://api.flat.io/v2"

    def __init__(self):
        self.token = getattr(settings, "FLAT_API_TOKEN", "")
        if not self.token:
            raise FlatError("FLAT_API_TOKEN must be set in settings.")

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type":  "application/json",
        }

    def create_score(self, title, privacy="private"):
        """
        Create a new score on Flat.io. Returns the score dict including id.

        privacy: "public" | "private" | "organizationPublic"
        """
        try:
            response = requests.post(
                f"{self.BASE_URL}/scores",
                headers=self._headers(),
                json={"title": title, "privacy": privacy},
                timeout=15,
            )
            data = response.json()
            if "id" not in data:
                raise FlatError(f"Flat.io did not return a score ID: {data}")
            return data
        except requests.RequestException as exc:
            raise FlatError(f"Flat.io create_score failed: {exc}") from exc

    def get_score(self, score_id):
        """Fetch a score by ID. Returns score dict."""
        try:
            response = requests.get(
                f"{self.BASE_URL}/scores/{score_id}",
                headers=self._headers(),
                timeout=10,
            )
            return response.json()
        except requests.RequestException as exc:
            raise FlatError(f"Flat.io get_score failed: {exc}") from exc

    def get_embed_url(self, score_id, theme="default"):
        """Return the Flat.io embed URL for displaying a score in an iframe."""
        return f"https://flat.io/embed/{score_id}?theme={theme}"

    def delete_score(self, score_id):
        """Delete a score from Flat.io (called when ChordChart is deleted)."""
        try:
            requests.delete(
                f"{self.BASE_URL}/scores/{score_id}",
                headers=self._headers(),
                timeout=10,
            )
        except requests.RequestException as exc:
            logger.warning("Flat.io delete_score failed for %s: %s", score_id, exc)