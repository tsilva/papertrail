from importlib import import_module

_EXPORTS = {
    "CanonicalRegistry": ("papertrail.repository", "CanonicalRegistry"),
    "DocumentEngine": ("papertrail.engine", "DocumentEngine"),
    "DocumentRepository": ("papertrail.repository", "DocumentRepository"),
    "RuleEngine": ("papertrail.rules", "RuleEngine"),
    "Runtime": ("papertrail.runtime", "Runtime"),
    "RuntimePaths": ("papertrail.runtime", "RuntimePaths"),
    "UpsertResult": ("papertrail.engine", "UpsertResult"),
    "create_runtime": ("papertrail.runtime", "create_runtime"),
    "runtime_from_profile": ("papertrail.runtime", "runtime_from_profile"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *__all__))
