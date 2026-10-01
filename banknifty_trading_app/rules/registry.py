"""Name -> rule-class registries.

New rule = one class + one ``register_*`` call. Nothing else changes.
"""

from __future__ import annotations

from typing import TypeVar

from .base import EntryRule, ExpiryRule, Filter, PartialRule, StopRule, TrailingRule

_R = TypeVar("_R")

FILTERS: dict[str, type[Filter]] = {}
ENTRIES: dict[str, type[EntryRule]] = {}
STOPS: dict[str, type[StopRule]] = {}
PARTIALS: dict[str, type[PartialRule]] = {}
TRAILINGS: dict[str, type[TrailingRule]] = {}
EXPIRIES: dict[str, type[ExpiryRule]] = {}


def _register(registry: dict, name: str):
    def decorator(cls):
        if name in registry:
            raise ValueError(f"duplicate rule registration: {name}")
        registry[name] = cls
        cls.rule_name = name
        return cls

    return decorator


def register_filter(name: str):
    return _register(FILTERS, name)


def register_entry(name: str):
    return _register(ENTRIES, name)


def register_stop(name: str):
    return _register(STOPS, name)


def register_partial(name: str):
    return _register(PARTIALS, name)


def register_trailing(name: str):
    return _register(TRAILINGS, name)


def register_expiry(name: str):
    return _register(EXPIRIES, name)


def build(registry: dict[str, type], cfg: dict | None, *, default_none: bool = False):
    """Instantiate a rule from a config dict; ``None``/missing means disabled."""
    if not cfg:
        if default_none:
            return None
        raise ValueError("rule config is required")
    cfg = dict(cfg)
    name = cfg.pop("type", None)
    if name is None:
        raise ValueError(f"rule config missing 'type': {cfg!r}")
    if name not in registry:
        raise KeyError(
            f"unknown rule type {name!r}; available: {sorted(registry)}"
        )
    return registry[name].from_config(cfg)
