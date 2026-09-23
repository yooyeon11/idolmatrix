"""AI 能力 IO 契约。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.schemas.common import ORMModel


class AiSuggestRequest(ORMModel):
    """调用入库 AI 生成元数据建议的请求。"""

    provider: str = "openai-compatible"
    base_url: str
    api_key: Optional[str] = ""
    model: str
    context: Dict[str, Any] = {}
    current: Dict[str, Any] = {}
    # 用户手动补充的辅助识别备注（粘贴百科链接 / 指明归属等），原样并入提示词
    aux_notes: Optional[str] = ""


class AiSuggestResult(ORMModel):
    """AI 返回的入库元数据建议。"""

    original_title: Optional[str] = None
    name: Optional[str] = None
    chinese_name: Optional[str] = None
    video_type: Optional[str] = None
    video_types: Optional[list[str]] = None
    event_name: Optional[str] = None
    performance_date: Optional[str] = None
    subject_artist_name: Optional[str] = None
    # 是否为 solo 表演（布尔值），AI 判断后供自动整理入库使用
    is_solo: Optional[bool] = None
    # 中文简介：由 AI 根据视频内容生成（原 description 保留来源原文，不入库建议）
    chinese_description: Optional[str] = None
    suggested_songs: Optional[list[str]] = None
    suggested_albums: Optional[list[str]] = None
    suggested_artists: Optional[list[str]] = None
    suggested_groups: Optional[list[str]] = None
    # 曲目配对：每首歌对应其专辑（可多张）。优先于平行的 songs/albums 数组。
    suggested_tracks: Optional[list[dict]] = None
    # 类型识别提示：AI 返回的类型无法精确匹配时给出的兜底说明（可选）
    notice: Optional[str] = None


class AiSearchAlbumRequest(ORMModel):
    """调用入库 AI 根据歌曲名与组合/艺人名判断所属专辑的请求。"""

    provider: str = "openai-compatible"
    base_url: str
    api_key: Optional[str] = ""
    model: str
    context: Dict[str, Any] = {}
    current: Dict[str, Any] = {}
    # AI 入库阶段已识别出的名称（优先使用；未传则从 current 的关联 id 解析）
    song_names: list[str] = []
    artist_names: list[str] = []
    group_names: list[str] = []
    # 用户手动补充的辅助识别备注
    aux_notes: Optional[str] = ""


class AiSearchAlbumResult(ORMModel):
    """AI 判断出的专辑名建议。"""

    song_name: Optional[str] = None
    performer: Optional[str] = None
    suggested_albums: list[str] = []
    # 多首歌曲时的逐曲分组结果：[{"song": "...", "albums": ["...", ...]}, ...]
    multi_songs: list[Dict[str, Any]] = []
    # 来源清单（供前端展示核对）：[{"field": "...", "value": "...", "source": "..."}, ...]
    sources: list[Dict[str, Any]] = []


class AiAnalyzeRequest(ORMModel):
    """调用 AI 分析并自动填充数据库编辑表单的请求。"""

    provider: str = "openai-compatible"
    base_url: str
    api_key: Optional[str] = ""
    model: str
    entity_type: str
    current: Dict[str, Any] = {}
    memberships: list[Dict[str, Any]] = []
    group_names: list[str] = []
    # 字段锁配套：仅让 AI 填写这些字段键（含 memberships 保留键）；
    # 缺省 None = 全字段任务（兼容旧行为）。锁定字段仍随 current 传入作上下文。
    only_fields: Optional[list[str]] = None
    # 用户手动补充的辅助识别备注（百科链接 / 归属说明等），高可信并入提示词
    aux_notes: Optional[str] = ""
    # 组合成员匹配的组合上下文（正在编辑的组合 id）：
    # 普通组合同名默认新建，小分队默认复用母队艺人
    group_id: Optional[int] = None


class AiAnalyzeResult(ORMModel):
    """AI 分析填充数据库编辑表单的结果。"""

    fields: Dict[str, Any] = {}
    group_memberships: Optional[list[Dict[str, Any]]] = None
    members: Optional[list[Dict[str, Any]]] = None
    unresolved_companies: Optional[list[str]] = None
    unresolved_albums: Optional[list[str]] = None
    # 来源清单（供前端展示核对）：[{"field": "...", "value": "...", "source": "..."}, ...]
    sources: list[Dict[str, Any]] = []


class AiEntityTaglineRequest(ORMModel):
    """调用 AI 为艺人/组合生成一句话简介的请求。"""

    provider: str = "openai-compatible"
    base_url: str
    api_key: Optional[str] = ""
    model: str
    # 自定义提示词（system prompt）；为空时使用后端默认
    prompt: Optional[str] = None
    entity_type: str  # artist | group
    entity_id: int


class AiEntityTaglineResult(ORMModel):
    """AI 生成的一句话简介（已写回实体，前端刷新展示即可）。"""

    tagline: str = ""


class AiSuggestSocialRequest(ORMModel):
    """调用入库 AI 提议艺人/组合社交媒体链接（只读，不写库）。"""

    provider: str = "openai-compatible"
    base_url: str = ""
    api_key: Optional[str] = ""
    model: str = ""
    entity_type: str  # artist | group
    entity_id: int
    # 为空时后端回落到「数据入库 AI」设置
    use_saved_ingest_ai: bool = True


class AiSuggestSocialResult(ORMModel):
    """提议的社交媒体链接 + 依据；前端确认后再 PATCH 写回。"""

    links: dict[str, str] = {}
    basis: str = ""
    sources: list[Dict[str, Any]] = []
