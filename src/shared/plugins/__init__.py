"""Shared plugin protocol."""
from __future__ import annotations

from shared.plugins.manifest import PluginManifest
from shared.plugins.protocol import REQUEST_EXTENSIONS, RESPONSE_EXTENSIONS
from shared.plugins.registry_schema import PluginRegistrySchema

__all__ = [
    "PluginManifest",
    "PluginRegistrySchema",
    "REQUEST_EXTENSIONS",
    "RESPONSE_EXTENSIONS",
]
