"""Application runtime container and initialization."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from papertrail.config import (
    AppContext,
    Config,
    ConfigError,
    ProfileLoader,
    ProfileNotFoundError,
    check_api_accessibility,
    get_cache_dir,
    get_openai_client,
    set_ctx,
    set_current_profile,
)
from papertrail.console import PapertrailConsole, set_console
from papertrail.hashing import HashCache
from papertrail.logging_utils import get_logger, setup_logging
from papertrail.models import reset_enum_cache, reset_session_cache
from papertrail.nif_lookup import NIFLookupCache

logger = get_logger("app")


@dataclass(frozen=True)
class AppPaths:
    """Resolved application paths."""

    raw: list[Path]
    processed: Optional[Path]
    export: Optional[Path]
    cache: Path
    profiles: Path


@dataclass
class App:
    """Runtime application context shared by services and tasks."""

    profile: Config
    profile_name: str
    paths: AppPaths
    model_id: str
    openai_client: Any
    nif_cache: Optional[NIFLookupCache]
    hash_cache: HashCache
    console: PapertrailConsole
    api_accessible: bool
    now: Callable[[], datetime] = field(default=datetime.now, repr=False)

    @property
    def today(self) -> str:
        return self.now().strftime("%Y-%m-%d")

    def require_processed_path(self) -> Path:
        if not self.paths.processed:
            raise RuntimeError("paths.processed is not configured")
        self.paths.processed.mkdir(parents=True, exist_ok=True)
        return self.paths.processed

    def require_export_path(self) -> Path:
        if not self.paths.export:
            raise RuntimeError("paths.export is not configured")
        self.paths.export.mkdir(parents=True, exist_ok=True)
        return self.paths.export


_current_app: App | None = None


def set_app(app: App | None) -> None:
    """Set the current app for compatibility helpers."""
    global _current_app
    _current_app = app
    if app is not None:
        set_current_profile(app.profile)
        set_ctx(
            AppContext(
                model_id=app.model_id,
                openai_client=app.openai_client,
                nif_cache=app.nif_cache,
            )
        )
        set_console(app.console)


def get_app() -> App:
    """Return the active app."""
    if _current_app is None:
        raise RuntimeError("Application runtime not initialized. Call create_app() first.")
    return _current_app


def create_app(profile_name: str | None = None, verbose: bool = False) -> App:
    """Create and install the application runtime."""
    setup_logging(verbose=verbose)

    loader = ProfileLoader()
    available = loader.list_available_profiles()
    if not available:
        raise ConfigError(
            "No profiles found in ~/.config/papertrail/profiles/. "
            "Copy profiles/profile.yaml.example to "
            "~/.config/papertrail/profiles/default/profile.yaml and configure it."
        )

    if profile_name is None:
        if "default" not in available:
            raise ConfigError(
                f"No 'default' profile found. Available profiles: {', '.join(available)}. "
                "Use --profile to specify one."
            )
        profile_name = "default"

    profile = loader.load_profile(profile_name)
    set_current_profile(profile)
    reset_enum_cache()
    reset_session_cache()

    console = PapertrailConsole()
    api_accessible = check_api_accessibility(profile.openrouter.base_url)
    if not api_accessible:
        console.warning(f"API base URL is not accessible: {profile.openrouter.base_url}", indent=False)
        console.warning(
            "LLM-dependent tasks will fail. Offline tasks (rename, check, export) will work.",
            indent=False,
        )

    cache_dir = get_cache_dir()
    nif_cache = None
    if profile.nif_api.enabled:
        nif_cache = NIFLookupCache(cache_dir / "nif_cache.yaml")

    app = App(
        profile=profile,
        profile_name=profile.profile.name,
        paths=AppPaths(
            raw=[Path(p) for p in profile.paths.raw or []],
            processed=Path(profile.paths.processed) if profile.paths.processed else None,
            export=Path(profile.paths.export) if profile.paths.export else None,
            cache=cache_dir,
            profiles=loader.profiles_dir,
        ),
        model_id=profile.openrouter.model_id,
        openai_client=get_openai_client() if api_accessible else None,
        nif_cache=nif_cache,
        hash_cache=HashCache(cache_dir / "hash_cache.yaml"),
        console=console,
        api_accessible=api_accessible,
    )

    logger.info(f"Using profile: {profile.profile.name}")
    if profile.profile.description:
        logger.info(f"  {profile.profile.description}")
    if nif_cache is not None:
        logger.info(f"NIF lookup enabled with {len(nif_cache)} cached entries")

    set_app(app)
    return app
