"""配置加载：config.yaml + 环境变量覆盖。

原则（SPEC 10.6 / 全局原则 14）：
- 密钥只从环境变量读取；
- config.yaml 只放引用名（api_key_env），不放密钥值。
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: Dict[str, Any] = {
    "server": {"host": "127.0.0.1", "port": 8000},
    "database": {"path": "data/memory.db"},
    "memory": {
        "total_budget": 1300,
        "hard_limit": 1500,
        "decay": {
            "affection": {"half_life": 200, "floor": 0.0, "refresh": 0.05},
            "trust": {"half_life": 300, "floor": 0.0, "refresh": 0.03},
            "respect": {"half_life": 500, "floor": 0.1, "refresh": 0.02},
            "fear": {"half_life": 80, "floor": 0.0, "refresh": 0.08},
            "hostility": {"half_life": 150, "floor": 0.0, "refresh": 0.06},
            "debt": {"half_life": 9999, "floor": 0.0, "refresh": 0.0},
        },
        "contact_levels": {
            "same_scene": 0.01,
            "direct_dialogue": 0.03,
            "cooperation": 0.05,
            "help": 0.08,
            "sacrifice": 0.20,
            "betrayal": -0.25,
        },
        "graph": {
            "max_hops": 2,
            "hop_weights": {1: 1.0, 2: 0.5},
            "max_nodes_per_hop": 5,
            "max_edges_per_node": 3,
            "allowed_edges": ["参与", "发生", "涉及", "推进", "持有"],
            "forbidden_edges": ["关系", "隶属"],
            "score_threshold": {
                "anchor": 0.3,
                "graph_1hop": 0.5,
                "graph_2hop": 0.7,
                "vector": 0.6,
            },
            "trigger_weights": {
                "character": 1.0,
                "location": 0.9,
                "item": 0.7,
                "event": 0.5,
                "relation": 0.4,
                "goal": 0.6,
                "faction": 0.5,
                "emotion": 0.2,
                "concept": 0.1,
            },
        },
        "causal": {
            "auto_commit_importance": 0.8,
            "stale_turns": 20,
            "discard_importance": 0.5,
        },
        "budget": {
            "priority": [
                "world_gate",
                "scene",
                "personality",
                "relations",
                "causal",
                "goals",
                "anchor_graph",
                "summaries",
                "vector",
                "warnings",
            ],
            "max_per_category": {
                "character_memories": 3,
                "location_memories": 2,
                "item_memories": 2,
                "vector_results": 3,
                "relations": 5,
                "goals": 3,
            },
            "cache_ttl": {
                "world_gate": 30,
                "scene": 5,
                "relations": 10,
                "anchor_graph": 5,
                "vector_results": 3,
            },
            "section_budget": {
                "world_gate": 150,
                "scene": 100,
                "personality": 200,
                "relations": 100,
                "causal": 200,
                "goals": 100,
                "anchor_graph": 150,
                "summaries": 150,
                "vector": 100,
                "warnings": 50,
            },
        },
        "char_priority": {
            "speaking": 1.0,
            "present": 0.7,
            "mentioned": 0.4,
            "offscreen_relevant": 0.2,
        },
    },
    "vector": {
        "provider": "chroma",
        "path": "data/chroma",
        "embedding_model": "text-embedding-3-small",
        "top_k": 3,
    },
    "gateway": {
        "enabled": True,
        "api_key_env": "GATEWAY_API_KEY",
        "upstreams": [
            {
                "name": "opencode-go",
                "base_url": "https://opencode.ai/zen/go/v1",
                "api_key_env": "OPENCODE_API_KEY",
                "headers": {"x-opencode-session": "auto"},
            },
            {"name": "local-memory", "base_url": "http://localhost:8000/v1"},
        ],
        "routes": [
            {"match": {"model_prefix": "memory-"}, "upstream": "local-memory"},
            {"match": {"model_prefix": "gpt-"}, "upstream": "opencode-go"},
        ],
        "timeout_s": 60,
    },
    "llm": {
        "summarizer": {"model": "gpt-4o-mini", "endpoint": "http://localhost:8000/v1"},
        "extractor": {"model": "gpt-4o-mini", "endpoint": "http://localhost:8000/v1"},
    },
    "runtime": {"lock_timeout_ms": 5000, "degrade_ms": 500, "job_workers": 2},
    "logging": {
        "dir": "data/logs",
        "level": "INFO",
        "slow_ms": 200,
        "max_bytes": 5242880,
        "backup_count": 5,
    },
}


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_config(path: str | os.PathLike[str] | None = None) -> Dict[str, Any]:
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    data: Dict[str, Any] = {}
    if cfg_path.exists():
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception:  # pragma: no cover - yaml 缺失时退回默认配置
            data = {}
    cfg = _deep_merge(DEFAULTS, data)

    # 环境变量只覆盖非敏感的运行参数
    cfg["server"]["host"] = os.getenv("MEMORY_PLUGIN_HOST", cfg["server"]["host"])
    cfg["server"]["port"] = int(os.getenv("MEMORY_PLUGIN_PORT", cfg["server"]["port"]))
    if os.getenv("MEMORY_PLUGIN_DB"):
        cfg["database"]["path"] = os.environ["MEMORY_PLUGIN_DB"]
    return cfg


CONFIG: Dict[str, Any] = load_config()


def get(path: str, default: Any = None) -> Any:
    """按 "a.b.c" 读取配置。"""
    node: Any = CONFIG
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def secret(env_name: str, default: str = "") -> str:
    """密钥只从环境变量读取。"""
    return os.getenv(env_name, default)


def mask(secret_value: str) -> str:
    """日志脱敏：sk-1234****5678。"""
    if not secret_value:
        return ""
    if len(secret_value) <= 8:
        return "****"
    return f"{secret_value[:5]}****{secret_value[-4:]}"


def db_path() -> Path:
    p = Path(get("database.path", "data/memory.db"))
    if not p.is_absolute():
        p = ROOT / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def vector_path() -> Path:
    p = Path(get("vector.path", "data/chroma"))
    if not p.is_absolute():
        p = ROOT / p
    p.mkdir(parents=True, exist_ok=True)
    return p
