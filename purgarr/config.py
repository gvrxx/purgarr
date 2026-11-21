import os
from dataclasses import dataclass


class ConfigError(Exception):
    pass


def _ids(name: str) -> frozenset[int]:
    raw = os.environ.get(name, "")
    try:
        return frozenset(int(part) for part in raw.replace(" ", "").split(",") if part)
    except ValueError:
        raise ConfigError(f"{name} must be a comma-separated list of Discord IDs, got {raw!r}")


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    discord_token: str
    admin_channel_id: int
    admin_role_ids: frozenset[int]
    requester_role_ids: frozenset[int]
    radarr_url: str
    radarr_api_key: str
    sonarr_url: str
    sonarr_api_key: str
    add_import_exclusion: bool
    dry_run: bool
    db_path: str

    @classmethod
    def from_env(cls) -> "Config":
        token = os.environ.get("DISCORD_TOKEN", "").strip()
        if not token:
            raise ConfigError("DISCORD_TOKEN is required")

        channel = os.environ.get("ADMIN_CHANNEL_ID", "").strip()
        if not channel.isdigit():
            raise ConfigError("ADMIN_CHANNEL_ID is required (right-click the channel > Copy Channel ID)")

        config = cls(
            discord_token=token,
            admin_channel_id=int(channel),
            admin_role_ids=_ids("ADMIN_ROLE_IDS"),
            requester_role_ids=_ids("REQUESTER_ROLE_IDS"),
            radarr_url=os.environ.get("RADARR_URL", "").strip().rstrip("/"),
            radarr_api_key=os.environ.get("RADARR_API_KEY", "").strip(),
            sonarr_url=os.environ.get("SONARR_URL", "").strip().rstrip("/"),
            sonarr_api_key=os.environ.get("SONARR_API_KEY", "").strip(),
            add_import_exclusion=_bool("ADD_IMPORT_EXCLUSION", False),
            dry_run=_bool("DRY_RUN", False),
            db_path=os.environ.get("DB_PATH", "/config/purgarr.db"),
        )

        if not (config.radarr_url and config.radarr_api_key) and not (config.sonarr_url and config.sonarr_api_key):
            raise ConfigError("Set RADARR_URL + RADARR_API_KEY and/or SONARR_URL + SONARR_API_KEY")
        return config
