"""Auth header construction for PDP requests."""

from __future__ import annotations


class AuthProvider:
    """Builds outbound auth headers.

    Supports a static API key (``X-Api-Key``) and/or bearer token. Designed to
    be subclassed for token-refresh flows.
    """

    def __init__(self, *, api_key: str = "", bearer_token: str = "") -> None:
        self._api_key = api_key
        self._bearer_token = bearer_token

    def headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._api_key:
            headers["X-Api-Key"] = self._api_key
        if self._bearer_token:
            headers["Authorization"] = f"Bearer {self._bearer_token}"
        return headers
