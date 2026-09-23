// 与后端 schemas 对齐的类型定义。命名与 API 字段保持一致。

import {
  RESOLUTION_TIERS,
  RESOLUTION_UNKNOWN_KEY,
  RESOLUTION_UNKNOWN_LABEL,
} from '../utils/resolution'

export interface Pagination<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface MessageResponse {
  message: string
  detail?: Record<string, unknown>
}

// 核心实体的双 ID + 软删除公共字段（与后端 BaseEntityMixin 对齐）
export interface EntityIdentity {
  uid: string
  deleted_at?: string | null
}

// ===== Artist =====
export interface Artist extends EntityIdentity {
  id: number
  name: string
  sort_name?: string | null
  chinese_name?: string | null
  english_name?: string | null
  korean_name?: string | null
  stage_name?: string | null
  aliases?: string[] | null
  gender?: string | null
  birth_date?: string | null
  birth_place?: string | null
  occupation?: string | null
  debut_date?: string | null
  social_media?: Record<string, unknown> | null
  external_links?: unknown[] | null
  description?: string | null
  avatar_path?: string | null
  banner_path?: string | null
  hide_from_home?: boolean
  completion_pct?: number | null
  video_count?: number | null
  updated_at?: string | null
}

export interface ArtistBrief extends EntityIdentity {
  id: number
  name: string
  chinese_name?: string | null
  stage_name?: string | null
  avatar_path?: string | null
}

export interface ArtistFuzzyHit extends ArtistBrief {
  korean_name?: string | null
  score: number
}

// ===== Group =====
export interface GroupSubUnitBrief extends EntityIdentity {
  id: number
  name: string
  chinese_name?: string | null
  group_type?: string | null
}

export interface Group extends EntityIdentity {
  id: number
  name: string
  sort_name?: string | null
  chinese_name?: string | null
  english_name?: string | null
  korean_name?: string | null
  aliases?: string[] | null
  group_type?: string | null
  parent_group_id?: number | null
  gender_type?: string | null
  origin_country?: string | null
  debut_date?: string | null
  description?: string | null
  social_media?: Record<string, unknown> | null
  external_links?: unknown[] | null
  member_count?: number | null
  avatar_path?: string | null
  banner_path?: string | null
  hide_from_home?: boolean
  company_ids?: number[] | null
  completion_pct?: number | null
  // 详情接口填充
  parent_name?: string | null
  parent_uid?: string | null
  sub_units?: GroupSubUnitBrief[]
}

export interface GroupBrief extends EntityIdentity {
  id: number
  name: string
  chinese_name?: string | null
  group_type?: string | null
  avatar_path?: string | null
}

export interface GroupFuzzyHit extends GroupBrief {
  english_name?: string | null
  korean_name?: string | null
  score: number
}

// ===== EntityImage（头像/横幅候选历史，与后端 entity_images 表对齐）=====
export type EntityImageSource = 'upload' | 'site' | 'crop' | 'existing'
export type EntityImageKind = 'avatar' | 'banner'
export type EntityImageEntityType = 'artist' | 'group'

export interface EntityImageRow {
  id: number
  entity_type: EntityImageEntityType
  entity_id: number
  kind: EntityImageKind
  rel_path: string
  url: string
  source: EntityImageSource
  width?: number | null
  height?: number | null
  created_at?: string | null
  is_primary: boolean
}

export interface EntityImageListResult {
  items: EntityImageRow[]
}

export interface EntityImagePointerResult {
  item: EntityImageRow | null
  kind: EntityImageKind
  pointer: string | null
}

// ===== Membership =====
export interface Membership {
  id: number
  group_id: number
  artist_id: number
  join_date?: string | null
  leave_date?: string | null
  status: string
  positions?: string[] | null
}

export interface MembershipWithArtist extends Membership {
  artist_name?: string | null
  artist_chinese_name?: string | null
  artist_korean_name?: string | null
  artist_stage_name?: string | null
  artist_avatar_path?: string | null
  artist_uid?: string | null
}

export interface MembershipWithGroup extends Membership {
  group_name?: string | null
  group_chinese_name?: string | null
  group_type?: string | null
  group_uid?: string | null
  // 母队溯源：小分队指向的顶级完整体（无上级时为空）
  root_group_name?: string | null
  root_group_chinese_name?: string | null
  root_group_type?: string | null
  root_group_uid?: string | null
}

// ===== Company =====
export interface Company extends EntityIdentity {
  id: number
  name: string
  chinese_name?: string | null
  english_name?: string | null
  korean_name?: string | null
  aliases?: string[] | null
  company_type?: string | null
  parent_company_id?: number | null
  description?: string | null
  social_media?: Record<string, unknown> | null
  external_links?: unknown[] | null
  completion_pct?: number | null
}

export interface CompanyRelationMember {
  id: number
  name: string
  chinese_name?: string | null
  korean_name?: string | null
  status: string
  role?: string | null
  start_date?: string | null
  end_date?: string | null
}

export interface CompanyRelations {
  company_id: number
  company_name: string
  artists: CompanyRelationMember[]
  groups: CompanyRelationMember[]
}

// ===== Album =====
export interface Album extends EntityIdentity {
  id: number
  name: string
  chinese_name?: string | null
  english_name?: string | null
  korean_name?: string | null
  aliases?: string[] | null
  release_date?: string | null
  album_type?: string | null
  release_artist_type?: string | null
  release_artist_id?: number | null
  label_id?: number | null
  description?: string | null
  cover_path?: string | null
  completion_pct?: number | null
  track_count?: number | null
}

export interface AlbumBrief extends EntityIdentity {
  id: number
  name: string
  chinese_name?: string | null
  album_type?: string | null
  cover_path?: string | null
  /** 专辑自身发行主体展示名（release_artist_*，未填为 null），下拉提示语兜底用 */
  owner?: string | null
  /** 由「专辑内曲目名」而非专辑名命中时给出（`brief(q, true)` 才填） */
  matched_song_id?: number | null
  matched_song_name?: string | null
  matched_song_owner?: string | null
}

export interface CoverSearchItem {
  id: string
  name: string
  artist?: string | null
  cover_url: string
  year?: string | null
  source: string
}

export interface AlbumFuzzyHit extends AlbumBrief {
  score: number
}

export interface AlbumTrack {
  id: number
  album_id: number
  song_id: number
  disc_number: number
  track_number: number
}

export interface AlbumTrackDetail extends AlbumTrack {
  song_name?: string | null
  song_chinese_name?: string | null
  song_duration?: number | null
  song_uid?: string | null
}

// ===== 专辑曲目外部导入 =====
export interface AlbumExternalCandidate {
  external_id: string
  name: string
  artist?: string | null
  cover_url?: string | null
  year?: string | null
  source: string
  track_count?: number | null
}

export interface AlbumTracklistItem {
  disc_number: number
  track_number: number
  name: string
  duration?: number | null
  external_track_id?: string | null
}

export interface AlbumTracklist {
  external_id: string
  name: string
  artist?: string | null
  cover_url?: string | null
  year?: string | null
  source: string
  tracks: AlbumTracklistItem[]
}

export interface AlbumImportTrackItem {
  name: string
  disc_number: number
  track_number: number
  duration?: number | null
  external_track_id?: string | null
  matched_song_id?: number | null
  // 存疑条目的人工结论：明确要求新建（不自动匹配库内同名歌）
  force_new?: boolean
}

export interface AlbumImportReviewVideo {
  video_id: number
  video_name?: string | null
}

export interface AlbumImportReviewCandidate {
  song_id: number
  song_name: string
  song_chinese_name?: string | null
  song_duration?: number | null
  videos: AlbumImportReviewVideo[]
}

export interface AlbumImportPreviewItem {
  index: number
  name: string
  disc_number: number
  track_number: number
  duration?: number | null
  match_status: 'match' | 'new' | 'in-album' | 'review'
  matched_song_id?: number | null
  matched_song_name?: string | null
  matched_song_duration?: number | null
  position_status?: 'free' | 'same' | 'conflict' | null
  position_song_name?: string | null
  review_candidates: AlbumImportReviewCandidate[]
}

export interface AlbumImportTrackResult {
  name: string
  song_id?: number | null
  detail?: string | null
}

export interface AlbumImportTracksResult {
  linked: AlbumImportTrackResult[]
  created: AlbumImportTrackResult[]
  failed: AlbumImportTrackResult[]
  skipped: AlbumImportTrackResult[]
}

export interface AlbumAlignTrackResult {
  name: string
  song_id?: number | null
  disc_number?: number | null
  track_number?: number | null
  action: 'aligned' | 'same' | 'appended' | 'skipped'
  detail?: string | null
}

export interface AlbumAlignTracksResult {
  aligned: AlbumAlignTrackResult[]
  appended: AlbumAlignTrackResult[]
  skipped: AlbumAlignTrackResult[]
}

// ===== Song =====
export interface Song extends EntityIdentity {
  id: number
  name: string
  chinese_name?: string | null
  english_name?: string | null
  korean_name?: string | null
  aliases?: string[] | null
  release_date?: string | null
  song_type?: string | null
  release_artist_type?: string | null
  release_artist_id?: number | null
  duration?: number | null
  description?: string | null
  external_links?: unknown[] | null
  album_ids?: number[] | null
  completion_pct?: number | null
  video_count?: number | null
  relation_names?: string[] | null
}

export interface SongBrief extends EntityIdentity {
  id: number
  name: string
  chinese_name?: string | null
  album_ids?: number[] | null
  /** 所属主体展示名（`brief(q, { withOwner: true })` 才填）；同名歌曲靠它区分 */
  owner?: string | null
  /** 不是按歌曲主名/中文名命中时给出：english_name / korean_name / alias */
  matched_field?: 'english_name' | 'korean_name' | 'alias' | null
  /** 命中的具体值（别名原文 / 韩文名…），供前端写「别名「XXX」」 */
  matched_value?: string | null
  /** 由「所属专辑名」而非歌曲名命中时给出（`albumHits: true` 才填） */
  matched_album_id?: number | null
  matched_album_name?: string | null
}

export interface SongFuzzyHit extends SongBrief {
  english_name?: string | null
  korean_name?: string | null
  score: number
}

export interface MusicVideoTrack {
  song_id: number
  album_ids: number[]
}

// ===== MusicVideo =====
export type VideoType =
  | 'OfficialMV'
  | 'PerformanceVideo'
  | 'Fancam'
  | 'SpecialStage'
  | 'CollabStage'
  | 'CoverStage'
  | 'MixEdit'
  | 'Teaser'
  | 'SpecialVideo'
  | 'ShortVideo'
  | 'Other'

export interface MusicVideoBrief extends EntityIdentity {
  id: number
  name: string
  video_type: string
  duration?: number | null
  width?: number | null
  height?: number | null
  thumbnail_path?: string | null
  // 封面焦点（0~1 归一化人脸质心；供裁切时保持人脸居中，null 回退居中）
  focus_x?: number | null
  focus_y?: number | null
  ingestion_status: string
  song_id?: number | null
  subject_artist_id?: number | null
  // 关联名称 + 卡片视图所需字段
  song_name?: string | null
  song_chinese_name?: string | null
  subject_artist_name?: string | null
  subject_artist_chinese_name?: string | null
  performance_date?: string | null
  release_date?: string | null
  cover_manual?: boolean
}

// 完整对象：详情/编辑用（对应后端 MusicVideoRead）
export interface MusicVideo extends MusicVideoBrief {
  name: string
  original_title?: string | null
  chinese_name?: string | null
  english_name?: string | null
  korean_name?: string | null
  aliases?: string[] | null
  song_id?: number | null
  video_type: string
  video_types?: VideoType[] | null
  song_ids?: number[] | null
  album_ids?: number[] | null
  tracks: MusicVideoTrack[]
  artist_ids?: number[] | null
  group_ids?: number[] | null
  subject_artist_id?: number | null
  event_name?: string | null
  is_short?: boolean | null
  is_solo?: boolean | null
  release_date?: string | null
  published_date?: string | null
  source_platform?: string | null
  source_id?: string | null
  source_url?: string | null
  original_uploader?: string | null
  duration?: number | null
  width?: number | null
  height?: number | null
  video_codec?: string | null
  audio_codec?: string | null
  frame_rate?: number | null
  bitrate?: number | null
  file_size?: number | null
  external_links?: unknown[] | null
  description?: string | null
  chinese_description?: string | null
  file_name?: string | null
  file_hash?: string | null
  file_path?: string | null
  // 多对多关联
  songs: SongBrief[]
  albums: AlbumBrief[]
  artists: ArtistBrief[]
  groups: GroupBrief[]
  // 翻唱原唱展示名
  cover_from?: string[]
}

// ===== 存储路径变更（详情页编辑用） =====
export interface VideoPathSuggestion {
  current_path: string
  // 自动整理规则不适用时为 null
  suggested_path?: string | null
  absolute_path?: string | null
  // 规则被阻断 / 不适用时的具体原因（如重名艺人缺中文名）
  notice?: string | null
}

export interface VideoRelocateResult {
  previous_path: string
  file_path: string
  moved_sidecars: string[]
}

// ===== Playback Session（统一播放会话 /api/playback/sessions） =====
export type PlayMode = 'direct_play' | 'direct_stream' | 'transcode'

export interface AudioStreamInfo {
  index: number
  codec?: string | null
  language?: string | null
  channels?: number | null
  default?: boolean | null
}

export interface SubtitleStreamInfo {
  index: number
  language?: string | null
  codec?: string | null
}

export interface PlaybackSession {
  session_id: number
  music_video_id: number
  play_mode: PlayMode | string
  transcode_required: boolean
  requested_quality?: string | null
  decision_reason?: string | null
  quality: string
  width?: number | null
  height?: number | null
  video_bitrate?: number | null
  audio_bitrate?: number | null
  video_codec?: string | null
  audio_codec?: string | null
  container?: string | null
  stream_protocol?: string | null
  status: string
  created_at?: string | null
  last_active_at?: string | null
  stream_url?: string | null
  manifest_url?: string | null
  // 片源完整时长（秒）；转码进度条总长度用此值
  source_duration?: number | null
  // 本路转码 window_start（秒）；进度条绝对时间 = start_offset + player.currentTime()
  start_offset?: number
  subtitle_urls: SubtitleStreamInfo[]
  audio_streams: AudioStreamInfo[]
  available_qualities: string[]
}

/**
 * 播放会话的「已可播范围」（GET /playback/sessions/{id}/window）。
 *
 * 用途：转码进行中 HLS 清单无 ENDLIST → 浏览器 MSE 的 duration 恒为
 * Infinity，前端无法据此判断「目标分片转出来了没有」；改为用后端给的
 * available_until（**片源绝对秒**）判断：超出即需重建会话（-ss 到目标）。
 */
export interface PlaybackSessionWindow {
  session_id: number
  play_mode: PlayMode | string
  /** 本路转码 window_start（秒） */
  start_offset: number
  /** 已转出、可原地 seek 到的片源绝对秒；null=直连（不走 HLS，无需判断） */
  available_until?: number | null
  /** 片源完整时长（秒） */
  source_duration?: number | null
  /** 本路转码是否已结束（true 后 available_until 不再变化，可停止轮询） */
  finished: boolean
}

export interface TranscodeSessionInfo {
  transcode_session_id: number
  status: string
  hardware_acceleration?: string | null
  ffmpeg_pid?: number | null
  progress?: number | null
  exit_code?: number | null
  started_at?: string | null
  stopped_at?: string | null
  full_command?: string | null
  stderr_tail?: string | null
  error_message?: string | null
  output_directory?: string | null
  running: boolean
}

export const QUALITY_LABEL: Record<string, string> = {
  original: '原画',
  '2160p': '2160P',
  '1440p': '1440P',
  '1080p': '1080P',
  '720p': '720P',
  '480p': '480P',
  '360p': '360P',
}

// ===== Library 扫描 / 入库 =====
export interface ScannedFileItem {
  path: string
  file_name: string
  file_size?: number | null
  duration?: number | null
  width?: number | null
  height?: number | null
  video_codec?: string | null
  audio_codec?: string | null
  file_hash?: string | null
  is_duplicate: boolean
}

// ===== 库驱动本地匹配（先建库后入库：专辑来自库内 AlbumTrack 关系）=====
export interface MatchHintEntity {
  id: number
  uid?: string | null
  name: string
  chinese_name?: string | null
}

export interface MatchHintTrack {
  song_id: number
  song_name: string
  chinese_name?: string | null
  album_ids: number[]
  confidence: 'high' | 'medium'
  source: string
}

export interface MatchHints {
  artists: MatchHintEntity[]
  groups: MatchHintEntity[]
  albums: MatchHintEntity[]
  tracks: MatchHintTrack[]
  notices: string[]
  match_mode?: IngestMatchMode
}

export interface ScanSyncStats {
  scanned: number
  added: number
  updated: number
  removed: number
  skipped_settling: number
  probe_failed: number
  skipped_unmounted: boolean
  skipped_running?: boolean
}

export interface IngestRequest {
  file_path: string
  music_video: Partial<MusicVideo> & { name: string; video_type: string }
  move_file: boolean
  destination_rel?: string | null
}

export interface PathPreview {
  relative_path: string
  absolute_path: string
  notice?: string | null
}

export interface BuildTitle {
  name: string
}

export interface IngestResult {
  music_video_id: number
  moved: boolean
  destination_path?: string | null
}

// ===== info.json 精简视图（不含评论/字幕等）=====
export interface IncomingInfo {
  title?: string | null
  fulltitle?: string | null
  webpage_url?: string | null
  id?: string | null
  extractor?: string | null
  uploader?: string | null
  uploader_id?: string | null
  channel?: string | null
  upload_date?: string | null
  timestamp?: number | null
  description?: string | null
  duration?: number | null
  width?: number | null
  height?: number | null
  resolution?: string | null
  fps?: number | null
  vcodec?: string | null
  acodec?: string | null
  filesize_approx?: number | null
  format_id?: string | null
  format?: string | null
  view_count?: number | null
  like_count?: number | null
  thumbnail?: string | null
  has_local_cover?: boolean
  // Bilibili NFO/Bili23-json 扩展（extractor === 'bilibili' 时存在）
  bili_tags?: string[] | null
  bili_up_mid?: string | null
  premiered?: string | null
}

// ===== AI 建议 =====
export interface AiSuggestRequest {
  provider: string
  base_url: string
  api_key?: string
  model: string
  context: Record<string, unknown>
  current: Record<string, unknown>
  /** 用户手动补充的辅助识别备注（百科链接 / 归属说明等），原样并入提示词 */
  aux_notes?: string
}

export interface AiSuggestResult {
  original_title?: string | null
  name?: string | null
  chinese_name?: string | null
  video_type?: string | null
  video_types?: string[] | null
  event_name?: string | null
  performance_date?: string | null
  subject_artist_name?: string | null
  is_solo?: boolean | null
  chinese_description?: string | null
  suggested_songs?: string[] | null
  suggested_albums?: string[] | null
  suggested_artists?: string[] | null
  suggested_groups?: string[] | null
  suggested_tracks?: { song: string; albums?: string[] }[] | null
  notice?: string | null
}

export interface AiAnalyzeMembership {
  group_name: string
  join_date?: string | null
  leave_date?: string | null
  positions?: string[] | null
}

export interface AiAnalyzeMemberCandidate {
  id: number
  name: string
  korean_name?: string | null
  /** 艺人当前所属组合及身份，如「NMIXX（现役）」 */
  groups?: string[] | null
}

export interface AiAnalyzeMember {
  name: string
  stage_name?: string | null
  korean_name?: string | null
  chinese_name?: string | null
  english_name?: string | null
  aliases?: string[] | null
  join_date?: string | null
  leave_date?: string | null
  positions?: string[] | null
  status?: string | null
  matched_artist_id?: number | null
  matched_artist_name?: string | null
  /** artist=默认关联 matched_artist_id；new=默认新建（普通组合同名优先新建） */
  match_mode?: 'artist' | 'new'
  /** 同名候选是否已属于其他组合（同名不同人的强信号） */
  conflict?: boolean
  /** 全部同名候选，供导入弹窗手动改选 */
  candidates?: AiAnalyzeMemberCandidate[] | null
}

// ===== 数据库关联视图 =====
export interface DbRelationIssueTarget {
  kind?: string
  id?: number
  membership_id?: number
  group_id?: number
  parent_group_id?: number
  [key: string]: unknown
}
export interface DbRelationIssue {
  level: 'warn' | 'info'
  text: string
  code?: string
  target?: DbRelationIssueTarget
  action?: string
  meta?: Record<string, unknown>
}
export interface DbRelations {
  kind: 'songs' | 'albums' | 'artists' | 'groups' | 'companies'
  entity: Record<string, unknown>
  relations: Record<string, unknown>
  issues: DbRelationIssue[]
}

export interface AiAnalyzeRequest {
  provider: string
  base_url: string
  api_key?: string
  model: string
  entity_type: string
  current: Record<string, unknown>
  memberships: Record<string, unknown>[]
  group_names: string[]
  /** 字段锁配套：只让 AI 填写这些键（缺省=全字段任务） */
  only_fields?: string[]
  /** 组合成员匹配的上下文：正在编辑的组合 id（普通组合同名默认新建，小分队默认复用母队艺人） */
  group_id?: number | null
  /** 用户手动补充的辅助识别备注（百科链接 / 归属说明等），高可信并入提示词 */
  aux_notes?: string
}

export interface AiAnalysisSource {
  field: string
  value: string
  source: string
}

export interface AiAnalyzeResult {
  fields: Record<string, unknown>
  group_memberships?: AiAnalyzeMembership[] | null
  members?: AiAnalyzeMember[] | null
  unresolved_companies?: string[] | null
  unresolved_albums?: string[] | null
  /** AI 返回的来源清单（供展示核对） */
  sources?: AiAnalysisSource[] | null
}

export interface AiSearchAlbumRequest {
  provider: string
  base_url: string
  api_key?: string
  model: string
  context: Record<string, unknown>
  current: Record<string, unknown>
  song_names?: string[]
  artist_names?: string[]
  group_names?: string[]
  /** 用户手动补充的辅助识别备注 */
  aux_notes?: string
}

export interface AiSearchAlbumResult {
  song_name?: string | null
  performer?: string | null
  suggested_albums: string[]
  /** 多首歌曲时的逐曲分组结果 */
  multi_songs?: { song: string; albums: string[] }[]
  /** AI 返回的来源清单（供展示核对） */
  sources?: AiAnalysisSource[] | null
}

export type HomeStageType = 'artist' | 'video'

// PC 首页刊头（HomeMagazineHero）优先用哪种源图：
//   'avatar' = 头像特写优先（默认，无头像才回退横幅）；
//   'banner' = 横幅海报优先（有横幅就用横幅，无横幅才回退头像）。
// **仅 PC 生效** —— 移动端走 HomeCinemaHero（1:1 头像盒），不读这一项。
// 口径写在 `stores/settings.ts`（localStorage `kpml_home_hero_image`），入口在「基础设置 · 外观与显示」。
export type HomeHeroImage = 'avatar' | 'banner'

/**
 * 外观与显示（2026-09-22 起改为**跨设备同步**：存后端 app_settings.appearance）。
 * localStorage 只留作首屏缓存（防主题闪烁）与未登录兜底，hydrate 后一律以后端为准。
 * 后端 PATCH 支持部分字段（exclude_unset 是递归的）；前端统一提交全量 4 字段，简单且无歧义。
 */
export interface AppAppearanceSettings {
  theme: 'light' | 'dark' | string
  accent: 'rose' | 'blue' | string
  show_covers: boolean
  home_hero_image: HomeHeroImage | string
}

export interface AppHeroSettings {
  video_id?: number | null
  // 首页轮播池（第一条即主打视频）
  video_ids?: number[] | null
  // 首页主舞台类型：artist=艺人/组合聚焦轮播；video=视频精选轮播
  stage_type?: HomeStageType | string
}
// 详情页主图固定头像（原 avatar/banner/test 三选一设置已移除）：
// 逻辑见 composables/useDetailPageTheme.ts::resolveHeroKind —— 有头像用头像，
// 没头像才回退横幅海报，保证不出现空主视觉。

export interface AppIngestAiSettings {
  enabled: boolean
  provider: string
  base_url: string
  api_key: string
  model: string
  // 原「自动化能力」三开关（auto_describe/auto_tag/auto_subject）为死设置，已随 UI 一并移除
  // 艺人/组合一句话简介的自定义提示词；为空时后端用内置默认
  tagline_prompt?: string
  // 视频中文简介（chinese_description）规则的自定义提示词；为空时后端用内置默认
  desc_prompt?: string
  api_key_set?: boolean
}

export interface AppMtPhotosSettings {
  enabled: boolean
  base_url: string
  api_key: string
  disk_prefix?: string
  mount_path?: string
  api_key_set?: boolean
}

export interface AppExternalProxySettings {
  enabled: boolean
  url: string
}

export interface AppTmdbSettings {
  enabled: boolean
  api_key: string
  api_key_set?: boolean
}

// 「放行跨站写请求」设置项已移除（v3.2.18）：设置页再无开关，AppSettings 也无 network 分区。
// 部署层后路 = 环境变量 ALLOW_CROSS_ORIGIN_WRITES（后端启动时读取，前端不再感知）。

// 入库自动关联策略：原「普通（normal）」档已移除，固定严格。
// 收紧成字面量类型 —— 以后谁再想写出 normal 分支，编译期就会报错。
// 备注：「内容与匹配」设置栏已下线，后端不再回传 ingest 分区，档位恒为 strict。
export type IngestMatchMode = 'strict'

/** 单个博主的固定视频类型规则（命中上传人 → 入库直接采用该类型，AI 不判断） */
export interface AppUploaderRule {
  name: string
  video_types: string[]
}

export interface AppUploaderRulesSettings {
  rules: AppUploaderRule[]
}

export interface AppSettings {
  appearance: AppAppearanceSettings
  hero: AppHeroSettings
  ingest_ai: AppIngestAiSettings
  mtphotos: AppMtPhotosSettings
  external_proxy: AppExternalProxySettings
  tmdb: AppTmdbSettings
  uploader_rules?: AppUploaderRulesSettings
}

// ===== 站点获取（Provider） =====
export interface ProviderSearchItem {
  external_id: string
  name: string
  kind: 'artist' | 'group'
  thumbnail?: string | null
  bio_excerpt?: string | null
  source?: string | null
}

export interface ProviderDetail {
  external_id: string
  name: string
  kind: 'artist' | 'group'
  biography?: string | null
  biography_lang?: string | null
  thumb?: string | null
  logo?: string | null
  fanart?: string | null
  banner?: string | null
  wide?: string | null
  gallery?: string[] | null
  meta?: string | null
  /** 成员名单（仅 fandom：infobox 解析结果，组合页可勾选导入） */
  members?: { name: string; korean_name?: string | null }[] | null
}

export interface FetchExternalPayload {
  external_id: string
  apply_biography: boolean
  apply_image: boolean
  image_url?: string | null
  /** 导入 Fandom 扩展信息（组合成员 / 别名 / 出道日期） */
  apply_members?: boolean
}

export interface FetchExternalResult {
  entity: Artist | Group
  applied_biography: boolean
  applied_image: boolean
  message?: string | null
}

// ===== Dashboard =====
export interface DashboardStats {
  total_videos: number
  incoming_count: number
  library_count: number
  total_artists: number
  total_groups: number
  total_songs: number
  total_albums: number
  video_type_breakdown: Record<string, number>
}

export interface StatsOverview {
  library_count: number
  incoming_count: number
  shorts_count: number
  last_30d_count: number
  total_duration_seconds: number
  total_size_bytes: number
  photo_count: number
  type_breakdown: Record<string, number>
  /** 分辨率构成，键为档位：8k / 4k / 2k / 1080p / 720p / 480p / 360p / 240p / sd / unknown */
  resolution_breakdown: Record<string, number>
}

export interface StatsRankItem {
  id: number
  uid: string
  name: string
  chinese_name?: string | null
  extra?: string | null
  avatar_path?: string | null
  video_count: number
  duration_seconds: number
}

export interface StatsWatchBlock {
  available: boolean
  play_count: number
  seconds_watched: number
  completed_count: number
  artists: StatsRankItem[]
  groups: StatsRankItem[]
  songs: StatsRankItem[]
  videos: StatsRankItem[]
  items: StatsRankItem[]
}

export interface LibraryStats {
  overview: StatsOverview
  range: 'all' | '30d' | 'year' | string
  include_shorts: boolean
  artists: StatsRankItem[]
  groups: StatsRankItem[]
  /** 镜头焦点：仅按直拍对象（subject_artist_id）聚合 */
  subject_artists?: StatsRankItem[]
  songs: StatsRankItem[]
  watch: StatsWatchBlock
}

// ===== 年度回顾（recap）=====
export interface RecapDay {
  date: string
  count: number
}

export interface RecapHour {
  hour: number
  count: number
}

export interface RecapMonth {
  month: string
  count: number
}

export interface RecapRecord {
  id: number
  uid: string
  name: string
  chinese_name?: string | null
  video_type?: string | null
  created_at?: string | null
  duration: number
  file_size: number
}

export interface StatsRecap {
  heatmap: RecapDay[]
  clock: RecapHour[]
  clock_peak_hour: number | null
  records: { first: RecapRecord | null; longest: RecapRecord | null; largest: RecapRecord | null }
  monthly: RecapMonth[]
}

// ===== System =====
export interface FFmpegStatus {
  available: boolean
  message: string
  ffmpeg_path?: string | null
  ffprobe_path?: string | null
  ffmpeg_version?: string | null
  ffprobe_version?: string | null
}

export interface CacheStats {
  root: string
  dirs: number
  files: number
  total_bytes: number
}

export interface CacheClearResult {
  dirs_removed: number
  bytes_freed: number
  remaining_bytes: number
}

export interface SystemPaths {
  incoming: string
  library: string
  derived: string
  transcode: string
  thumbnails: string
}

export interface MissingVideoItem {
  id: number
  name: string
  file_path?: string | null
  ingestion_status: string
}

export interface LibraryCleanupResult {
  scanned: number
  cleaned: number
  missing: number
  skipped_unmounted: boolean
  unmounted_roots: string[]
  items: MissingVideoItem[]
  files_purged?: number
}

export interface FolderChild {
  name: string
  count: number
  cover_id?: number | null
}

export interface FolderBrowseResult {
  prefix: string
  folders: FolderChild[]
  videos: MusicVideo[]
  video_total: number
  page: number
  page_size: number
}

/** 某一分辨率档位在当前筛选条件下的条数（浏览页「分辨率」菜单的数据源）。 */
export interface ResolutionFacetItem {
  key: string
  count: number
}

export interface ResolutionFacetResponse {
  /** 只含有条数的档位，顺序为高清 → 低清，未探测收尾 */
  items: ResolutionFacetItem[]
}

export type RecycleKind = 'videos' | 'artists' | 'groups' | 'songs' | 'albums' | 'companies'

export type PhotoOwnerType = 'artist' | 'group'
export type PhotoSection = 'official' | 'fan' | 'wall'
export type PhotoMediaKind = 'image' | 'video'
export type PhotoProvider = 'folder' | 'mtphotos'

export interface PhotoSource {
  id: number
  owner_type: PhotoOwnerType | string
  owner_id: number
  section: PhotoSection | string
  folder_path: string
  provider?: PhotoProvider | string
  external_id?: string | null
  recursive: boolean
  last_scanned_at?: string | null
  last_scan_files: number
  last_scan_posts: number
  photo_count: number
  label?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface PhotoBrowseEntry {
  name: string
  path: string
  photo_count: number
  subfolder_count: number
}

export interface PhotoBrowseResult {
  path: string
  parent?: string | null
  entries: PhotoBrowseEntry[]
}

export interface MtPhotosAlbum {
  id: number
  name: string
  count: number
}

export interface MtPhotosFolder {
  id: number
  name: string
  path?: string
  count: number
  subfolder_count: number
}

export interface MtPhotosPingResult {
  version: string
  build: string
  album_count: number
}

export interface PhotoScanResult {
  source_id: number
  files_seen: number
  created: number
  updated: number
  restored: number
  removed: number
  posts: number
  elapsed_ms: number
  thumbs_queued?: number
  warning?: string
}

export interface PhotoGarment {
  desc?: string | null
  color?: string | null
}

export interface PhotoShoes {
  type?: string | null
  color?: string | null
  detail?: string | null
}

export interface PhotoHosiery {
  present?: string | null
  type?: string | null
  color?: string | null
  opacity?: string | null
}

export interface PhotoAnalysis {
  caption_zh?: string | null
  tags?: string[]
  manually_edited?: boolean
  scene?: string | null
  shot?: string | null
  shoes?: PhotoShoes | null
  hosiery?: PhotoHosiery | null
  prompt_version?: string
  people?: string[]
  top?: PhotoGarment | null
  bottom?: PhotoGarment | null
  outer?: PhotoGarment | null
  accessories?: string[]
  quality?: string[]
}

export interface PhotoItem extends EntityIdentity {
  id: number
  source_id: number
  section: string
  file_name: string
  media_kind: PhotoMediaKind | string
  published_at?: string | null
  caption?: string | null
  author?: string | null
  post_key?: string | null
  position_in_post: number
  analysis?: PhotoAnalysis | null
  analysis_prompt_version?: string | null
  analyzed_at?: string | null
  provider?: PhotoProvider | string
}

export interface PhotoFeedFilter {
  scene?: string
  shot?: string
  shoes_type?: string
  hosiery_present?: string
  analyzed?: boolean
  tags?: string[]
  on_date?: string
  q?: string
  author?: string
  sort_by?: 'date' | 'name' | string
  sort_dir?: 'asc' | 'desc' | string
}

export interface PhotoTimelineDay {
  date: string
  count: number
}

export interface PhotoTagOption {
  tag: string
  count: number
}

export interface PhotoAuthorOption {
  author: string
  count: number
}

export interface PhotoFilterOptions {
  options: PhotoTagOption[]
  authors?: PhotoAuthorOption[]
}

export interface PhotoFeedItem {
  kind: 'post' | 'photo'
  post_key?: string | null
  caption?: string | null
  author?: string | null
  published_at?: string | null
  photos: PhotoItem[]
}

export interface PhotoFeed {
  items: PhotoFeedItem[]
  total: number
  page: number
  page_size: number
  source_count: number
  photo_count: number
  last_scanned_at?: string | null
  readonly?: boolean
  timeline?: PhotoTimelineDay[]
}

export interface PhotoCollection extends EntityIdentity {
  id: number
  name: string
  description?: string | null
  photo_count: number
  cover_photo_id?: number | null
  created_at?: string | null
  updated_at?: string | null
}

export interface VideoCollection extends EntityIdentity {
  id: number
  name: string
  description?: string | null
  video_count: number
  cover_video_id?: number | null
  created_at?: string | null
  updated_at?: string | null
}

export interface RecycleItem {
  id: number
  uid?: string | null
  name: string
  chinese_name?: string | null
  deleted_at: string
  extra?: string | null
  file_missing?: boolean
}

// ===== 视频类型枚举 =====
export const VIDEO_TYPE_OPTIONS: { label: string; value: VideoType }[] = [
  { label: '官方 MV', value: 'OfficialMV' },
  { label: '官方舞台', value: 'PerformanceVideo' },
  { label: '粉丝直拍', value: 'Fancam' },
  { label: '特别舞台', value: 'SpecialStage' },
  { label: '合作舞台', value: 'CollabStage' },
  { label: '翻唱', value: 'CoverStage' },
  { label: 'MIX混剪', value: 'MixEdit' },
  { label: '预告', value: 'Teaser' },
  { label: '非表演视频', value: 'SpecialVideo' },
  { label: '短视频', value: 'ShortVideo' },
  { label: '其他视频', value: 'Other' },
]

export const VIDEO_TYPE_LABEL: Record<string, string> = Object.fromEntries(
  VIDEO_TYPE_OPTIONS.map((o) => [o.value, o.label]),
)

/** 历史视频类型 → 现行分类：旧库里留下的细分类型归并到现行 10 类里，
 *  保证统计页「收藏构成」只出现当前分类体系中的类别（官方/粉丝直拍本就都算 Fancam）。 */
export const LEGACY_VIDEO_TYPE_ALIAS: Record<string, VideoType> = {
  PersonalFancam: 'Fancam',
  GroupFancam: 'Fancam',
  OfficialFancam: 'Fancam',
  OfficialFacecam: 'Fancam',
}

// ===== 分辨率档位 =====
// 唯一数据源在 utils/resolution.ts（与后端 video_meta.RESOLUTION_TIERS 逐项对应，按短边归档）。
// 360P / 240P 各自成档，不再有笼统的「标清」；比 240P 更低的才落到 <240P。
export const RESOLUTION_OPTIONS: { key: string; label: string }[] = [
  ...RESOLUTION_TIERS.map(({ key, label }) => ({ key, label })),
  { key: RESOLUTION_UNKNOWN_KEY, label: RESOLUTION_UNKNOWN_LABEL },
]

export const RESOLUTION_LABEL: Record<string, string> = Object.fromEntries(
  RESOLUTION_OPTIONS.map((o) => [o.key, o.label]),
)

export const GROUP_TYPE_OPTIONS = [
  { label: '女团', value: 'Girl Group' },
  { label: '男团', value: 'Boy Group' },
  { label: '混声', value: 'Co-ed' },
  { label: '企划', value: 'Project' },
  { label: '小分队', value: 'Sub-unit' },
]

export const ALBUM_TYPE_OPTIONS = [
  { label: '单曲', value: 'Single' },
  { label: '迷你专辑', value: 'MiniAlbum' },
  { label: '正式专辑', value: 'FullAlbum' },
  { label: '再包装', value: 'Repackage' },
  { label: 'OST', value: 'OST' },
  { label: '合辑', value: 'Compilation' },
  { label: '其他', value: 'Other' },
]
