"""Server API layer."""
from __future__ import annotations


def create_app():
    from backend.api.app import create_app as _create

    return _create()


__all__ = ["create_app"]
