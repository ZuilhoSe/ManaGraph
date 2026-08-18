"""Embedding-space helpers. No play-data / EDHREC.

Color identity is stored as 0/1 bits on Chroma metadata so hybrid search can
filter at query time (card colors ⊆ commander identity) instead of fetching
400 hits and dropping them in Python.
"""

from __future__ import annotations

import json

import numpy as np

COLOR_BITS = (("W", "ci_w"), ("U", "ci_u"), ("B", "ci_b"), ("R", "ci_r"), ("G", "ci_g"))


def cosine(a, b) -> float:
    va = np.asarray(a, dtype=float).ravel()
    vb = np.asarray(b, dtype=float).ravel()
    na = np.linalg.norm(va)
    nb = np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def chroma_metadata(name: str, color_identity, cmc, type_line: str) -> dict:
    if isinstance(color_identity, str):
        colors = json.loads(color_identity or "[]")
        color_json = color_identity
    else:
        colors = list(color_identity or [])
        color_json = json.dumps(colors)
    meta = {
        "name": name,
        "color_identity": color_json,
        "cmc": float(cmc or 0),
        "is_creature": 1 if "creature" in (type_line or "").lower() else 0,
    }
    color_set = set(colors)
    for code, key in COLOR_BITS:
        meta[key] = 1 if code in color_set else 0
    return meta


def identity_where(allowed_colors: list[str] | None) -> dict | None:
    """Chroma `where`: every color *not* in the commander identity must be 0."""
    allowed = set(allowed_colors or [])
    clauses = [{key: 0} for code, key in COLOR_BITS if code not in allowed]
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def knn_indices(vectors: np.ndarray, k: int = 8) -> np.ndarray:
    """Row i → indices of k nearest others by cosine (excludes self)."""
    x = np.asarray(vectors, dtype=float)
    if x.ndim != 2 or len(x) == 0:
        return np.zeros((0, 0), dtype=int)
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    unit = x / norms
    sim = unit @ unit.T
    np.fill_diagonal(sim, -np.inf)
    k = min(max(int(k), 0), max(len(x) - 1, 0))
    if k == 0:
        return np.zeros((len(x), 0), dtype=int)
    return np.argpartition(-sim, kth=k - 1, axis=1)[:, :k]
