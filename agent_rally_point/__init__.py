# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
# build-loop@tyroneross:canary:agent-rally-point
# canary-end
"""Agent Rally Point — local-first coordination point for coding agents.

Public API surface. Most users should reach for the CLI (`agent-rally-point ...`)
shipped in v0.2; this Python API is for advanced adapter authors building
integrations into in-process runtimes (LangGraph nodes, CrewAI tools,
AutoGen tool-callable wrappers, etc.).

Submodules are imported lazily via PEP 562 module-level ``__getattr__`` so that
``python -m agent_rally_point.<submodule>`` does not race the package
``__init__`` (which previously caused a ``RuntimeWarning`` from runpy because
the submodule was already in ``sys.modules`` before runpy executed it as
``__main__``). Consumer-facing imports (``from agent_rally_point import
discover``) behave identically to the previous eager-import layout.
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

__version__ = "0.4.0"

# Map: public attribute name -> (submodule, source attribute name)
# Source attribute = the name in the submodule that the public attribute resolves to.
# When source attribute is None, the public attribute IS the submodule itself.
_LAZY_ATTRS: dict[str, tuple[str, str | None]] = {
    # channel identity
    "app_slug": ("channel_paths", "app_slug"),
    "app_channel_dir": ("channel_paths", "app_channel_dir"),
    "ensure_channel_dir": ("channel_paths", "ensure_channel_dir"),
    "apps_root": ("channel_paths", "apps_root"),
    "DEFAULT_APPS_ROOT": ("channel_paths", "DEFAULT_APPS_ROOT"),
    # presence (heartbeat)
    "write_presence": ("presence", "write_presence"),
    "read_active_presence": ("presence", "read_active_presence"),
    "run_refresh_loop": ("presence", "run_refresh_loop"),
    # append-only event log
    "append_change": ("changes", "append_change"),
    "make_record": ("changes", "make_record"),
    "new_event_id": ("changes", "new_event_id"),
    "new_thread_id": ("changes", "new_thread_id"),
    "source_for_tool": ("changes", "source_for_tool"),
    "event_type_for_kind": ("changes", "event_type_for_kind"),
    "dataschema_for_type": ("changes", "dataschema_for_type"),
    # monotonic revision counter
    "bump_revision": ("revision", "bump_revision"),
    # delta-computing reader
    "checkpoint_read": ("checkpoint", "checkpoint_read"),
    # canonical write helper
    "post": ("post", "post"),
    # lifecycle hygiene (re-exported as the submodule itself)
    "lifecycle": ("lifecycle", None),
    # discovery (v0.2: manifest + resolver)
    "discover": ("discover", "discover"),
    # repo identity (v0.3: worktree-stable, clone-stable repo_id)
    "repo_id": ("repo_id", "repo_id"),
    # migration tool (v0.3: legacy -> canonical, cutover verifier)
    "migrate": ("migrate", None),
}

__all__ = [
    "__version__",
    *_LAZY_ATTRS.keys(),
]


def __getattr__(name: str):  # PEP 562
    if name in _LAZY_ATTRS:
        submodule_name, source_attr = _LAZY_ATTRS[name]
        module = importlib.import_module(f".{submodule_name}", __name__)
        value = module if source_attr is None else getattr(module, source_attr)
        globals()[name] = value  # cache so subsequent lookups skip __getattr__
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(list(globals().keys()) + __all__))


if TYPE_CHECKING:
    # Re-state for type checkers (which don't execute __getattr__).
    from .channel_paths import (  # noqa: F401
        app_slug,
        app_channel_dir,
        ensure_channel_dir,
        apps_root,
        DEFAULT_APPS_ROOT,
    )
    from .presence import write_presence, read_active_presence  # noqa: F401
    from .changes import (  # noqa: F401
        append_change,
        make_record,
        new_event_id,
        new_thread_id,
        source_for_tool,
        event_type_for_kind,
        dataschema_for_type,
    )
    from .revision import bump_revision  # noqa: F401
    from .checkpoint import checkpoint_read  # noqa: F401
    from .post import post  # noqa: F401
    from . import lifecycle  # noqa: F401
    from .discover import discover  # noqa: F401
