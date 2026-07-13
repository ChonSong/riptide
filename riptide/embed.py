"""
embed.py — Ollama embeddings for Riptide.

Wraps the proven logic from pr-review/scripts/embed.py.
Handles chunking (nomic-embed-text has ~2048 token context = ~1500 chars safe cap).
"""
import os, sys, json, time, math
from typing import List
import numpy as np
import requests

OLLAMA_BASE  = os.environ.get("OLLAMA_BASE_URL",     "http://localhost:43311")
EMBED_MODEL  = os.environ.get("OLLAMA_EMBED_MODEL",  "nomic-embed-text")
CHUNK_SIZE   = int(os.environ.get("EMBED_CHUNK_SIZE", "1500"))
OVERLAP      = 100


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = OVERLAP) -> List[str]:
    """Split text into overlapping chunks, preferring code-structure boundaries."""
    if not text or len(text) <= chunk_size:
        return [text] if text else []
    separators = ["\n\n", "\n", "## ", "// ", "/* ", "class ", "def ", "function ", "; ", "}"]
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        for sep in separators:
            split_pt = text.rfind(sep, start + chunk_size // 2, end)
            if split_pt > start:
                end = split_pt + len(sep)
                break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap if end < len(text) else end
    return chunks


def embed_texts(texts: List[str], model: str = EMBED_MODEL) -> List[List[float]]:
    """
    Embed a list of text chunks via Ollama /api/embed.
    Returns list of embedding vectors (768-dim for nomic-embed-text).
    """
    if not texts:
        return []
    vectors = []
    for i, chunk in enumerate(texts):
        if not chunk.strip():
            vectors.append([])
            continue
        for attempt in range(3):
            try:
                resp = requests.post(
                    f"{OLLAMA_BASE}/api/embed",
                    json={"model": model, "input": chunk},
                    timeout=60,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    emb = data.get("embeddings", [[]])
                    if isinstance(emb, list) and len(emb) > 0:
                        vectors.append(emb[0] if isinstance(emb[0], list) else emb)
                    else:
                        vectors.append([])
                    break
                elif resp.status_code == 400:
                    # Chunk too long — split and average sub-embeddings
                    sub = chunk_text(chunk, chunk_size=CHUNK_SIZE // 2, overlap=50)
                    sub_vecs = embed_texts(sub, model)
                    valid = [v for v in sub_vecs if v]
                    vectors.append(np.mean(valid, axis=0).tolist() if valid else [])
                    break
                else:
                    time.sleep(2 ** attempt)
            except requests.exceptions.Timeout:
                time.sleep(2 ** attempt)
            except Exception:
                time.sleep(1)
        else:
            vectors.append([])
    return vectors


def embed_query(text: str) -> List[float]:
    """Embed a single query string (returns zero vector on failure)."""
    result = embed_texts([text])
    return result[0] if result else [0.0] * 768
