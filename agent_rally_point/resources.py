#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Resource id normalization for Rally ownership claims and blockers."""
from __future__ import annotations

import os
from pathlib import Path


def normalize_file_resource(path: str, *, workdir: Path) -> str:
    """Return a canonical ``file:`` resource id for ``path``.

    Relative paths are normalized to POSIX-looking repo-relative strings.
    Absolute paths under ``workdir`` are converted to repo-relative strings;
    absolute paths outside ``workdir`` remain absolute POSIX strings so they do
    not collide with repo-local resources accidentally.
    """
    p = Path(path).expanduser()
    if p.is_absolute():
        resolved = p.resolve(strict=False)
        try:
            normalized = resolved.relative_to(workdir).as_posix()
        except ValueError:
            normalized = resolved.as_posix()
    else:
        normalized = os.path.normpath(path).replace(os.sep, "/")
    if normalized == ".":
        normalized = ""
    return f"file:{normalized}"


def resource_from_values(
    *,
    resource: str | None,
    path: str | None,
    workdir: Path,
    required: bool = True,
) -> str | None:
    """Return canonical resource string from ``--resource`` or ``--path`` values."""
    if resource and path:
        raise ValueError("use either --resource or --path, not both")
    if resource:
        return resource
    if path:
        return normalize_file_resource(path, workdir=workdir)
    if required:
        raise ValueError("one of --resource or --path is required")
    return None
