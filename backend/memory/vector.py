"""向量检索（SPEC 10.5）。

1. 每个世界一个 collection：world_{world_id}
2. 查询强制 where={"world_id": world_id}
3. 跨世界检索需显式 cross_world=true
4. embedding 调用失败 → 降级为纯图检索
5. 向量维度变更 → 整库重建

优先使用 Chroma（本地嵌入式）；未安装时自动降级为本地哈希嵌入 + JSON 存储，
接口保持一致，保证在无网络/无重型依赖环境下仍可用。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import config, db

DIM = 256

try:  # pragma: no cover
    import chromadb  # type: ignore

    _HAS_CHROMA = True
except Exception:  # pragma: no cover
    chromadb = None  # type: ignore
    _HAS_CHROMA = False

_client = None


def backend_name() -> str:
    return "chroma" if _HAS_CHROMA else "local-hash"


def _store_dir() -> Path:
    return config.vector_path()


def _collection_name(world_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(world_id))
    return f"world_{safe}"


def _client_instance():
    global _client
    if not _HAS_CHROMA:
        return None
    if _client is None:
        _client = chromadb.PersistentClient(path=str(_store_dir()))
    return _client


# --------------------------------------------------------------- 嵌入


def _hash_embedding(text: str) -> List[float]:
    """确定性哈希嵌入：字符 bigram 映射到固定维度。"""
    vec = [0.0] * DIM
    tokens = [text[i : i + 2] for i in range(max(1, len(text) - 1))]
    for token in tokens:
        digest = hashlib.md5(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:2], "big") % DIM
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _remote_embedding(texts: List[str]) -> Optional[List[List[float]]]:
    """尝试调用 OpenAI 兼容 /v1/embeddings；失败则返回 None（降级）。"""
    api_key = config.secret("GATEWAY_API_KEY") or config.secret("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        import httpx  # type: ignore

        endpoint = config.get("llm.extractor.endpoint", "http://localhost:8000/v1")
        base = str(endpoint).rstrip("/")
        if base.endswith("/v1"):
            url = f"{base}/embeddings"
        else:
            url = f"{base}/v1/embeddings"
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": config.get("vector.embedding_model", "text-embedding-3-small"), "input": texts},
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return [item["embedding"] for item in data.get("data", [])]
    except Exception:
        return None


def embed(texts: List[str]) -> List[List[float]]:
    remote = _remote_embedding(texts)
    if remote and len(remote) == len(texts):
        return remote
    return [_hash_embedding(t) for t in texts]


# --------------------------------------------------------------- 本地降级存储


def _local_path(world_id: str) -> Path:
    return _store_dir() / f"{_collection_name(world_id)}.json"


def _local_load(world_id: str) -> List[Dict[str, Any]]:
    path = _local_path(world_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []


def _local_save(world_id: str, items: List[Dict[str, Any]]) -> None:
    _local_path(world_id).write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


# --------------------------------------------------------------- 公共 API


def add(world_id: str, doc_id: str, text: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
    """写入一条记忆（向量只做补充，不替代台账）。"""
    meta = {"world_id": world_id, "text": text[:2000], **(metadata or {})}
    vectors = embed([text])
    if _HAS_CHROMA:
        try:
            client = _client_instance()
            collection = client.get_or_create_collection(_collection_name(world_id))
            collection.upsert(ids=[doc_id], documents=[text], metadatas=[meta], embeddings=vectors)
            return True
        except Exception:
            pass
    items = [item for item in _local_load(world_id) if item["id"] != doc_id]
    items.append({"id": doc_id, "text": text, "metadata": meta, "vector": vectors[0]})
    _local_save(world_id, items)
    return True


def search(world_id: str, query: str, top_k: int = 3, cross_world: bool = False) -> List[Dict[str, Any]]:
    """查询强制按世界过滤；跨世界需显式开关。"""
    if not query:
        return []
    if _HAS_CHROMA:
        try:
            client = _client_instance()
            collection = client.get_or_create_collection(_collection_name(world_id))
            kwargs: Dict[str, Any] = {"query_embeddings": embed([query]), "n_results": max(1, top_k)}
            if not cross_world:
                kwargs["where"] = {"world_id": world_id}
            result = collection.query(**kwargs)
            out = []
            ids = (result.get("ids") or [[]])[0]
            docs = (result.get("documents") or [[]])[0]
            metas = (result.get("metadatas") or [[]])[0]
            dists = (result.get("distances") or [[]])[0]
            for i, doc_id in enumerate(ids):
                distance = dists[i] if i < len(dists) else 1.0
                out.append(
                    {
                        "id": doc_id,
                        "text": docs[i] if i < len(docs) else "",
                        "score": round(max(0.0, 1.0 - float(distance)), 4),
                        "metadata": metas[i] if i < len(metas) else {},
                        "backend": "chroma",
                    }
                )
            return out
        except Exception:
            pass  # 降级为纯图检索

    qvec = embed([query])[0]
    scored = []
    for item in _local_load(world_id):
        meta = item.get("metadata") or {}
        if not cross_world and meta.get("world_id") not in (None, world_id):
            continue
        scored.append(
            {
                "id": item["id"],
                "text": item.get("text", ""),
                "score": round(_cosine(qvec, item.get("vector") or []), 4),
                "metadata": meta,
                "backend": "local-hash",
            }
        )
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[: max(1, top_k)]


def search_text(world_id: str, anchors: List[str], top_k: int = 3) -> str:
    """【向量补充】块；检索失败返回空串（不阻塞注入）。"""
    query = " ".join([a for a in anchors if a])
    if not query:
        return ""
    try:
        results = search(world_id, query, top_k=top_k)
    except Exception:
        return ""
    threshold = float(config.get("memory.graph.score_threshold.vector", 0.6))
    hits = [r for r in results if r["score"] >= threshold and r["text"]]
    if not hits:
        return ""
    lines = [f"【向量补充】（backend={backend_name()}）"]
    for hit in hits:
        lines.append(f"- {hit['text'][:120]}（相似度 {hit['score']:.2f}）")
    return "\n".join(lines)


def rebuild(world_id: str) -> Dict[str, Any]:
    """向量维度/模型变更 → 整库重建。"""
    items = _local_load(world_id)
    for item in items:
        item["vector"] = embed([item.get("text", "")])[0]
    _local_save(world_id, items)
    if _HAS_CHROMA:
        try:
            client = _client_instance()
            client.delete_collection(_collection_name(world_id))
            collection = client.get_or_create_collection(_collection_name(world_id))
            if items:
                collection.upsert(
                    ids=[i["id"] for i in items],
                    documents=[i.get("text", "") for i in items],
                    metadatas=[i.get("metadata") or {} for i in items],
                    embeddings=[i["vector"] for i in items],
                )
        except Exception:
            pass
    return {"ok": True, "world_id": world_id, "count": len(items), "backend": backend_name()}


def backfill_from_events(world_id: str, limit: int = 500) -> int:
    """把 committed 事件灌进向量库（供检索补充）。"""
    rows = db.query(
        "SELECT id, content FROM events WHERE world_id=? AND state='committed' ORDER BY id DESC LIMIT ?",
        (world_id, limit),
    )
    for row in rows:
        add(world_id, f"event:{row['id']}", row["content"], {"type": "event", "event_id": row["id"]})
    return len(rows)