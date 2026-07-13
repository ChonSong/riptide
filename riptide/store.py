"""
store.py — NumPy vector store for Riptide.

Pure numpy (float32 blobs) + SQLite metadata. No sqlite-vec extension needed.
Uses scipy.spatial.distance.cosine for similarity search.

Schema:
  chunks: id, path, chunk_text, chunk_hash, vec_bytes, indexed_at

Usage:
  init_store(db_path)         — create tables
  upsert_chunks(db_path, path, chunks_and_vecs)  — add/update file chunks
  remove_path(db_path, path)  — remove a file's chunks
  search(db_path, vec, top_k) — cosine similarity top-K
  get_stats(db_path)          — chunk / repo stats
"""
import os, sqlite3, json, hashlib, struct
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Tuple
import numpy as np
from scipy.spatial.distance import cosine as scipy_cosine

OLLAMA_DIM = int(os.environ.get("OLLAMA_EMBED_DIM", "768"))


# ── Serialisation ────────────────────────────────────────────────────────────

def vec_to_bytes(vec: list) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()

def bytes_to_vec(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.float32)


# ── Init ─────────────────────────────────────────────────────────────────────

def init_store(db_path: str):
    """Create tables if they don't exist."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            path       TEXT NOT NULL,
            chunk_text TEXT,
            chunk_hash TEXT,
            vec_bytes  BLOB NOT NULL,
            indexed_at TEXT NOT NULL,
            UNIQUE(path, chunk_hash)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_path ON chunks(path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hash ON chunks(chunk_hash)")
    conn.commit()
    conn.close()


# ── Upsert ────────────────────────────────────────────────────────────────────

def upsert_chunks(db_path: str, file_path: str, chunks_and_vecs: List[Tuple[str, list]]):
    """
    Upsert a list of (chunk_text, vector) for a given file.
    Replaces all existing chunks for that file first.
    """
    if not chunks_and_vecs:
        return
    conn = sqlite3.connect(db_path)
    # Remove old chunks for this file
    conn.execute("DELETE FROM chunks WHERE path = ?", (file_path,))
    now = datetime.now(timezone.utc).isoformat()
    for chunk_text, vec in chunks_and_vecs:
        chunk_hash = hashlib.sha256(chunk_text.encode()).hexdigest()[:16]
        vec_bytes = vec_to_bytes(vec)
        conn.execute("""
            INSERT OR IGNORE INTO chunks (path, chunk_text, chunk_hash, vec_bytes, indexed_at)
            VALUES (?, ?, ?, ?, ?)
        """, (file_path, chunk_text[:10000], chunk_hash, vec_bytes, now))
    conn.commit()
    conn.close()


# ── Remove ───────────────────────────────────────────────────────────────────

def remove_path(db_path: str, file_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM chunks WHERE path = ?", (file_path,))
    conn.commit()
    conn.close()


# ── Search ────────────────────────────────────────────────────────────────────

def search(db_path: str, query_vec: list, top_k: int = 8) -> List[Tuple[str, str, float]]:
    """
    Cosine-similarity top-K search over all chunks.
    Returns [(chunk_text, path, score), ...] sorted by similarity (highest first).
    Score = 1 - cosine_distance, so 1.0 = perfect match.
    """
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT id, chunk_text, path, vec_bytes FROM chunks").fetchall()
    conn.close()

    if not rows:
        return []

    q = np.asarray(query_vec, dtype=np.float32)
    q_norm = np.linalg.norm(q)
    if q_norm == 0:
        return []

    results = []
    for row in rows:
        chunk_id, chunk_text, path, vec_bytes = row
        v = bytes_to_vec(vec_bytes)
        v_norm = np.linalg.norm(v)
        if v_norm == 0:
            continue
        # Cosine similarity = 1 - cosine distance
        sim = 1 - scipy_cosine(q, v)
        results.append((chunk_text, path, float(sim)))

    results.sort(key=lambda x: x[2], reverse=True)
    return results[:top_k]


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_stats(db_path: str) -> dict:
    """Return chunk count and unique path count."""
    conn = sqlite3.connect(db_path)
    cur = conn.execute("SELECT COUNT(*), COUNT(DISTINCT path) FROM chunks")
    count, unique = cur.fetchone()
    conn.close()
    return {"chunks": count, "unique_paths": unique}
