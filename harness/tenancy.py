"""Tenant isolation primitives. A leaf module (stdlib only) so every store can import it.

Contract:
- A tenant id is a lowercase slug (`[a-z0-9][a-z0-9_-]{0,47}`). Anything else is rejected, so a tenant
  id can never traverse a path or break a collection name.
- The `default` tenant is the operator tenant. It keeps the legacy, unprefixed locations (`kb/`,
  `outputs/skills/`, `kb_live`), so single-tenant installs and the benchmark path are unchanged.
- Every other tenant gets its own directory (`<base>/tenants/<slug>`) and collection (`<name>__<slug>`).
- `use(tenant)` binds the tenant for the current thread of execution (a context variable). It is set
  and reset around synchronous work only, never across a generator `yield`.
- Built-in agents are shared. A custom agent belongs to the tenant stamped in its metadata; an
  unstamped (legacy) custom agent belongs to `default`.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import re
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

DEFAULT_TENANT = "default"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")

_current: contextvars.ContextVar[str] = contextvars.ContextVar("orion_tenant", default=DEFAULT_TENANT)


class TenantError(ValueError):
    """The tenant id is not a valid slug."""


def slug(tenant: Optional[str]) -> str:
    """The validated tenant id (empty or None means `default`). Raises `TenantError` otherwise."""
    value = (tenant or DEFAULT_TENANT).strip()
    if not _SLUG.fullmatch(value):
        raise TenantError(f"invalid tenant id {value[:60]!r}: use 1-48 chars of a-z, 0-9, '_' or '-'")
    return value


def is_valid(tenant: Optional[str]) -> bool:
    try:
        slug(tenant)
    except TenantError:
        return False
    return True


def current() -> str:
    return _current.get()


@contextlib.contextmanager
def use(tenant: Optional[str]) -> Iterator[str]:
    """Bind `tenant` for the duration of the block (validated; restores the previous binding)."""
    value = slug(tenant)
    token = _current.set(value)
    try:
        yield value
    finally:
        _current.reset(token)


def bound(fn: Callable[..., Any], tenant: Optional[str]) -> Callable[..., Any]:
    """Wrap `fn` so it runs with `tenant` bound. Use for thread targets: new threads start unbound."""
    value = slug(tenant)

    @functools.wraps(fn)
    def run(*args: Any, **kwargs: Any) -> Any:
        with use(value):
            return fn(*args, **kwargs)

    return run


def of(ctx: Any) -> str:
    """The tenant of a `RequestContext` (`default` for eval-mode contexts)."""
    extra = getattr(ctx, "extra", None) or {}
    return slug(extra.get("tenant") or DEFAULT_TENANT)


def scoped_dir(base: Path, tenant: Optional[str] = None) -> Path:
    """`base` for the default tenant, `base/tenants/<slug>` for any other."""
    value = slug(tenant if tenant is not None else current())
    return base if value == DEFAULT_TENANT else base / "tenants" / value


def collection(name: str, tenant: Optional[str] = None) -> str:
    """Vector-store collection name: unchanged for `default`, suffixed for any other tenant."""
    value = slug(tenant if tenant is not None else current())
    return name if value == DEFAULT_TENANT else f"{name}__{value}"


def is_platform(tenant: Optional[str]) -> bool:
    """The operator tenant. Its administrators manage deployment-wide resources (MCP, connectors, other tenants)."""
    return (tenant or DEFAULT_TENANT) == DEFAULT_TENANT


def card_owner(card: Any) -> str:
    """Owning tenant of a custom agent card (`default` when unstamped)."""
    meta = getattr(card, "metadata", None) or {}
    return str(meta.get("tenant") or DEFAULT_TENANT)


def card_visible(card: Any, tenant: Optional[str]) -> bool:
    """Built-in agents are shared; a custom agent is visible only to its owning tenant."""
    if getattr(card, "is_builtin", False) or not getattr(card, "is_custom", False):
        return True
    return card_owner(card) == (tenant or DEFAULT_TENANT)
