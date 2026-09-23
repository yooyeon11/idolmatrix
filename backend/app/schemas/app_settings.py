"""跨设备共享的应用设置 IO。"""

from __future__ import annotations

from typing import Any, List, Optional

from app.schemas.common import ORMModel

# 首页主舞台类型：artist=艺人/组合聚焦轮播（随机池）；video=视频精选轮播
HOME_STAGE_TYPES = ("artist", "video")


class HeroSettings(ORMModel):
    video_id: Optional[int] = None
    # 首页轮播池（第一条即主打视频）
    video_ids: Optional[List[int]] = None
    # 首页主舞台类型，默认艺人/组合聚焦轮播
    stage_type: str = "artist"
    # 详情页主图固定头像，不再作为可配置项（原 detail_hero_media 已移除）


class IngestAiSettings(ORMModel):
    enabled: bool = False
    provider: str = "openai-compatible"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    # 原「自动化能力」三开关 auto_describe / auto_tag / auto_subject 为死设置
    # （没有任何消费方），UI 与字段已一并移除；存量值在合并时被丢弃。
    # 艺人/组合一句话简介的自定义提示词；为空时用后端内置默认
    tagline_prompt: str = ""
    # 视频中文简介（chinese_description）规则的自定义提示词；为空时用后端内置默认
    # （默认 = 原版自由口径，见 ai_service.DEFAULT_DESC_RULE），可在 AI 设置页编辑 / 恢复默认
    desc_prompt: str = ""
    # 仅 GET 回读：是否已保存密钥（明文不返回）
    api_key_set: bool = False


class MtPhotosSettings(ORMModel):
    enabled: bool = False
    base_url: str = ""
    api_key: str = ""
    # MT Photos 返回的磁盘路径前缀（如 Y:\photo 或 /volume1/photo）
    disk_prefix: str = ""
    # 本容器内对应挂载点，默认 /data/mt-photos
    mount_path: str = "/data/mt-photos"
    api_key_set: bool = False


class ExternalProxySettings(ORMModel):
    # 外部站点（TheAudioDB/Deezer/iTunes/Wikidata/TMDB 等）出网代理开关
    enabled: bool = False
    # 代理地址，如 http://192.168.1.10:7890（支持 http/https/socks 需环境带相应支持）
    url: str = ""


class TmdbSettings(ORMModel):
    # TMDB 艺人资料来源（仅艺人，TMDB 无组合实体）
    enabled: bool = False
    # TMDB API Key（v3，32 位十六进制）
    api_key: str = ""
    api_key_set: bool = False


# 「放行跨站写请求」（原 NetworkSecuritySettings.allow_cross_origin_writes）已从产品设置层移除：
# 设置页不再有开关，DB 也再无 network 分区 —— 缺省即严格 CSRF 校验。
# 部署层后路仍保留：环境变量 ALLOW_CROSS_ORIGIN_WRITES（见 core/config.py 与 docker-compose.yml）。
# 存量库里的 network 分区行会被忽略（不再出现在 SECTIONS 白名单里），其 true 值不再生效。


# 入库自动关联策略：已固定为 strict（收紧字符串 + 成员关系闸）。
# 「内容与匹配」设置栏（v3.2.21）整栏下线后，本档位不再有任何设置入口 —— 分区也不在
# SECTIONS 白名单里，写入被忽略、回读不再出现。算法层的 normal（宽匹配）能力保留在
# name_match.normalize_ingest_match_mode 里，便于回退与单测。
INGEST_MATCH_MODES = ("strict",)


class UploaderRule(ORMModel):
    """单个博主的固定视频类型规则。

    命中的上传人（original_uploader，逐字比对，忽略首尾空白与大小写）在待整理 /
    入库时直接采用 video_types，不再由 AI 判断类型；未命中或未配置的博主仍交给 AI。
    """

    # 博主名 = 视频来源上传人（original_uploader）
    name: str = ""
    # 固定视频类型（VIDEO_TYPES 子集，至少一个；为空视为不生效）
    video_types: List[str] = []


class UploaderRulesSettings(ORMModel):
    rules: List[UploaderRule] = []


# ===== 外观与显示（2026-09-22 起跨设备同步）=====
# 主题 / 强调色 / 显示封面 / PC 刊头优先图，原先只落本机 localStorage（单机偏好），
# 结果是「换一台设备/浏览器就变回默认」。现改为存本分区，localStorage 只留作首屏缓存
# （防主题闪烁）与离线兜底，hydrate 后一律以本分区的值为准。
# PATCH 支持**部分字段**（pydantic 的 exclude_unset 是递归的，未传的内层字段不会出现在 payload 里，
# 已由 test_appearance_settings.py 第 5 节钉住）；前端为简单起见统一提交全量 4 字段，两种写法都安全。
THEMES = ("light", "dark")
ACCENTS = ("rose", "blue")
HOME_HERO_IMAGES = ("avatar", "banner")


class AppearanceSettings(ORMModel):
    # 亮暗主题，默认保持既有视觉（深色）
    theme: str = "dark"
    # 强调色，默认蓝
    accent: str = "blue"
    # 列表/卡片是否显示封面图
    show_covers: bool = True
    # PC 首页刊头优先图：avatar=有头像用头像（默认）；banner=有横幅用横幅
    home_hero_image: str = "avatar"


class AppSettingsRead(ORMModel):
    appearance: AppearanceSettings = AppearanceSettings()
    hero: HeroSettings = HeroSettings()
    ingest_ai: IngestAiSettings = IngestAiSettings()
    mtphotos: MtPhotosSettings = MtPhotosSettings()
    external_proxy: ExternalProxySettings = ExternalProxySettings()
    tmdb: TmdbSettings = TmdbSettings()
    uploader_rules: UploaderRulesSettings = UploaderRulesSettings()


class AppSettingsPatch(ORMModel):
    appearance: Optional[AppearanceSettings] = None
    hero: Optional[HeroSettings] = None
    ingest_ai: Optional[IngestAiSettings] = None
    mtphotos: Optional[MtPhotosSettings] = None
    external_proxy: Optional[ExternalProxySettings] = None
    tmdb: Optional[TmdbSettings] = None
    uploader_rules: Optional[UploaderRulesSettings] = None

    def sections(self) -> dict[str, Any]:
        data = self.model_dump(exclude_unset=True)
        return {k: v for k, v in data.items() if v is not None}
