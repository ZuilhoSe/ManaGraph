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

# Name is not a view. Oracle is the largest weight; keywords and mana cost are smaller than type.
ORACLE_WEIGHT = 0.55
TYPE_WEIGHT = 0.25
KEYWORD_WEIGHT = 0.12
MANA_WEIGHT = 0.08
VIEW_WEIGHTS = {
    "oracle": ORACLE_WEIGHT,
    "type": TYPE_WEIGHT,
    "keywords": KEYWORD_WEIGHT,
    "mana": MANA_WEIGHT,
}


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
    raw_kw = info.get("keywords")
    if isinstance(raw_kw, str):
        try:
            raw_kw = json.loads(raw_kw)
        except json.JSONDecodeError:
            raw_kw = [raw_kw] if raw_kw.strip() else []
    if isinstance(raw_kw, list) and raw_kw:
        kw_text = ", ".join(str(k) for k in raw_kw)
    else:
        kw_text = "No keywords."
    mana = (info.get("mana_cost") or "").strip() or "No mana cost."
    return {"oracle": oracle, "type": type_line, "keywords": kw_text, "mana": mana}


def multi_view_cosine(cmd_views: dict, card_views: dict) -> float:
    """Weighted mean of shared views. Missing views (old npz) are dropped and weights renormalized."""
    keys = [k for k in VIEW_WEIGHTS if k in cmd_views and k in card_views]
    if not keys:
        return 0.0
    weight_sum = sum(VIEW_WEIGHTS[k] for k in keys)
    return sum(VIEW_WEIGHTS[k] * cosine(cmd_views[k], card_views[k]) for k in keys) / weight_sum


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


def save_card_views(
    ids: list[str],
    oracle,
    type_vecs,
    path: str = VIEWS_PATH,
    keywords=None,
    mana=None,
):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "ids": np.asarray(ids, dtype=object),
        "oracle": np.asarray(oracle, dtype=np.float16),
        "type": np.asarray(type_vecs, dtype=np.float16),
    }
    if keywords is not None:
        payload["keywords"] = np.asarray(keywords, dtype=np.float16)
    if mana is not None:
        payload["mana"] = np.asarray(mana, dtype=np.float16)
    np.savez_compressed(path, **payload)


def load_card_views(path: str = VIEWS_PATH) -> dict | None:
    if not os.path.exists(path):
        return None
    data = np.load(path, allow_pickle=True)
    ids = [str(i) for i in data["ids"]]
    store = {
        "index": {card_id: i for i, card_id in enumerate(ids)},
        "oracle": data["oracle"],
        "type": data["type"],
    }
    for key in ("keywords", "mana"):
        if key in data.files:
            store[key] = data[key]
    return store
