"""
Remote file store: local cache + remote backend.

Each RemoteFileStore instance binds to one StorageBackend + cache_dir.
`key` (str) is both the local relative path (under cache_dir) and the
remote blob key (in the bucket).

  - Local:  cache_dir / key
  - Remote: bucket / key
  - Sync:   cache_dir / .sync / {key}.sync

Sync file format:
  local=<sha256> remote=<sha256>

Usage:
    from dubora_core.utils.file_store import get_gcs_store, get_tos_store

    gcs = get_gcs_store()
    gcs.write("dramas/xxx/0.jpg", content)
    gcs.write_file(Path("/tmp/out.mp4"), "dramas/xxx/dub/5-dubbed.mp4")
    gcs.upload("dramas/xxx/dub/5-dubbed.mp4")
    local = gcs.get("dramas/xxx/11.mp4")
    url = gcs.get_url("dramas/xxx/0.jpg")
"""

import hashlib
import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from dubora_core.utils.logger import info as _log_info, warning as _log_warn


# ── Utilities ────────────────────────────────────────────────────────────────


def sha256_file(path: Path) -> str:
    """Compute SHA256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ── SyncInfo ─────────────────────────────────────────────────────────────────


@dataclass
class SyncInfo:
    local_sha: str = ""
    remote_sha: str = ""


# ── Storage backend interface ────────────────────────────────────────────────


class StorageBackend(ABC):
    """Remote storage backend interface."""

    @abstractmethod
    def upload(self, local_path: Path, key: str) -> None:
        """Upload local file to remote. Raises on failure."""

    @abstractmethod
    def download(self, key: str, local_path: Path) -> None:
        """Download remote file to local path. Raises on failure."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if key exists in remote storage."""

    @abstractmethod
    def get_url(self, key: str, expires: int) -> str:
        """Generate a presigned GET URL."""


# ── GCS backend ──────────────────────────────────────────────────────────────


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


class GCSBackend(StorageBackend):
    def upload(self, local_path: Path, key: str) -> None:
        _gcs_bucket().blob(key).upload_from_filename(str(local_path))

    def download(self, key: str, local_path: Path) -> None:
        _gcs_bucket().blob(key).download_to_filename(str(local_path))

    def exists(self, key: str) -> bool:
        return _gcs_bucket().blob(key).exists()

    def get_url(self, key: str, expires: int = 3600) -> str:
        return _gcs_bucket().blob(key).generate_signed_url(
            expiration=timedelta(seconds=expires),
        )


# ── TOS backend ──────────────────────────────────────────────────────────────


class TOSBackend(StorageBackend):
    def __init__(self):
        self._client = None
        self._bucket: str = ""
        self._tos = None

    def _ensure_client(self):
        if self._client is not None:
            return
        try:
            import tos
        except ImportError as e:
            raise RuntimeError("Missing dependency: pip install tos") from e

        ak = os.getenv("TOS_ACCESS_KEY_ID", "")
        sk = os.getenv("TOS_SECRET_ACCESS_KEY", "")
        if not ak or not sk:
            raise ValueError("TOS_ACCESS_KEY_ID and TOS_SECRET_ACCESS_KEY required")

        region = os.getenv("TOS_REGION", "cn-beijing")
        endpoint = os.getenv("TOS_ENDPOINT", f"tos-cn-{region}.volces.com")
        endpoint = endpoint.replace("https://", "").replace("http://", "")
        self._bucket = os.getenv("TOS_BUCKET", "pikppo-video")
        self._client = tos.TosClientV2(ak, sk, endpoint, region)
        self._tos = tos

    def upload(self, local_path: Path, key: str) -> None:
        self._ensure_client()
        self._client.upload_file(self._bucket, key, str(local_path))

    def download(self, key: str, local_path: Path) -> None:
        self._ensure_client()
        self._client.get_object_to_file(self._bucket, key, str(local_path))

    def exists(self, key: str) -> bool:
        self._ensure_client()
        try:
            self._client.head_object(self._bucket, key)
            return True
        except self._tos.exceptions.TosServerError as e:
            if e.status_code == 404:
                return False
            raise

    def get_url(self, key: str, expires: int = 3600) -> str:
        self._ensure_client()
        out = self._client.pre_signed_url(
            self._tos.enum.HttpMethodType.Http_Method_Get,
            self._bucket,
            key,
            expires=expires,
        )
        return out.signed_url


# ── RemoteFileStore ──────────────────────────────────────────────────────────


class RemoteFileStore:
    """File store: local cache_dir + remote backend, key-unified API.

    `key` (str) serves as both:
      - Local path: cache_dir / key
      - Remote blob key: bucket / key
    """

    def __init__(self, backend: StorageBackend, cache_dir: Path, *, name: str = ""):
        self.backend = backend
        self.cache_dir = cache_dir
        self.name = name or type(backend).__name__

    # ── Internal ──────────────────────────────────────────────

    def _local_path(self, key: str) -> Path:
        """key -> local cache path."""
        return self.cache_dir / key

    def _sync_path(self, key: str) -> Path:
        """key -> .sync file path."""
        return self.cache_dir / ".sync" / f"{key}.sync"

    def _calc_sha256(self, key: str) -> str:
        """Compute SHA256 of local cached file."""
        return sha256_file(self._local_path(key))

    def _read_sync(self, key: str) -> SyncInfo:
        """Read .sync file. Returns empty SyncInfo if missing/corrupt."""
        sp = self._sync_path(key)
        if not sp.is_file():
            return SyncInfo()
        try:
            kv = {}
            for token in sp.read_text().strip().split():
                k, _, v = token.partition("=")
                kv[k] = v
            return SyncInfo(
                local_sha=kv.get("local", ""),
                remote_sha=kv.get("remote", ""),
            )
        except Exception:
            return SyncInfo()

    def _write_sync(self, key: str, info: SyncInfo) -> None:
        """Write .sync file."""
        sp = self._sync_path(key)
        sp.parent.mkdir(parents=True, exist_ok=True)
        parts = [f"local={info.local_sha or '-'}"]
        if info.remote_sha:
            parts.append(f"remote={info.remote_sha}")
        sp.write_text(" ".join(parts))

    def _update_sync(self, key: str, sync_remote: bool = False) -> None:
        """Recompute local SHA and update .sync. sync_remote=True marks remote as synced."""
        sha = self._calc_sha256(key)
        if sync_remote:
            remote = sha
        else:
            # Preserve remote_sha if content unchanged (avoid redundant re-upload)
            existing = self._read_sync(key)
            remote = existing.remote_sha if existing.remote_sha == sha else ""
        self._write_sync(key, SyncInfo(local_sha=sha, remote_sha=remote))

    # ── Public API ────────────────────────────────────────────

    def write(self, key: str, data: bytes, upload: bool = True) -> int:
        """Write in-memory data to local cache, optionally upload.

        Local write failure raises OSError. Upload failure raises backend exception.

        Args:
            key: Blob key (= relative path under cache_dir).
            data: File content bytes.
            upload: If True, upload to remote after writing.

        Returns:
            0=local ok, not uploaded. 1=local ok, uploaded. 2=local ok, skipped (synced).

        Raises:
            OSError: Local write failed (disk full, permission denied, etc.).
            Exception: Upload failed (network error, auth error, etc.).
        """
        local = self._local_path(key)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
        self._update_sync(key)
        if upload:
            return self.upload(key)
        return 0

    def write_file(self, src: Path, key: str, upload: bool = True) -> int:
        """Copy an external file into local cache, optionally upload.

        Local copy failure raises OSError. Upload failure raises backend exception.

        Args:
            src: Source file path (outside cache_dir).
            key: Blob key (= relative path under cache_dir).
            upload: If True, upload to remote after copying.

        Returns:
            0=local ok, not uploaded. 1=local ok, uploaded. 2=local ok, skipped (synced).

        Raises:
            OSError: Local copy failed (src missing, disk full, permission denied, etc.).
            Exception: Upload failed (network error, auth error, etc.).
        """
        local = self._local_path(key)
        local.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, local)
        self._update_sync(key)
        if upload:
            return self.upload(key)
        return 0

    def upload(self, key: str) -> int:
        """Upload local cached file to remote if not already synced.

        Returns:
            1=uploaded. 2=skipped (already synced).

        Raises:
            FileNotFoundError: Local file missing.
            Exception: Backend upload failed (network error, auth error, etc.).
        """
        local = self._local_path(key)
        if not local.is_file():
            raise FileNotFoundError(f"Upload failed: local file missing: {local}")

        sync = self._read_sync(key)
        if not sync.local_sha:
            sync.local_sha = self._calc_sha256(key)
            self._write_sync(key, sync)
        sha_short = sync.local_sha[:8]

        if sync.local_sha and sync.remote_sha == sync.local_sha:
            _log_info(f"Upload skipped (synced, sha={sha_short}): store={self.name} key={key}")
            return 2

        _log_info(f"Uploading (sha={sha_short}): store={self.name} key={key}")
        self.backend.upload(local, key)
        self._write_sync(key, SyncInfo(local_sha=sync.local_sha, remote_sha=sync.local_sha))
        _log_info(f"Upload done: store={self.name} key={key}")
        return 1

    def get(self, key: str) -> Path | None:
        """Ensure local cache is up-to-date, return local path.

        Returns None if file doesn't exist locally or remotely.

        Raises:
            Exception: Backend error (network/auth failure during exists check or download).
        """
        local = self._local_path(key)

        # Fast path: trust .sync
        if local.is_file():
            sync = self._read_sync(key)
            if sync.local_sha and sync.remote_sha == sync.local_sha:
                return local

        if not self.backend.exists(key):
            _log_info(f"Get skipped (not found remote): store={self.name} key={key}")
            return local if local.is_file() else None

        _log_info(f"Downloading: store={self.name} key={key}")
        local.parent.mkdir(parents=True, exist_ok=True)
        self.backend.download(key, local)
        self._update_sync(key, sync_remote=True)
        sha_short = self._read_sync(key).local_sha[:8]
        _log_info(f"Download done (sha={sha_short}): store={self.name} key={key}")
        return local

    def get_local_sha(self, key: str) -> str:
        """Return local SHA256 from .sync file. Empty string if unknown."""
        return self._read_sync(key).local_sha

    def delete(self, key: str) -> None:
        """Delete local cached file + .sync."""
        self._local_path(key).unlink(missing_ok=True)
        self._sync_path(key).unlink(missing_ok=True)

    def get_url(self, key: str, expires: int = 3600) -> str:
        """Generate a presigned remote URL for the key."""
        return self.backend.get_url(key, expires=expires)


# ── Concrete subclasses ──────────────────────────────────────────────────────


class GcsFileStore(RemoteFileStore):
    def __init__(self, cache_dir: Path):
        super().__init__(backend=GCSBackend(), cache_dir=cache_dir, name="gcs")


class TosFileStore(RemoteFileStore):
    def __init__(self, cache_dir: Path):
        super().__init__(backend=TOSBackend(), cache_dir=cache_dir, name="tos")


# ── Lazy singletons ──────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def get_gcs_store() -> GcsFileStore:
    from dubora_core.config.settings import get_data_root
    return GcsFileStore(cache_dir=get_data_root() / "gcs")


@lru_cache(maxsize=1)
def get_tos_store() -> TosFileStore:
    from dubora_core.config.settings import get_data_root
    return TosFileStore(cache_dir=get_data_root() / "tos")
