"""Embedding-space helpers. No play-data / EDHREC.

Color identity is stored as 0/1 bits on Chroma metadata so hybrid search can
filter at query time (card colors ⊆ commander identity) instead of fetching
400 hits and dropping them in Python.
"""

from __future__ import annotations

import json
import os

import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "data")
VIEWS_PATH = os.path.join(DATA_DIR, "card_views.npz")

COLOR_BITS = (("W", "ci_w"), ("U", "ci_u"), ("B", "ci_b"), ("R", "ci_r"), ("G", "ci_g"))

# Name is not a view: MiniLM clusters proper names, not mechanics (Kumano vs Krenko).
ORACLE_WEIGHT = 0.7
TYPE_WEIGHT = 0.3


def cosine(a, b) -> float:
    va = np.asarray(a, dtype=float).ravel()
    vb = np.asarray(b, dtype=float).ravel()
    na = np.linalg.norm(va)
    nb = np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def view_texts(info: dict) -> dict[str, str]:
    oracle = (info.get("oracle_text") or "").strip() or "Vanilla creature / No abilities."
    type_line = (info.get("type_line") or "").strip() or "Unknown type"
    return {"oracle": oracle, "type": type_line}


def multi_view_cosine(cmd_views: dict, card_views: dict) -> float:
    """Weighted mean of per-view cosines (oracle, type). Not a fused vector."""
    return (
        ORACLE_WEIGHT * cosine(cmd_views["oracle"], card_views["oracle"])
        + TYPE_WEIGHT * cosine(cmd_views["type"], card_views["type"])
    )


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


def save_card_views(ids: list[str], oracle, type_vecs, path: str = VIEWS_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.savez_compressed(
        path,
        ids=np.asarray(ids, dtype=object),
        oracle=np.asarray(oracle, dtype=np.float16),
        type=np.asarray(type_vecs, dtype=np.float16),
    )


def load_card_views(path: str = VIEWS_PATH) -> dict | None:
    if not os.path.exists(path):
        return None
    data = np.load(path, allow_pickle=True)
    ids = [str(i) for i in data["ids"]]
    return {
        "index": {card_id: i for i, card_id in enumerate(ids)},
        "oracle": data["oracle"],
        "type": data["type"],
    }
