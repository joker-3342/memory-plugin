"""OpenCode Go 适配（SPEC 12.9）。

1. 自动生成并复用 x-opencode-session 头
2. Base URL: https://opencode.ai/zen/go/v1
3. 支持 /v1/chat/completions 和 /v1/responses
4. 多 Key 轮询和故障转移
5. 协议转换：OpenAI ↔ Anthropic
"""

from __future__ import annotations

import itertools
import os
import threading
import uuid
from typing import Any, Dict, List, Optional

from .. import config

_lock = threading.Lock()
_session_cache: Dict[str, str] = {}
_key_cursor = itertools.count()


def enabled() -> bool:
    return bool(config.get("gateway.enabled", True))


def upstreams() -> List[Dict[str, Any]]:
    return list(config.get("gateway.upstreams", []))


def find_upstream(name: str) -> Optional[Dict[str, Any]]:
    for item in upstreams():
        if item.get("name") == name:
            return item
    return None


def keys_for(upstream: Dict[str, Any]) -> List[str]:
    """从环境变量读多 Key（支持 API_KEY、API_KEY_2 ... 命名）。"""
    env_name = upstream.get("api_key_env")
    if not env_name:
        return []
    keys = []
    primary = os.getenv(env_name)
    if primary:
        keys.append(primary)
    index = 2
    while True:
        extra = os.getenv(f"{env_name}_{index}")
        if not extra:
            break
        keys.append(extra)
        index += 1
    # 兼容逗号分隔
    if primary and "," in primary:
        keys = [k.strip() for k in primary.split(",") if k.strip()]
    return keys


def next_key(upstream: Dict[str, Any]) -> Optional[str]:
    """多 Key 轮询。"""
    keys = keys_for(upstream)
    if not keys:
        return None
    with _lock:
        index = next(_key_cursor) % len(keys)
    return keys[index]


def session_header(upstream_name: str) -> str:
    """自动生成并复用 x-opencode-session。"""
    with _lock:
        session = _session_cache.get(upstream_name)
        if session is None:
            session = f"oc-{uuid.uuid4().hex[:16]}"
            _session_cache[upstream_name] = session
        return session


def reset_session(upstream_name: Optional[str] = None) -> None:
    with _lock:
        if upstream_name:
            _session_cache.pop(upstream_name, None)
        else:
            _session_cache.clear()


def build_headers(upstream: Dict[str, Any], api_key: Optional[str]) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    for key, value in (upstream.get("headers") or {}).items():
        if value == "auto":
            headers[key] = session_header(upstream.get("name", "default"))
        else:
            headers[key] = str(value)
    return headers


def endpoint_for(upstream: Dict[str, Any], path: str) -> str:
    base = str(upstream.get("base_url", "")).rstrip("/")
    return f"{base}{path}"


# --------------------------------------------------------------- 协议转换


def to_anthropic(messages: List[Dict[str, Any]], model: str, max_tokens: int = 1024) -> Dict[str, Any]:
    """OpenAI chat → Anthropic messages。"""
    system_parts = [m.get("content", "") for m in messages if m.get("role") == "system"]
    converted = [
        {"role": "assistant" if m.get("role") == "assistant" else "user", "content": m.get("content", "")}
        for m in messages
        if m.get("role") != "system"
    ]
    payload: Dict[str, Any] = {"model": model, "messages": converted, "max_tokens": max_tokens}
    if system_parts:
        payload["system"] = "\n".join(system_parts)
    return payload


def from_anthropic(data: Dict[str, Any], model: str) -> Dict[str, Any]:
    """Anthropic response → OpenAI chat.completion。"""
    content = ""
    for block in data.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            content += block.get("text", "")
    return {
        "id": data.get("id", "chatcmpl-anthropic"),
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": data.get("usage", {}),
    }


# --------------------------------------------------------------- 调用


def chat_completion(payload: Dict[str, Any], upstream_name: str) -> Dict[str, Any]:
    """调用上游；多 Key 故障转移。"""
    upstream = find_upstream(upstream_name)
    if upstream is None:
        return {"error": {"message": f"upstream_not_found:{upstream_name}", "type": "gateway_error"}}

    try:
        import httpx  # type: ignore
    except Exception:
        return {"error": {"message": "httpx_not_installed", "type": "gateway_error"}}

    keys = keys_for(upstream) or [None]
    last_error = ""
    timeout = float(config.get("gateway.timeout_s", 60))
    model = str(payload.get("model", ""))

    for api_key in keys:
        headers = build_headers(upstream, api_key)
        url = endpoint_for(upstream, "/chat/completions")
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, headers=headers, json=payload)
                if response.status_code >= 400:
                    last_error = f"{response.status_code}:{response.text[:200]}"
                    continue
                return response.json()
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue

    # 回退：尝试 Anthropic 协议
    try:
        import httpx  # type: ignore

        api_key = keys[0]
        headers = build_headers(upstream, api_key)
        headers.pop("Authorization", None)
        if api_key:
            headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
        anthropic_payload = to_anthropic(list(payload.get("messages", [])), model)
        with httpx.Client(timeout=timeout) as client:
            response = client.post(endpoint_for(upstream, "/messages"), headers=headers, json=anthropic_payload)
            if response.status_code < 400:
                return from_anthropic(response.json(), model)
            last_error = f"anthropic:{response.status_code}"
    except Exception as exc:
        last_error = f"anthropic:{type(exc).__name__}: {exc}"

    return {"error": {"message": last_error or "upstream_failed", "type": "gateway_error"}}


def responses(payload: Dict[str, Any], upstream_name: str) -> Dict[str, Any]:
    """支持 /v1/responses（简单映射到 chat.completions）。"""
    mapped = {
        "model": payload.get("model"),
        "messages": [{"role": "user", "content": payload.get("input", "")}],
    }
    return chat_completion(mapped, upstream_name)