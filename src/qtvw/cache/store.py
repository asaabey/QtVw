"""One SQLite-owner thread, disposable disk data, and byte-bounded image caches."""

import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import RLock

from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QImage

SCHEMA_VERSION = 1
THUMBNAIL_VERSION = 1
INDEX_VERSION = 1
MAX_INDEX_BYTES = 32 * 1024 * 1024
MAX_THUMBNAIL_BYTES = 8 * 1024 * 1024


def default_cache_dir():
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    return Path(location) / "v1"


def thumbnail_key(entry, source_fingerprint, size, policy):
    data = [
        THUMBNAIL_VERSION,
        "cover-v1" if entry.kind == "archive" else "image",
        str(entry.path.absolute()),
        source_fingerprint,
        entry.member,
        entry.member_index,
        entry.crc,
        entry.size if entry.member is not None else None,
        size,
        policy,
    ]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def index_key(limits):
    return json.dumps([INDEX_VERSION, limits], sort_keys=True)


class ImageLRU:
    def __init__(self, limit):
        self.limit = limit
        self.bytes = 0
        self.items = OrderedDict()

    def get(self, key):
        if key not in self.items:
            return None
        self.items.move_to_end(key)
        return QImage(self.items[key])

    def put(self, key, image):
        old = self.items.pop(key, None)
        if old is not None:
            self.bytes -= old.sizeInBytes()
        if image.sizeInBytes() <= self.limit:
            self.items[key] = QImage(image)
            self.bytes += image.sizeInBytes()
        while self.bytes > self.limit:
            _, old = self.items.popitem(last=False)
            self.bytes -= old.sizeInBytes()

    def clear(self):
        self.items.clear()
        self.bytes = 0


class CacheStore:
    def __init__(
        self,
        root=None,
        thumbnail_memory=32 * 1024 * 1024,
        image_memory=128 * 1024 * 1024,
        disk_payload_limit=512 * 1024 * 1024,
        disk_limit=512 * 1024 * 1024,
    ):
        self.root = Path(root) if root is not None else default_cache_dir()
        self.disk_payload_limit = disk_payload_limit
        self.disk_limit = disk_limit
        self._actor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qtvw-cache")
        self._db = None
        self._closed = False
        self._lock = RLock()
        self._epoch = 0
        self._known_sources = OrderedDict()
        self.thumbnails = ImageLRU(thumbnail_memory)
        self.images = ImageLRU(image_memory)

    @property
    def epoch(self):
        with self._lock:
            return self._epoch

    def memory_get(self, key, full=False):
        with self._lock:
            return (self.images if full else self.thumbnails).get(key)

    def memory_put(self, key, image, epoch, full=False):
        with self._lock:
            if epoch == self._epoch:
                (self.images if full else self.thumbnails).put(key, image)

    def _connect(self):
        if self._db is not None:
            return self._db
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "thumbnails").mkdir(exist_ok=True)
        path = self.root / "index.sqlite3"
        for attempt in range(2):
            connection = sqlite3.connect(path, timeout=1)
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=NORMAL")
                if version != SCHEMA_VERSION:
                    connection.executescript(
                        "DROP TABLE IF EXISTS thumbnails; "
                        "DROP TABLE IF EXISTS archives; "
                        "DROP TABLE IF EXISTS sources;"
                    )
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS sources (
                        path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS thumbnails (
                        key TEXT PRIMARY KEY, source TEXT NOT NULL,
                        bytes INTEGER NOT NULL, accessed REAL NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS thumbnails_source ON thumbnails(source);
                    CREATE TABLE IF NOT EXISTS archives (
                        source TEXT NOT NULL, policy TEXT NOT NULL, data BLOB NOT NULL,
                        accessed REAL NOT NULL, PRIMARY KEY(source, policy)
                    );
                """)
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                connection.commit()
                self._cleanup(connection)
                self._db = connection
                return connection
            except (sqlite3.DatabaseError, OSError) as error:
                connection.close()
                corrupt = getattr(error, "sqlite_errorcode", None) in {11, 26}
                corrupt = corrupt or any(
                    message in str(error).lower()
                    for message in ("not a database", "database disk image is malformed")
                )
                if attempt or not corrupt:
                    raise
                logging.getLogger(__name__).warning("Rebuilding corrupt cache database: %s", path)
                for suffix in ("", "-wal", "-shm"):
                    Path(str(path) + suffix).unlink(missing_ok=True)

    def _call(self, operation, fallback=None):
        def run():
            try:
                return operation(self._connect())
            except (OSError, sqlite3.Error, ValueError) as error:
                logging.getLogger(__name__).warning("Cache fallback for %s: %s", self.root, error)
                self._rollback()
                return fallback

        with self._lock:
            if self._closed:
                return fallback
            future = self._actor.submit(run)
        return future.result()

    def _rollback(self):
        if self._db is not None:
            try:
                self._db.rollback()
            except sqlite3.Error:
                pass

    def validate_source(self, path, expected):
        name = str(path.absolute())
        with self._lock:
            if self._known_sources.get(name) == expected:
                self._known_sources.move_to_end(name)
                return True

        def validate(db):
            result = self._source(db, path, expected)
            db.commit()
            return result

        result = self._call(validate, fallback=True)
        if result:
            with self._lock:
                self._known_sources[name] = expected
                while len(self._known_sources) > 1024:
                    self._known_sources.popitem(last=False)
        return result

    def _source(self, db, path, expected):
        info = path.stat()
        if (info.st_size, info.st_mtime_ns) != tuple(expected):
            return False
        name = str(path.absolute())
        old = db.execute("SELECT size, mtime FROM sources WHERE path=?", (name,)).fetchone()
        if old is not None and old != tuple(expected):
            keys = db.execute("SELECT key FROM thumbnails WHERE source=?", (name,)).fetchall()
            db.execute("DELETE FROM thumbnails WHERE source=?", (name,))
            db.execute("DELETE FROM archives WHERE source=?", (name,))
            for (key,) in keys:
                try:
                    self._thumbnail_path(key).unlink(missing_ok=True)
                except ValueError:
                    pass
        if old != tuple(expected):
            db.execute("INSERT OR REPLACE INTO sources VALUES (?, ?, ?)", (name, *expected))
        return True

    def _thumbnail_path(self, key):
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("Invalid thumbnail key")
        return self.root / "thumbnails" / f"{key}.png"

    def thumbnail_get(self, key, path, expected):
        def read(db):
            if not self._source(db, path, expected):
                return None
            record = db.execute(
                "SELECT bytes, accessed FROM thumbnails WHERE key=?", (key,)
            ).fetchone()
            if record is None:
                db.commit()
                return None
            file = self._thumbnail_path(key)
            try:
                if file.stat().st_size != record[0] or record[0] > MAX_THUMBNAIL_BYTES:
                    raise ValueError("Damaged cache entry")
                data = file.read_bytes()
            except (OSError, ValueError):
                db.execute("DELETE FROM thumbnails WHERE key=?", (key,))
                file.unlink(missing_ok=True)
                db.commit()
                return None
            now = time.time()
            if now - record[1] > 30:
                db.execute("UPDATE thumbnails SET accessed=? WHERE key=?", (now, key))
            db.commit()
            return data

        return self._call(read)

    def thumbnail_put(self, key, path, expected, data, epoch):
        if len(data) > MAX_THUMBNAIL_BYTES:
            return

        def write(db):
            if epoch != self.epoch or not self._source(db, path, expected):
                return
            destination = self._thumbnail_path(key)
            descriptor, temporary = tempfile.mkstemp(prefix=".tmp-", dir=destination.parent)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                os.replace(temporary, destination)
                db.execute(
                    "INSERT OR REPLACE INTO thumbnails VALUES (?, ?, ?, ?)",
                    (key, str(path.absolute()), len(data), time.time()),
                )
                db.commit()
                self._evict(db)
            finally:
                Path(temporary).unlink(missing_ok=True)

        self._call(write)

    def thumbnail_drop(self, key):
        def drop(db):
            db.execute("DELETE FROM thumbnails WHERE key=?", (key,))
            self._thumbnail_path(key).unlink(missing_ok=True)
            db.commit()

        self._call(drop)

    def index_get(self, path, expected, policy):
        def read(db):
            if not self._source(db, path, expected):
                return None
            row = db.execute(
                "SELECT data, accessed FROM archives WHERE source=? AND policy=?",
                (str(path.absolute()), index_key(policy)),
            ).fetchone()
            if row and time.time() - row[1] > 30:
                db.execute(
                    "UPDATE archives SET accessed=? WHERE source=? AND policy=?",
                    (time.time(), str(path.absolute()), index_key(policy)),
                )
            db.commit()
            if row is None or len(row[0]) > MAX_INDEX_BYTES:
                return None
            return json.loads(row[0])

        return self._call(read)

    def index_put(self, path, expected, policy, records, epoch):
        data = json.dumps(records, separators=(",", ":"), ensure_ascii=False).encode()
        if len(data) > MAX_INDEX_BYTES:
            return

        def write(db):
            if epoch != self.epoch or not self._source(db, path, expected):
                return
            db.execute(
                "INSERT OR REPLACE INTO archives VALUES (?, ?, ?, ?)",
                (str(path.absolute()), index_key(policy), data, time.time()),
            )
            db.commit()
            self._evict(db)

        self._call(write)

    def _oldest(self, db):
        return db.execute("""
            SELECT 'thumbnail', key, '', bytes, accessed FROM thumbnails
            UNION ALL SELECT 'index', source, policy, length(data), accessed FROM archives
            ORDER BY 5
        """).fetchall()

    def _remove_record(self, db, row):
        kind, key, policy, cost, _ = row
        if kind == "thumbnail":
            db.execute("DELETE FROM thumbnails WHERE key=?", (key,))
            self._thumbnail_path(key).unlink(missing_ok=True)
        else:
            db.execute("DELETE FROM archives WHERE source=? AND policy=?", (key, policy))
        return cost

    def _evict(self, db):
        size = db.execute("SELECT COALESCE(SUM(bytes),0) FROM thumbnails").fetchone()[0]
        size += db.execute("SELECT COALESCE(SUM(length(data)),0) FROM archives").fetchone()[0]
        if size > self.disk_payload_limit:
            for row in self._oldest(db):
                size -= self._remove_record(db, row)
                if size <= self.disk_payload_limit:
                    break
        db.commit()
        thumbnail_bytes = db.execute("SELECT COALESCE(SUM(bytes),0) FROM thumbnails").fetchone()[0]
        if thumbnail_bytes + self._database_bytes() > self.disk_limit:
            self._cleanup(db)

    def _database_bytes(self):
        return sum(
            path.stat().st_size for path in self.root.glob("index.sqlite3*") if path.is_file()
        )

    def _disk_bytes(self):
        return self._database_bytes() + sum(
            path.stat().st_size for path in (self.root / "thumbnails").iterdir() if path.is_file()
        )

    def _compact(self, db):
        db.commit()
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        if db.execute("PRAGMA freelist_count").fetchone()[0]:
            db.execute("VACUUM")
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()

    def _prune_sources(self, db):
        db.execute("""DELETE FROM sources WHERE path NOT IN
                   (SELECT source FROM thumbnails UNION SELECT source FROM archives)""")

    def _cleanup(self, db):
        known = set()
        for (key,) in db.execute("SELECT key FROM thumbnails").fetchall():
            try:
                file = self._thumbnail_path(key)
                if file.is_file():
                    known.add(file.name)
                else:
                    db.execute("DELETE FROM thumbnails WHERE key=?", (key,))
            except ValueError:
                db.execute("DELETE FROM thumbnails WHERE key=?", (key,))
        cutoff = time.time() - 300
        for path in (self.root / "thumbnails").iterdir():
            managed = path.suffix == ".png" or path.name.startswith(".tmp-")
            if managed and path.name not in known and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        self._prune_sources(db)
        self._compact(db)
        usage = self._disk_bytes()
        while usage > self.disk_limit:
            rows = self._oldest(db)
            if not rows:
                break
            target = usage - self.disk_limit
            freed = 0
            for row in rows:
                freed += self._remove_record(db, row)
                if freed >= target:
                    break
            self._prune_sources(db)
            self._compact(db)
            usage = self._disk_bytes()
        return {
            "ok": usage <= self.disk_limit,
            "bytes": usage,
            "limit": self.disk_limit,
            "message": ""
            if usage <= self.disk_limit
            else "Cache overhead or recent files exceed the limit; retry cleanup later.",
        }

    def _maintenance(self, clear=False, limit=None):
        try:
            db = self._connect()
            if limit is not None:
                self.disk_limit = self.disk_payload_limit = max(0, int(limit))
            if clear:
                db.executescript(
                    "DELETE FROM thumbnails; DELETE FROM archives; DELETE FROM sources;"
                )
                for path in (self.root / "thumbnails").iterdir():
                    if path.suffix == ".png" or path.name.startswith(".tmp-"):
                        path.unlink(missing_ok=True)
            return self._cleanup(db)
        except (OSError, sqlite3.Error, ValueError) as error:
            logging.getLogger(__name__).warning("Cache maintenance failed: %s", error)
            self._rollback()
            return {"ok": False, "message": str(error)}

    def maintain_async(self, limit=None):
        with self._lock:
            if self._closed:
                raise RuntimeError("Cache is closed")
            return self._actor.submit(self._maintenance, False, limit)

    def clear_async(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("Cache is closed")
            self._epoch += 1
            self.images.clear()
            self.thumbnails.clear()
            self._known_sources.clear()
            return self._actor.submit(self._maintenance, True)

    def clear(self):
        return self.clear_async().result()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True

        def finish():
            if self._db is not None:
                self._db.close()
                self._db = None

        self._actor.submit(finish).result()
        self._actor.shutdown(wait=True)
