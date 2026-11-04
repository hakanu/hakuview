"""Small human-readable formatting helpers shared across HakuView modules."""

from __future__ import annotations


def human_size(num_bytes: int) -> str:
    """Format a byte count as e.g. '512 B', '3.4 MB', '1.2 TB'."""
    val = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if val < 1024.0:
            return f"{int(val)} B" if unit == "B" else f"{val:.1f} {unit}"
        val /= 1024.0
    return f"{val:.1f} TB"
