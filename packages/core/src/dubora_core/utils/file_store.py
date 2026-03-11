"""
File access layer: local-first with GCS signed URL fallback.

Any code needing a servable URL for a cloud-stored file should go through
FileStore.get_url(blob_path).  The store checks registered local directories
first; only when no local copy exists does it generate a GCS signed URL
(cached in memory to avoid redundant calls).

Usage:
    store = FileStore()
    store.add_local("/data/videos", "/api/media")
    url = store.get_url("东北雀神风云/0.jpg")
    # → "/api/media/东北雀神风云/0.jpg"  (if file exists locally)
    # → "https://storage.googleapis.com/..."  (GCS fallback)
"""

import logging
import os
import time
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_SIGNED_URL_EXPIRY = timedelta(hours=1)
_CACHE_MARGIN = 300  # refresh 5 min before real expiry


@lru_cache(maxsize=1)
def _gcs_bucket():
    """Lazy-init GCS bucket client (singleton)."""
    from google.cloud import storage
    from dubora_core.config.settings import resolve_relative_path

    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    if creds_path:
        resolved = str(resolve_relative_path(creds_path))
        client = storage.Client.from_service_account_json(resolved)
    else:
        client = storage.Client()
    bucket_name = os.getenv("GCS_BUCKET", "dubora")
    return client.bucket(bucket_name)


class FileStore:
    """File access layer: local-first, GCS fallback, in-memory URL cache."""

    def __init__(self):
        self._local_roots: list[tuple[Path, str]] = []
        self._gcs_cache_dir: Path | None = None
        # blob_path → (signed_url, expire_timestamp)
        self._url_cache: dict[str, tuple[str, float]] = {}

    # ── Configuration ────────────────────────────────────────

    def add_local(self, local_dir: Path, url_prefix: str):
        """Register a local directory as a file root.

        Args:
            local_dir:   Absolute path to a local directory.
            url_prefix:  URL prefix used to serve files from this directory
                         (e.g. "/api/media").
        """
        self._local_roots.append((Path(local_dir).resolve(), url_prefix.rstrip("/")))

    def set_gcs_cache_dir(self, cache_dir: Path):
        """Set the local directory for caching GCS downloads."""
        self._gcs_cache_dir = Path(cache_dir).resolve()

    # ── Public API ───────────────────────────────────────────

    def get_url(self, blob_path: str) -> str | None:
        """Return a servable URL for *blob_path*.

        1. Scan local roots — if ``local_dir / blob_path`` exists on disk,
           return ``url_prefix/blob_path`` immediately (zero network cost).
        2. Check GCS cache directory for locally cached downloads.
        3. Otherwise generate a GCS signed URL (cached in memory until near
           expiry).
        4. Return ``None`` on empty input or GCS failure.
        """
        if not blob_path:
            return None

        # 1) Local check
        for local_dir, url_prefix in self._local_roots:
            if (local_dir / blob_path).is_file():
                return f"{url_prefix}/{blob_path}"

        # 2) GCS cache directory check
        if self._gcs_cache_dir:
            cached_file = self._gcs_cache_dir / blob_path
            if cached_file.is_file():
                return f"/api/media/{blob_path}"

        # 3) In-memory signed-URL cache
        now = time.time()
        cached = self._url_cache.get(blob_path)
        if cached and cached[1] - _CACHE_MARGIN > now:
            return cached[0]

        # 4) GCS signed URL
        try:
            blob = _gcs_bucket().blob(blob_path)
            url = blob.generate_signed_url(expiration=_SIGNED_URL_EXPIRY)
            self._url_cache[blob_path] = (url, now + _SIGNED_URL_EXPIRY.total_seconds())
            return url
        except Exception as e:
            logger.warning("GCS signed URL failed for %s: %s", blob_path, e)
            return None

    def invalidate(self, blob_path: str):
        """Remove *blob_path* from the in-memory URL cache."""
        self._url_cache.pop(blob_path, None)
