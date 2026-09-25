"""Pydantic 模型（SPEC 4 / 5）。"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

SceneType = Literal["present", "flashback", "vision", "simulation"]
DraftState = Literal["draft", "pending", "committed", "revoked", "discarded"]
Severity = Literal["low", "medium", "high"]


# ---------------------------------------------------------------- 请求模型


class Scene(BaseModel):
    loc: Optional[str] = None
    chars: List[str] = Field(default_factory=list)
    story_time: Optional[str] = None


class Message(BaseModel):
    id: Optional[str] = None
    content: str = ""
    role: Optional[str] = None


class InjectRequest(BaseModel):
    turn: int = 0
    world_id: str
    chat_id: Optional[str] = None
    scene: Scene = Field(default_factory=Scene)
    anchors: List[str] = Field(default_factory=list)
    recent_summary: Optional[str] = None
    budget: Optional[int] = None
    scene_type: SceneType = "present"
    flashback_target_time: Optional[str] = None
    speaking: List[str] = Field(default_factory=list)


class UpdateRequest(BaseModel):
    turn: int = 0
    world_id: str
    chat_id: str
    user_msg: Message = Field(default_factory=Message)
    ai_msg: Message = Field(default_factory=Message)
    scene: Scene = Field(default_factory=Scene)
    anchors: List[str] = Field(default_factory=list)
    scene_type: SceneType = "present"


class CausalLink(BaseModel):
    event_id: Optional[int] = None
    relation: str = "导致"
    desc: Optional[str] = None


class EventPayload(BaseModel):
    content: str
    causes: List[CausalLink] = Field(default_factory=list)
    effects: List[CausalLink] = Field(default_factory=list)
    chars: List[str] = Field(default_factory=list)
    loc: Optional[str] = None
    items: List[str] = Field(default_factory=list)
    importance: float = 0.5
    turn: Optional[int] = None
    story_time: Optional[str] = None
    unresolved: List[str] = Field(default_factory=list)
    preconditions: List[str] = Field(default_factory=list)
    source_msg: Optional[str] = None


class CommitRequest(BaseModel):
    draft_id: Optional[str] = None
    world_id: str
    event: EventPayload
    force: bool = False


class TickRequest(BaseModel):
    world_id: str
    meta_time: Optional[int] = None
    player_present: bool = False


class SummarizeRequest(BaseModel):
    world_id: str
    level: Literal["scene", "chapter", "volume"] = "scene"
    turn_start: int = 0
    turn_end: int = 0
    messages: List[Message] = Field(default_factory=list)


class LockRequest(BaseModel):
    world_id: str
    lock_type: str = "update"
    holder: str
    timeout_ms: int = 5000
    session_id: Optional[str] = None


class UnlockRequest(BaseModel):
    world_id: str
    holder: str


class JobRetryRequest(BaseModel):
    job_id: Optional[str] = None
    replay_dead_letters: bool = False


class ImportRequest(BaseModel):
    mode: Literal["merge", "overwrite"] = "merge"
    data: Dict[str, Any] = Field(default_factory=dict)


class ChapterCloseRequest(BaseModel):
    chapter_id: str


class AdminOverrideRequest(BaseModel):
    target_table: str
    target_id: str
    field: str
    value: Any = None
    reason: str = "manual_override"
    operator: str = "user"


class TemplateApplyRequest(BaseModel):
    template_id: str
    world_id: Optional[str] = None


class DraftUpsertRequest(BaseModel):
    world_id: str
    chat_id: Optional[str] = None
    draft: Dict[str, Any]


# ---------------------------------------------------------------- 响应模型


class DebugBlock(BaseModel):
    blocks: List[str] = Field(default_factory=list)
    dropped: List[str] = Field(default_factory=list)
    reason: str = ""
    warnings: List[str] = Field(default_factory=list)


class InjectResponse(BaseModel):
    inject_text: str = ""
    token_count: int = 0
    cache_hit: bool = False
    latency_ms: int = 0
    trace_id: str = ""
    debug: DebugBlock = Field(default_factory=DebugBlock)


class UpdateResponse(BaseModel):
    ok: bool = True
    job_id: str = ""
    queued: List[str] = Field(default_factory=list)
    trace_id: str = ""


class CommitResponse(BaseModel):
    ok: bool = True
    committed_event_id: Optional[int] = None
    causal_edges_created: int = 0
    trace_id: str = ""
    blocked: List[str] = Field(default_factory=list)


class TickResponse(BaseModel):
    off_screen_events: List[str] = Field(default_factory=list)
    world_status: str = "active"
    local_time: str = ""


class SummarizeResponse(BaseModel):
    summary_id: Optional[int] = None
    content: str = ""
    unresolved: List[str] = Field(default_factory=list)