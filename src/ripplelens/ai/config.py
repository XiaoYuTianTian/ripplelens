"""AI reviewer settings: a user-level JSON file, overridden by RIPPLELENS_AI_* environment variables.

The API key never leaves this process: it is not written into impact.json and `public()`
only reports whether one is set.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROVIDERS = ("openai", "anthropic")
DEFAULT_BASE = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com"}
ENV = {
    "provider": "RIPPLELENS_AI_PROVIDER",
    "base_url": "RIPPLELENS_AI_BASE_URL",
    "model": "RIPPLELENS_AI_MODEL",
    "api_key": "RIPPLELENS_AI_API_KEY",
}


def config_dir() -> Path:
    if os.environ.get("RIPPLELENS_CONFIG_DIR"):
        return Path(os.environ["RIPPLELENS_CONFIG_DIR"])
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "ripplelens"


def config_path() -> Path:
    return config_dir() / "config.json"


@dataclass
class AiConfig:
    provider: str = "openai"
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    enabled: bool = False
    auto: bool = True
    acknowledged: bool = False  # the user saw the "code is sent to this endpoint" notice
    from_env: list[str] = field(default_factory=list)

    @property
    def endpoint(self) -> str:
        return (self.base_url or DEFAULT_BASE.get(self.provider, "")).rstrip("/")

    @property
    def configured(self) -> bool:
        # local OpenAI-compatible servers (Ollama, LM Studio, vLLM) need no key
        local = any(h in self.endpoint for h in ("://127.0.0.1", "://localhost", "://[::1]"))
        return bool(self.model and self.endpoint and (self.api_key or local))

    def public(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "base_url": self.endpoint,
            "model": self.model,
            "has_key": bool(self.api_key),
            "enabled": self.enabled,
            "auto": self.auto,
            "acknowledged": self.acknowledged,
            "configured": self.configured,
            "from_env": self.from_env,
        }


def _guess_provider(base_url: str) -> str:
    return "anthropic" if "anthropic" in base_url.lower() else "openai"


def load_config() -> AiConfig:
    raw: dict[str, Any] = {}
    p = config_path()
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8")).get("ai", {}) or {}
        except (OSError, ValueError):
            raw = {}
    cfg = AiConfig(**{k: v for k, v in raw.items() if k in AiConfig.__dataclass_fields__ and k != "from_env"})
    for key, var in ENV.items():
        val = os.environ.get(var)
        if val:
            setattr(cfg, key, val)
            cfg.from_env.append(key)
    if "provider" not in cfg.from_env and "provider" not in raw and cfg.base_url:
        cfg.provider = _guess_provider(cfg.base_url)
    if cfg.provider not in PROVIDERS:
        cfg.provider = "openai"
    return cfg


def save_config(update: dict[str, Any]) -> AiConfig:
    """Merge `update` into the stored settings. An empty or missing api_key keeps the stored one."""
    p = config_path()
    doc: dict[str, Any] = {}
    if p.is_file():
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
    ai = dict(doc.get("ai") or {})
    for key in ("provider", "base_url", "model"):
        if isinstance(update.get(key), str):
            ai[key] = update[key].strip()
    if isinstance(update.get("api_key"), str) and update["api_key"].strip():
        ai["api_key"] = update["api_key"].strip()
    if update.get("clear_key"):
        ai.pop("api_key", None)
    for key in ("enabled", "auto", "acknowledged"):
        if isinstance(update.get(key), bool):
            ai[key] = update[key]
    if ai.get("provider") not in PROVIDERS:
        ai["provider"] = _guess_provider(ai.get("base_url", ""))
    doc["ai"] = ai
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    tmp.replace(p)
    return load_config()
