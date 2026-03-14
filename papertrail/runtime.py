"""Application runtime container and initialization."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from papertrail.config import (
    ConfigError,
    ProfileLoader,
    ProfileNotFoundError,
    ProfileSettings,
    build_openai_client,
    check_api_accessibility,
    get_cache_dir,
)
from papertrail.console import PapertrailConsole
from papertrail.hashing import HashCache
from papertrail.logging_utils import get_logger, setup_logging
from papertrail.nif_lookup import NIFLookupCache

logger = get_logger("runtime")


@dataclass(frozen=True)
class RuntimePaths:
    raw: list[Path]
    processed: Path | None
    export: Path | None
    cache: Path
    profiles: Path


@dataclass
class Runtime:
    """Runtime resources shared by repository, engine, and commands."""

    profile: ProfileSettings
    profile_name: str
    paths: RuntimePaths
    model_id: str | None
    openai_client: Any
    nif_cache: NIFLookupCache | None
    hash_cache: HashCache
    console: PapertrailConsole
    api_accessible: bool
    now: Callable[[], datetime] = field(default=datetime.now, repr=False)

    @property
    def today(self) -> str:
        return self.now().strftime("%Y-%m-%d")

    def require_processed_path(self) -> Path:
        if self.paths.processed is None:
            raise RuntimeError("paths.processed is not configured")
        self.paths.processed.mkdir(parents=True, exist_ok=True)
        return self.paths.processed

    def require_export_path(self) -> Path:
        if self.paths.export is None:
            raise RuntimeError("paths.export is not configured")
        self.paths.export.mkdir(parents=True, exist_ok=True)
        return self.paths.export


def create_runtime(
    profile_name: str | None = None,
    *,
    verbose: bool = False,
    enable_client: bool = True,
    probe_api: bool = True,
) -> Runtime:
    """Create the canonical runtime from a named profile."""

    setup_logging(verbose=verbose)

    loader = ProfileLoader()
    available = loader.list_available_profiles()
    if not available:
        raise ConfigError(
            "No profiles found in ~/.config/papertrail/profiles/. "
            "Copy profiles/profile.yaml.example to "
            "~/.config/papertrail/profiles/<name>/profile.yaml, configure it, "
            "and pass --profile <name>."
        )

    if profile_name is None:
        raise ConfigError(
            f"A profile is required. Use --profile <name>. Available profiles: {', '.join(available)}."
        )

    profile = loader.load_profile(profile_name)
    return runtime_from_profile(
        profile,
        profile_name=profile_name,
        profiles_dir=loader.profiles_dir,
        verbose=verbose,
        enable_client=enable_client,
        probe_api=probe_api,
    )


def runtime_from_profile(
    profile: ProfileSettings,
    *,
    profile_name: str | None = None,
    profiles_dir: Path | None = None,
    verbose: bool = False,
    enable_client: bool = True,
    probe_api: bool = True,
) -> Runtime:
    """Build a runtime from an already-loaded profile object."""

    setup_logging(verbose=verbose)

    console = PapertrailConsole()
    api_accessible = False
    if enable_client:
        api_accessible = (
            check_api_accessibility(profile.openrouter.base_url)
            if probe_api
            else bool(profile.openrouter.api_key)
        )
        if not api_accessible:
            console.warning(
                f"API base URL is not accessible: {profile.openrouter.base_url}",
                indent=False,
            )
            console.warning(
                "LLM-dependent tasks will fail. Offline tasks (rename, check, export) will work.",
                indent=False,
            )

    cache_dir = get_cache_dir()
    nif_cache = None
    if profile.nif_api.enabled:
        nif_cache = NIFLookupCache(cache_dir / "nif_cache.yaml")

    runtime = Runtime(
        profile=profile,
        profile_name=profile_name or profile.profile.name,
        paths=RuntimePaths(
            raw=[Path(path) for path in profile.paths.raw],
            processed=Path(profile.paths.processed) if profile.paths.processed else None,
            export=Path(profile.paths.export) if profile.paths.export else None,
            cache=cache_dir,
            profiles=profiles_dir or profile.profile_dir or Path.home() / ".config" / "papertrail" / "profiles",
        ),
        model_id=profile.openrouter.model_id,
        openai_client=build_openai_client(profile) if enable_client and api_accessible else None,
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
    return runtime


__all__ = ["Runtime", "RuntimePaths", "create_runtime", "runtime_from_profile"]
