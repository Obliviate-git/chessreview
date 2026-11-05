from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import keyring


def data_dir() -> Path:
    override = os.environ.get("CHESSREVIEW_DATA_DIR")
    path = Path(override) if override else Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ChessReview"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Settings:
    provider: str = "codex"
    model: str = "gpt-6-luna"
    engine: str = ""
    codex: str = ""
    api_profile: str = "自定义"
    api_profiles: dict = field(default_factory=lambda: {
        name: {"base_url": "", "model": ""} for name in ("千问", "GLM", "Kimi", "DeepSeek", "自定义")
    })

    @classmethod
    def load(cls) -> Settings:
        path = data_dir() / "settings.json"
        if not path.exists():
            return cls()
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
            return cls(**{key: value for key, value in content.items() if key in cls.__dataclass_fields__})
        except (ValueError, TypeError):
            return cls()

    def save(self) -> None:
        (data_dir() / "settings.json").write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    def get_key(self, profile: str | None = None) -> str:
        return keyring.get_password("ChessReview", profile or self.api_profile) or ""

    def set_key(self, profile: str, key: str) -> None:
        if key:
            keyring.set_password("ChessReview", profile, key)

    def endpoint(self) -> str:
        return self.api_profiles.get(self.api_profile, {}).get("base_url", "")

    def active_model(self) -> str:
        if self.provider == "codex":
            return self.model
        return self.api_profiles.get(self.api_profile, {}).get("model", "")

    def provider_id(self) -> str:
        return "codex" if self.provider == "codex" else "api:" + self.api_profile
