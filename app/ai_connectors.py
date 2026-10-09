from __future__ import annotations

import os
from dataclasses import dataclass


MODES = ["Local only", "Gemini", "OpenRouter"]


@dataclass
class AIRequest:
    mode: str
    model: str
    purpose: str
    files: list[str]
    content: str
    owner_confirmed_external_transfer: bool = False


def provider_status() -> dict:
    return {
        "default_mode": "Local only",
        "modes": MODES,
        "keys": {
            "Gemini": bool(os.environ.get("LIFEOS_GEMINI_API_KEY")),
            "OpenRouter": bool(os.environ.get("LIFEOS_OPENROUTER_API_KEY")),
        },
        "network_enabled": False,
        "policy": "Provider calls are disabled unless the owner explicitly enables AI Analysis Mode and confirms selected content can leave the device.",
    }


def call_provider(request: AIRequest) -> str:
    if request.mode == "Local only":
        return "Local only mode: no external request was made."
    if not request.owner_confirmed_external_transfer:
        raise PermissionError("Owner confirmation is required before selected content can leave the device.")
    if request.mode == "Gemini":
        key = os.environ.get("LIFEOS_GEMINI_API_KEY")
        if not key:
            raise RuntimeError("LIFEOS_GEMINI_API_KEY is not set.")
        raise RuntimeError("Gemini connector is configured but intentionally not activated during this build.")
    if request.mode == "OpenRouter":
        key = os.environ.get("LIFEOS_OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("LIFEOS_OPENROUTER_API_KEY is not set.")
        raise RuntimeError("OpenRouter connector stub is prepared, but network calls are disabled for this build.")
    raise RuntimeError("Unsupported or inactive AI provider mode.")
