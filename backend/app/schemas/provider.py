"""站点获取（外部元数据 Provider）相关 schema。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from app.schemas.common import ORMModel


class ProviderSearchItem(ORMModel):
    """外部数据源检索候选（TheAudioDB / Deezer / iTunes / Wikidata / TMDB）。"""

    external_id: str
    name: str
    kind: str = Field(description="artist 或 group")
    thumbnail: Optional[str] = None
    bio_excerpt: Optional[str] = None
    source: Optional[str] = Field(None, description="数据来源：TheAudioDB / Deezer / iTunes / Wikidata / TMDB")


class ProviderDetail(ORMModel):
    """外部条目详情：全简介 + 图片 URL；tmdb 来源额外带 gallery 与 meta。"""

    external_id: str
    name: str
    kind: str
    biography: Optional[str] = None
    biography_lang: Optional[str] = None
    thumb: Optional[str] = None
    logo: Optional[str] = None
    fanart: Optional[str] = None
    banner: Optional[str] = None
    wide: Optional[str] = None
    # 头像候选画廊（仅 tmdb：同一人物的多张 profiles，供网格自选）
    gallery: List[str] = []
    # 简短核对信息（仅 tmdb：职业 · 生日 · 出生地），辅助确认同名同人
    meta: Optional[str] = None
    # 成员名单（仅 fandom：infobox 解析结果，供组合「导入成员」勾选）
    members: Optional[List[Dict[str, Any]]] = None


class FetchExternalRequest(ORMModel):
    """把外部条目应用到本地实体。"""

    external_id: str
    apply_biography: bool = True
    apply_image: bool = True
    # 指定头像下载地址（画廊自选结果）；缺省回落 thumb，再回落 gallery 首图
    image_url: Optional[str] = None
    # 导入 Fandom 结构化扩展信息：组合成员导入 / 别名合并 / 出道日期填充
    apply_members: bool = False


class TmdbTestRequest(ORMModel):
    """TMDB 连接测试入参。"""

    api_key: str = ""


class FetchExternalResult(ORMModel):
    """fetch-external 的执行结果。"""

    entity: Dict[str, Any]
    applied_biography: bool
    applied_image: bool
    message: Optional[str] = None
