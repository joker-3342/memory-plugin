"""OpenAI 兼容网关（SPEC 4.19 / 4.20）。

路由规则：
  model 以 "memory-" 开头 → 本地记忆引擎
  model 以 "gpt-" 开头    → OpenCode Go
  其他                    → 透传默认上游
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Body

from .. import config
from ..core import injector
from ..memory import vector
from ..models import InjectRequest
from . import opencode

router = APIRouter(tags=["gateway"])


def _routes() -> List[Dict[str, Any]]:
    return list(config.get("gateway.routes", []))


def resolve_upstream(model: str) -> str:
    for route in _routes():
        prefix = (route.get("match") or {}).get("model_prefix")
        if prefix and model.startswith(prefix):
            return route.get("upstream", "")
    return "local-memory" if model.startswith("memory-") else "opencode-go"


def _last_user_message(messages: List[Dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _local_memory_completion(payload: Dict[str, Any]) -> Dict[str, Any]:
    """memory-* 模型：返回记忆引擎的注入块。"""
    metadata = payload.get("metadata") or {}
    world_id = metadata.get("world_id") or payload.get("world_id") or "default"
    turn = int(metadata.get("turn") or payload.get("turn") or 0)
    scene = metadata.get("scene") or {"loc": None, "chars": [], "story_time": None}
    anchors = metadata.get("anchors") or [a for a in str(_last_user_message(payload.get("messages", []))).split() if a][:8]

    request = InjectRequest(
        turn=turn,
        world_id=world_id,
        scene=scene,
        anchors=[str(a) for a in anchors][:12],
        recent_summary=metadata.get("recent_summary"),
        budget=metadata.get("budget"),
        scene_type=metadata.get("scene_type", "present"),
        speaking=metadata.get("speaking", []),
    )
    result = injector.build(request)
    return {
        "id": f"chatcmpl-memory-{result['trace_id']}",
        "object": "chat.completion",
        "model": payload.get("model", "memory-engine"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result["inject_text"]},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": result["token_count"], "completion_tokens": 0, "total_tokens": result["token_count"]},
        "memory_debug": result["debug"],
        "latency_ms": result["latency_ms"],
    }


@router.post("/v1/chat/completions")
def chat_completions(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    model = str(payload.get("model", ""))
    upstream = resolve_upstream(model)

    if upstream == "local-memory":
        return _local_memory_completion(payload)

    if not opencode.enabled():
        return {"error": {"message": "gateway_disabled", "type": "gateway_error"}}

    result = opencode.chat_completion(payload, upstream)
    if "error" in result:
        # 上游不可用 → 不阻塞，返回可读错误对象
        return result
    return result


@router.post("/v1/responses")
def responses(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    model = str(payload.get("model", ""))
    upstream = resolve_upstream(model)
    if upstream == "local-memory":
        mapped = {
            "model": model,
            "messages": [{"role": "user", "content": payload.get("input", "")}],
            "metadata": payload.get("metadata", {}),
        }
        return _local_memory_completion(mapped)
    return opencode.responses(payload, upstream)


@router.post("/v1/embeddings")
def embeddings(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """OpenAI 兼容 embeddings（本地哈希嵌入兜底，无需外网）。"""
    raw = payload.get("input")
    texts = raw if isinstance(raw, list) else [str(raw or "")]
    vectors = vector.embed([str(t) for t in texts])
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": vec} for i, vec in enumerate(vectors)
        ],
        "model": payload.get("model", config.get("vector.embedding_model", "local-hash")),
        "usage": {"prompt_tokens": sum(len(t) for t in texts), "total_tokens": sum(len(t) for t in texts)},
        "backend": vector.backend_name(),
    }


@router.get("/v1/models")
def models() -> Dict[str, Any]:
    entries = [
        {"id": "memory-engine", "object": "model", "owned_by": "memory-plugin"},
        {"id": "memory-inject", "object": "model", "owned_by": "memory-plugin"},
    ]
    for upstream in opencode.upstreams():
        entries.append({"id": f"{upstream.get('name')}-default", "object": "model", "owned_by": str(upstream.get("name"))})
    return {"object": "list", "data": entries}