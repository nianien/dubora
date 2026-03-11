"""
Package resources: emotions.json, voices.json, schema.sql, seed.sql.

Access via importlib.resources:
    from dubora_core.resources import get_resource_path
    path = get_resource_path("emotions.json")
"""
from importlib import resources
from pathlib import Path


def get_resource_path(name: str) -> Path:
    """Return the filesystem path for a bundled resource file."""
    ref = resources.files(__package__).joinpath(name)
    # resources.files returns a Traversable; as_posix() works for on-disk packages
    return Path(str(ref))
