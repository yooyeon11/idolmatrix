<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { NButton, NForm, NFormItem, NInput, NModal, NSpin, useMessage } from 'naive-ui'
import { aiApi } from '@/api/ai'
import { albumsApi } from '@/api/albums'
import { artistsApi } from '@/api/artists'
import { dashboardApi } from '@/api/dashboard'
import { groupsApi } from '@/api/groups'
import { libraryApi } from '@/api/library'
import { songsApi } from '@/api/songs'
import { uploadersApi } from '@/api/uploaders'
import { ALBUM_TYPE_OPTIONS, VIDEO_TYPE_LABEL, VIDEO_TYPE_OPTIONS } from '@/types/models'
import type {
  AlbumBrief,
  AlbumFuzzyHit,
  AiAnalysisSource,
  ArtistBrief,
  ArtistFuzzyHit,
  DashboardStats,
  GroupBrief,
  GroupFuzzyHit,
  IncomingInfo,
  ScannedFileItem,
  Song,
  SongBrief,
  SongFuzzyHit,
} from '@/types/models'
import { formatDuration, formatFileSize, formatResolution, detectPlatform } from '@/utils/format'
import SaSelect from '@/components/SaSelect.vue'
import type { SaOption } from '@/components/SaSelect.vue'
import VideoTrackList from '@/components/VideoTrackList.vue'
import type { TrackRow } from '@/components/VideoTrackList.vue'
import {
  InboxOutlined,
  RefreshOutlined,
  RobotOutlined,
  FolderOutlined,
  ArrowLeftOutlined,
  SearchOutlined,
  LoadingOutlined,
  InfoOutlined,
  CloseOutlined,
} from '@/components/icons'
import { loadAi, outboundAiCreds, readIngestAiConfig } from '@/views/settings/aiLocal'

const emit = defineEmits<{ gotoAi: [] }>()
const message = useMessage()

// ===== 待整理板块 =====
const stats = ref<DashboardStats | null>(null)
const scanning = ref(false)
const incomingList = ref<ScannedFileItem[]>([])
const incomingLoading = ref(false)
const incomingOpen = ref(false)

async function loadStats() {
  try {
    stats.value = await dashboardApi.stats()
  } catch (e) {
    message.error((e as Error).message)
  }
}

async function loadIncoming() {
  incomingLoading.value = true
  try {
    incomingList.value = await libraryApi.scan()
    await loadStats()
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    incomingLoading.value = false
  }
}

function toggleIncoming() {
  incomingOpen.value = !incomingOpen.value
  if (incomingOpen.value) void loadIncoming()
}

async function doScan() {
  scanning.value = true
  try {
    const stats = await libraryApi.scanSync()
    incomingList.value = await libraryApi.scan()
    await loadStats()
    if (stats.skipped_running) {
      message.info('上一轮扫描仍在进行，已展示当前缓存结果')
    } else if (stats.skipped_unmounted) {
      message.warning('incoming 目录不可访问或为空，未更新缓存')
    } else {
      message.success(
        `扫描完成：新增 ${stats.added}，移除 ${stats.removed}，共 ${incomingList.value.length} 个待整理文件`,
      )
    }
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    scanning.value = false
  }
}

// ===== 博主动态：按发布博主固定视频类型（设置 · 资料库 · 博主管理）=====
// 命中的博主不再由 AI 判断类型；未配置的博主照旧交给 AI。
const uploaderRuleMap = ref<Record<string, string[]>>({})
// 当前待整理文件命中的规则类型（空 = 未命中，由 AI 填）
const uploaderRuleTypes = ref<string[]>([])
const uploaderRuleName = ref('')

async function loadUploaderRules() {
  try {
    const res = await uploadersApi.rules()
    const map: Record<string, string[]> = {}
    for (const r of res.rules || []) {
      const key = (r.name || '').trim().toLowerCase()
      if (key && r.video_types?.length) map[key] = [...r.video_types]
    }
    uploaderRuleMap.value = map
  } catch {
    uploaderRuleMap.value = {}
  }
}

function matchUploaderRule(uploader?: string | null): string[] {
  const key = (uploader || '').trim().toLowerCase()
  if (!key) return []
  return uploaderRuleMap.value[key] || []
}

function applyUploaderRule(uploader?: string | null) {
  const types = matchUploaderRule(uploader)
  uploaderRuleName.value = types.length ? (uploader || '').trim() : ''
  uploaderRuleTypes.value = types
  if (types.length) detailForm.video_types = [...types]
}

const uploaderRuleLabels = computed(() =>
  uploaderRuleTypes.value.map((t) => VIDEO_TYPE_LABEL[t] || t).join('、'),
)

// ===== 待整理详情（主从视图） =====
const activeItem = ref<ScannedFileItem | null>(null)
const detailInfo = ref<IncomingInfo | null>(null)

const infoLoading = ref(false)
const coverFailed = ref(false)
// 本地同名封面（/library/local-cover）加载失败后回退远程缩略图的标志
const localCoverFailed = ref(false)
const aiLoading = ref(false)
const albumAiLoading = ref(false)
const albumAiOpen = ref(false)
const albumAiNames = ref<string[]>([])
const albumAiInfo = ref<{ song?: string; performer?: string }>({})
// 多首歌曲时 AI 逐曲识别的专辑结果（单曲时为空，用 albumAiNames 展示）
const albumAiMulti = ref<{ song: string; albums: string[] }[]>([])
// AI 返回的来源清单（供展示核对）
const albumAiSources = ref<AiAnalysisSource[]>([])
const ingesting = ref(false)
const libraryDirs = ref<string[]>([])
const destPreview = ref<{ relative: string; absolute: string; notice?: string | null }>({ relative: '', absolute: '', notice: null })
const destDirOpen = ref(false)
// 只有用户改过输入框 / 选过目录才视为手动路径；自动预览不能把字段占住
const destRelTouched = ref(false)
// 用户手动编辑过标题（name）后停止自动重建；AI 回填 / 强制重建按钮可重新生成
const titleTouched = ref(false)
const aiFilling = ref(false)

interface DetailForm {
  video_types: string[]
  original_title: string
  name: string
  chinese_name: string
  event_name: string
  performance_date: string
  published_date: string
  source_platform: string
  source_url: string
  source_id: string
  original_uploader: string
  description: string
  chinese_description: string
  is_solo: boolean
  destination_rel: string
  tracks: DraftTrackRow[]
  artist_ids: number[]
  group_ids: number[]
  subject_artist_id: number | null
  duration?: number | null
  width?: number | null
  height?: number | null
  video_codec?: string | null
  audio_codec?: string | null
}

const detailForm = reactive<DetailForm>({
  video_types: ['Other'],
  original_title: '',
  name: '',
  chinese_name: '',
  event_name: '',
  performance_date: '',
  published_date: '',
  source_platform: '',
  source_url: '',
  source_id: '',
  original_uploader: '',
  description: '',
  chinese_description: '',
  is_solo: false,
  destination_rel: '',
  tracks: [{ key: 't-init', song_id: null, album_ids: [] }],
  artist_ids: [],
  group_ids: [],
  subject_artist_id: null,
})

const libraryRoot = ref('')

const destAbsDisplay = computed(() => {
  const rel = detailForm.destination_rel.trim().replace(/\\/g, '/')
  if (!rel) return destPreview.value.absolute || '—'
  const lib = libraryRoot.value.replace(/[\\/]+$/, '')
  return lib ? `${lib}/${rel}` : destPreview.value.absolute || rel
})

function uploadDateToISO(value?: string | null): string {
  if (!value) return ''
  const m = value.match(/^(\d{4})(\d{2})(\d{2})$/)
  return m ? `${m[1]}-${m[2]}-${m[3]}` : value
}

// 详情封面来源：优先本地同名封面（后端 /library/local-cover，零网络开销），
// 本地加载失败自动回退 info.json 里的远程缩略图
const detailCoverSrc = computed(() => {
  if (detailInfo.value?.has_local_cover && activeItem.value && !localCoverFailed.value) {
    return libraryApi.localCoverUrl(activeItem.value.path)
  }
  if (detailInfo.value?.thumbnail && !coverFailed.value) return detailInfo.value.thumbnail
  return ''
})

function onDetailCoverError() {
  if (detailCoverSrc.value.startsWith('/api/library/local-cover')) {
    localCoverFailed.value = true
    return
  }
  coverFailed.value = true
}

async function openDetail(item: ScannedFileItem) {
  activeItem.value = item
  // 换文件即清掉上一个文件的 AI 加载态（其结果由 aiIngest 的 item 守卫丢弃）
  aiLoading.value = false
  aiFilling.value = false
  Object.assign(detailForm, {
    video_types: ['Other'],
    original_title: '',
    name: '',
    chinese_name: '',
    event_name: '',
    performance_date: '',
    published_date: '',
    source_platform: '',
    source_url: '',
    source_id: '',
    original_uploader: '',
    description: '',
    chinese_description: '',
    is_solo: false,
    destination_rel: '',
    tracks: [{ key: `t-${Date.now()}`, song_id: null, album_ids: [] }],
    artist_ids: [],
    group_ids: [],
    subject_artist_id: null,
  })
  destPreview.value = { relative: '', absolute: '', notice: null }
  destRelTouched.value = false
  titleTouched.value = false
  uploaderRuleTypes.value = []
  uploaderRuleName.value = ''
  draftArtistNames.value = []
  draftGroupNames.value = []
  draftSubjectArtistName.value = null
  songPresets.value = []
  manualAlbumPresets.value = []
  songAlbumPresets.value = []
  detailInfo.value = null
  infoLoading.value = true
  coverFailed.value = false
  localCoverFailed.value = false
  try {
    const info = await libraryApi.info(item.path)
    detailInfo.value = info
    Object.assign(detailForm, {
      original_title: info.title || item.file_name,
      name: info.title || item.file_name.replace(/\.[^.]+$/, ''),
      source_platform: detectPlatform(info.webpage_url, info.extractor),
      source_url: info.webpage_url || '',
      source_id: info.id || '',
      original_uploader: info.uploader || '',
      performance_date: '',
      published_date: uploadDateToISO(info.upload_date),
      description: info.description || '',
      duration: item.duration,
      width: item.width,
      height: item.height,
      video_codec: item.video_codec,
      audio_codec: item.audio_codec,
    })
    // is_short 恒由系统自动派生：入库只按 video_types 是否含 ShortVideo（与时长无关），无需手动标记
    // 博主固定类型：命中规则时先选中，AI 之后也不覆盖（详见 applyUploaderRule）
    applyUploaderRule(info.uploader)
    // 加载正式库已有目录，供“选择其他路径”使用
    libraryApi.directories().then((dirs) => {
      libraryDirs.value = dirs
    }).catch(() => {
      libraryDirs.value = []
    })
    await refreshDestPreview()
    // 方案一：库驱动本地匹配（不依赖 AI，专辑来自库内 AlbumTrack 关系）
    void applyLocalMatchHints(item)
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    infoLoading.value = false
  }
}

// ===== 本地确定性预匹配（先建库后入库场景）=====
// 打开详情即从文件名/info.json 识别艺人、歌曲，并按资料库关系带出专辑；
// AI 入库阶段只补本地未命中的空白，不覆盖这里的行
async function applyLocalMatchHints(item: ScannedFileItem) {
  try {
    const hints = await libraryApi.matchHints(item.path)
    if (activeItem.value !== item) return // 用户已切换到其他文件
    const hasAny =
      hints.tracks.length || hints.artists.length || hints.groups.length
    if (!hasAny) {
      if (hints.notices.length) message.info(hints.notices.join('；'))
      return
    }
    aiFilling.value = true // 抑制填充过程中的路径/标题联动抖动
    try {
      for (const a of hints.artists) {
        if (!detailForm.artist_ids.includes(a.id)) detailForm.artist_ids.push(a.id)
        if (!artistPresets.value.some((o) => o.value === a.id)) {
          artistPresets.value.push({
            label: artistLabel(a as ArtistBrief),
            value: a.id,
            tag: '本地匹配',
          })
        }
      }
      for (const g of hints.groups) {
        if (!detailForm.group_ids.includes(g.id)) detailForm.group_ids.push(g.id)
        if (!groupPresets.value.some((o) => o.value === g.id)) {
          groupPresets.value.push({
            label: groupLabel(g as GroupBrief),
            value: g.id,
            tag: '本地匹配',
          })
        }
      }
      for (const al of hints.albums) {
        if (!manualAlbumPresets.value.some((o) => o.value === al.id)) {
          manualAlbumPresets.value.push({
            label: albumLabel(al as AlbumBrief),
            value: al.id,
          })
        }
      }
      if (hints.tracks.length) {
        await fetchSongLabels(hints.tracks.map((t) => t.song_id))
        detailForm.tracks = hints.tracks.map((t, i) => ({
          key: `t-local-${Date.now()}-${i}`,
          song_id: t.song_id,
          album_ids: [...t.album_ids],
          local_matched: true,
        }))
      }
    } finally {
      aiFilling.value = false
    }
    // 填充完成后手动刷新一次（watch 被 aiFilling 抑制了）
    if (!destRelTouched.value) await refreshDestPreview()
    if (!titleTouched.value) void refreshTitle()
    const medium = hints.tracks.filter((t) => t.confidence === 'medium')
    const names = medium.map((t) => t.song_name)
    if (names.length) {
      message.info(`本地匹配：歌曲「${names.join('、')}」仅在简介中命中，请核实`)
    } else if (hints.tracks.length) {
      message.success(
        `本地匹配命中 ${hints.tracks.length} 首歌曲，专辑已按资料库自动关联`,
      )
    }
    if (hints.notices.length) message.info(hints.notices.join('；'))
  } catch {
    // 本地匹配是增强能力，失败静默，不影响手动流程
  }
}

// ===== 入库路径预览 / 选择 =====
function isBilibiliFile(path: string): boolean {
  return /[/\\]incoming-bilibili[/\\]/i.test(path)
}

async function refreshDestPreview(force = false) {
  if (!activeItem.value) return
  const fileName = activeItem.value.file_name
  try {
    const r = await libraryApi.previewPath({
      file_name: fileName,
      group_name: null,
      title: detailForm.name || null,
      video_type: detailForm.video_types[0] || 'Other',
      is_solo: !!detailForm.is_solo,
      duration: detailForm.duration ?? null,
      performance_date: detailForm.performance_date || null,
      song_ids: detailForm.tracks.map((t) => t.song_id).filter((id): id is number => id != null),
      // 「待新建」草稿歌曲也计入串烧判定（与入库时先建歌再算路径同口径）
      draft_song_names: detailForm.tracks
        .map((t) => t.draft_song)
        .filter((n): n is string => !!n),
      // 「待新建」草稿艺人/组合/直拍对象同口径参与归档规则
      // （入库时会先创建实体再算路径，预览不一致会错误平铺根目录）
      draft_artist_names: [...draftArtistNames.value],
      draft_group_names: [...draftGroupNames.value],
      draft_subject_artist_name: draftSubjectArtistName.value || null,
      artist_ids: detailForm.artist_ids,
      group_ids: detailForm.group_ids,
      video_types: detailForm.video_types,
    })
    destPreview.value = { relative: r.relative_path, absolute: r.absolute_path, notice: r.notice ?? null }
    if (force) destRelTouched.value = false
    // 规则被阻断（重名艺人缺中文名 / 无主体关联）时**不回填**目标路径：
    // 目标路径一旦有值，入库就会走「用户手动指定路径」分支，绕过校验并把文件平铺到正式库根目录。
    // 用户自己填过（destRelTouched）则尊重其输入 —— 那正是「手动指定路径」这条合法出路。
    if (r.notice) {
      if (!destRelTouched.value) detailForm.destination_rel = ''
    } else if (!destRelTouched.value) {
      detailForm.destination_rel = r.relative_path
    }
  } catch {
    destPreview.value = { relative: '', absolute: '', notice: null }
  }
}

// ===== 标题自动重建 =====
// 与 AI 标题规范「YYMMDD 艺人 - 歌曲 [活动 类型]」同口径；无关联艺人时后端返回空标题，此时保留用户当前值
async function refreshTitle(force = false) {
  if (!activeItem.value) return
  if (!force && titleTouched.value) return
  try {
    const r = await libraryApi.buildTitle({
      performance_date: detailForm.performance_date || null,
      published_date: detailForm.published_date || null,
      song_ids: detailForm.tracks.map((t) => t.song_id).filter((id): id is number => id != null),
      artist_ids: detailForm.artist_ids,
      group_ids: detailForm.group_ids,
      subject_artist_id: detailForm.subject_artist_id,
      event_name: detailForm.event_name || null,
      video_types: detailForm.video_types,
    })
    if (r.name) {
      detailForm.name = r.name
    }
  } catch {
    // 重建失败时静默保留当前输入，不打断用户编辑
  }
}

// 一键把上传日期同步到表演日期（AI 未识别出表演日期时的快捷操作；同步后仍可手动修改）
function syncPerformanceFromPublished() {
  if (!detailForm.published_date) return
  detailForm.performance_date = detailForm.published_date
}

function applyDestDir(dir: string) {
  const fileName = activeItem.value?.file_name || ''
  const base = dir.replace(/^\/+|\/+$/g, '')
  destRelTouched.value = true
  detailForm.destination_rel = base ? `${base}/${fileName}` : fileName
  destPreview.value = { ...destPreview.value, relative: detailForm.destination_rel }
  destDirOpen.value = false
}

async function loadDestDirs() {
  destDirOpen.value = true
  try {
    libraryDirs.value = await libraryApi.directories()
  } catch {
    libraryDirs.value = []
  }
}

// ===== AI 建议「待新建」草稿 =====
// AI 分析只填草稿，点「入库」时才真正创建，避免错误数据直接进库
const draftArtistNames = ref<string[]>([])
const draftGroupNames = ref<string[]>([])
const draftSubjectArtistName = ref<string | null>(null)

// 标题 / 视频类型 / 短视频标记 / solo 标记 / 非表演标记 / 表演日期 / 关联（歌曲/艺术家/组合）变化时，若用户尚未手动修改入库路径，自动刷新建议路径
watch(
  () => [detailForm.name, detailForm.video_types, detailForm.is_solo, detailForm.performance_date, detailForm.tracks, detailForm.artist_ids, detailForm.group_ids, draftArtistNames, draftGroupNames, draftSubjectArtistName],
  () => {
    if (aiFilling.value) return
    if (activeItem.value && !destRelTouched.value) {
      void refreshDestPreview()
    }
  },
  { deep: true },
)

// 表演日期 / 上传日期 / 曲目 / 艺人 / 组合 / 类型 / 活动 / 直拍对象变化时，若用户尚未手动编辑标题，自动按 AI 标题规范重建
watch(
  () => [detailForm.performance_date, detailForm.published_date, detailForm.tracks, detailForm.artist_ids, detailForm.group_ids, detailForm.video_types, detailForm.event_name, detailForm.subject_artist_id],
  () => {
    if (aiFilling.value) return
    if (activeItem.value && !titleTouched.value) {
      void refreshTitle()
    }
  },
  { deep: true },
)

function closeDetail() {
  activeItem.value = null
  detailInfo.value = null
  // 离开详情即清掉 AI 加载态：残留的 AI 请求由 aiIngest 内的 item 守卫丢弃
  aiLoading.value = false
  aiFilling.value = false
}

// ===== 关联搜索（歌曲 / 专辑 / 艺术家 / 组合） =====
function songLabel(s: SongBrief): string {
  return s.chinese_name ? `${s.name} / ${s.chinese_name}` : s.name
}

function albumLabel(a: AlbumBrief): string {
  return a.chinese_name ? `${a.name} / ${a.chinese_name}` : a.name
}

function artistLabel(a: ArtistBrief): string {
  const parts = [a.name]
  if (a.chinese_name && a.chinese_name !== a.name) parts.push(a.chinese_name)
  if (a.stage_name && a.stage_name !== a.name) parts.push(`(${a.stage_name})`)
  return parts.join(' / ')
}

function groupLabel(g: GroupBrief): string {
  return g.chinese_name ? `${g.name} / ${g.chinese_name}` : g.name
}

/**
 * 专辑远程搜索：关键字同时匹配**专辑名**与**专辑内曲目名** —— 只记得歌名
 * （如 Gee）时也能把收录它的专辑找出来；带上所在曲目行的歌曲，让搜索命中的
 * 选项也给出同一套提示语。
 *
 * 因曲目命中的选项，提示语里的歌曲换成**造成命中的那首歌**（而不是本行的歌），
 * 用户才能确认「是不是这张专辑」；命中曲目本身也会写进本地缓存，避免再逐首回查。
 */
async function searchAlbums(q: string, songId?: number | null): Promise<SaOption[]> {
  if (songId != null) await fetchSongLabels([songId])
  const list = await albumsApi.brief(q || undefined, true)
  rememberAlbumOwners(list)
  for (const a of list) {
    if (a.matched_song_id == null) continue
    if (a.matched_song_name && !songNameMap.value.has(a.matched_song_id)) {
      songNameMap.value.set(a.matched_song_id, a.matched_song_name)
    }
    if (a.matched_song_owner && !songOwnerMap.value.has(a.matched_song_id)) {
      songOwnerMap.value.set(a.matched_song_id, a.matched_song_owner)
    }
  }
  return list.map((a) => {
    const label = albumLabel(a)
    const tag =
      a.matched_song_id != null
        ? albumHintOf(a.matched_song_id, label)
        : albumHintOf(songId, label) ?? albumOwnerFallback(a.id, label)
    return { label, value: a.id, tag }
  })
}

async function searchArtists(q: string): Promise<SaOption[]> {
  const list = await artistsApi.brief(q || undefined)
  return list.map((a) => ({ label: artistLabel(a), value: a.id }))
}

async function searchGroups(q: string): Promise<SaOption[]> {
  const list = await groupsApi.brief(q || undefined)
  return list.map((g) => ({ label: groupLabel(g), value: g.id }))
}

/**
 * 已选值的渲染方式：HeroUI 的多选在触发器里就是一段**纯文本枚举**，
 * 不是一个个小框 —— 所以这里只输出文本，由 CSS 用「、」连接。
 * 逐项删除改走触发器右侧的清除按钮（与 HeroUI 的 ClearButton 行为一致）。
 */
// 已选歌曲的本地名称缓存（用于「专辑提示」中的歌曲名）
const songNameMap = ref(new Map<number, string>())
// 歌曲归属主体（组合 / 艺人）显示名缓存：同名歌曲靠它区分，
// 例如 aespa 的「Supernova」与别家的「Supernova」不能再笼统写成「来自歌曲 Supernova」
const songOwnerMap = ref(new Map<number, string>())
// 专辑**自身**发行主体缓存：候选专辑关联不到任何歌（未被曲目行选中、
// 或选中它的那行歌还是待新建草稿）时，提示语退到「主体 - 专辑」，
// 至少让用户看出这是谁的专辑。来源：所有 /albums/brief 响应（rememberAlbumOwners 统一记）。
const albumOwnerMap = ref(new Map<number, string>())
const songPresets = ref<SaOption[]>([])
const manualAlbumPresets = ref<SaOption[]>([])
const songAlbumPresets = ref<SaOption[]>([])
const artistPresets = ref<SaOption[]>([])
const groupPresets = ref<SaOption[]>([])
/** /albums/brief 响应统一入口：把专辑自身主体记进缓存（有则记，不覆盖）。 */
function rememberAlbumOwners(list: AlbumBrief[]) {
  for (const a of list) {
    if (a.owner && !albumOwnerMap.value.has(a.id)) albumOwnerMap.value.set(a.id, a.owner)
  }
}

/** 拿不到歌曲关联时的兜底提示：「主体 - 专辑」；连专辑主体也没有则不出括号。 */
function albumOwnerFallback(albumId: number | undefined, albumName: string): string | undefined {
  const owner = albumId != null ? albumOwnerMap.value.get(albumId) : undefined
  return owner ? `${owner} - ${albumName}` : undefined
}

/**
 * 专辑下拉选项：手动/匹配/AI 收集来的 + 歌曲关系带出的，**提示语在这里统一重算**。
 *
 * 不在 push 的地方各写各的 tag —— 那是「同一张专辑一会写 AI 建议、一会写本地匹配」
 * 的根因（来源词还取决于哪条链路先把它塞进列表）。统一成
 * 「组合/艺人 - 专辑名 - 歌曲名」，见 albumHintOf()；
 * 关联不到歌的候选退到「主体 - 专辑」，见 albumOwnerFallback()。
 */
const albumPresets = computed<SaOption[]>(() => {
  const seen = new Set<string | number>()
  const out: SaOption[] = []
  for (const o of [...manualAlbumPresets.value, ...songAlbumPresets.value]) {
    if (seen.has(o.value)) continue
    seen.add(o.value)
    const albumId = Number(o.value)
    out.push({
      ...o,
      tag:
        albumHintOf(albumSongMap.value.get(albumId), o.label) ??
        albumOwnerFallback(albumId, o.label),
    })
  }
  return out
})

/**
 * 专辑 → 当前引用它的**第一首歌**，来自曲目行（歌曲 + 已选专辑）。
 *
 * 同一张专辑可能被多行引用（串烧同专辑），此时以第一行为准 —— 与原先
 * refreshAlbumPresets 按专辑去重时保留首次命中的行为一致。
 */
const albumSongMap = computed(() => {
  const map = new Map<number, number>()
  for (const row of detailForm.tracks) {
    if (row.song_id == null) continue
    for (const albumId of row.album_ids) {
      if (!map.has(albumId)) map.set(albumId, row.song_id)
    }
  }
  return map
})

/**
 * 解析歌曲的归属主体显示名。
 *
 * 优先用发行主体（release_artist 多态，库内填充率最高）；缺失时退到
 * SongArtistRelation 的首个关系。两者都没有则返回 null（由调用方降级提示）。
 * 取名口径与后端 `_song_relation_names` 一致：中文名优先，艺人再退艺名。
 */
async function resolveSongOwner(s: Song): Promise<string | null> {
  const kind = s.release_artist_type
  const id = s.release_artist_id
  if (id != null) {
    try {
      if (kind === 'group') {
        const g = await groupsApi.get(id)
        return g.chinese_name || g.name || null
      }
      if (kind === 'artist') {
        const a = await artistsApi.get(id)
        return a.chinese_name || a.stage_name || a.name || null
      }
    } catch {
      // 主体已删除 / 反查失败：退回关系名
    }
  }
  return s.relation_names?.find(Boolean) ?? null
}

async function fetchSongLabels(ids: number[]) {
  for (const id of ids) {
    if (songNameMap.value.has(id)) continue
    try {
      const s = await songsApi.get(id)
      songNameMap.value.set(id, s.name)
      const owner = await resolveSongOwner(s)
      if (owner) songOwnerMap.value.set(id, owner)
    } catch {
      songNameMap.value.set(id, `#${id}`)
    }
  }
}

/**
 * 专辑下拉提示语的**唯一口径**：「组合/艺人 - 专辑名 - 歌曲名」。
 *
 * 只描述「这张专辑归属于谁、对应哪首歌」—— 来源（本地匹配 / AI 建议 / 手动新建）
 * 一律不写进括号：同一个下拉里来源词混着出现时，用户看不出差异、也核对不了信息。
 * 主体取歌曲发行主体（缺失时退到关系名）；连歌曲名都没有（如孤儿 preset）则不出括号
 * —— 调用方再退到 albumOwnerFallback 的「主体 - 专辑」两级兜底。
 */
function albumHintOf(songId: number | null | undefined, albumName: string): string | undefined {
  if (songId == null) return undefined
  return albumHintText(songOwnerMap.value.get(songId), songNameMap.value.get(songId), albumName)
}

/** 「主体 - 专辑名 - 歌曲名」拼装核心（提示语的唯一实现，供 albumHintOf 与远程搜索结果共用）。 */
function albumHintText(
  owner: string | null | undefined,
  songName: string | null | undefined,
  albumName: string,
): string | undefined {
  if (!songName) return undefined
  return owner ? `${owner} - ${albumName} - ${songName}` : `${albumName} - ${songName}`
}

// 选择歌曲后，自动带出数据库中该歌曲已关联的专辑（提示语由 albumPresets 统一补）
async function refreshAlbumPresets() {
  const ids = detailForm.tracks.map((t) => t.song_id).filter((id): id is number => id != null)
  await fetchSongLabels(ids)
  const results: SaOption[] = []
  for (const id of ids) {
    try {
      const list = await albumsApi.songAlbums(id)
      for (const a of list) {
        results.push({ label: albumLabel(a), value: a.id })
      }
    } catch {
      // 忽略单个歌曲查询失败
    }
  }
  const seen = new Set<string | number>()
  songAlbumPresets.value = results.filter((r) => {
    if (seen.has(r.value)) return false
    seen.add(r.value)
    return true
  })
}

watch(
  () => detailForm.tracks.map((t) => t.song_id),
  () => void refreshAlbumPresets(),
)
const albumCreateShow = ref(false)
const albumCreating = ref(false)
const albumCreateRowIndex = ref(0)
const albumCreateForm = ref<{ name: string; chinese_name: string; album_type: string }>({
  name: '',
  chinese_name: '',
  album_type: 'Single',
})

function openAlbumCreate(_target: 'ingest' | 'db' = 'ingest', rowIndex = 0) {
  albumCreateRowIndex.value = rowIndex
  albumCreateForm.value = { name: '', chinese_name: '', album_type: 'Single' }
  albumCreateShow.value = true
}

function applyAlbumToTarget(brief: AlbumBrief) {
  if (!manualAlbumPresets.value.some((o) => o.value === brief.id)) {
    manualAlbumPresets.value.unshift({ label: albumLabel(brief), value: brief.id })
  }
  const row =
    detailForm.tracks[albumCreateRowIndex.value] ||
    detailForm.tracks.find((t) => t.song_id != null) ||
    detailForm.tracks[0]
  if (row && !row.album_ids.includes(brief.id)) {
    row.album_ids.push(brief.id)
  }
}

async function submitAlbumCreate() {
  const name = albumCreateForm.value.name.trim()
  if (!name) {
    message.warning('请填写专辑名')
    return
  }
  const chosen = await askAlbumFuzzy(name)
  if (chosen === 'skip') return
  if (chosen !== 'create') {
    applyAlbumToTarget(chosen)
    albumCreateShow.value = false
    message.success(`已关联库内已有专辑：${albumLabel(chosen)}`)
    return
  }
  albumCreating.value = true
  try {
    const created = await albumsApi.create({
      name,
      chinese_name: albumCreateForm.value.chinese_name.trim() || null,
      album_type: albumCreateForm.value.album_type,
    })
    message.success('已创建专辑')
    const brief: AlbumBrief = {
      id: created.id,
      uid: created.uid,
      name: created.name,
      chinese_name: created.chinese_name,
      album_type: created.album_type,
    }
    applyAlbumToTarget(brief)
    albumCreateShow.value = false
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    albumCreating.value = false
  }
}

// ===== 快速新建艺人 / 组合（含查重；AI 建错了的手动修正路径） =====
const artistCreateShow = ref(false)
const artistCreating = ref(false)
const artistCreateForm = ref<{ name: string; chinese_name: string }>({ name: '', chinese_name: '' })

function openArtistCreate() {
  artistCreateForm.value = { name: '', chinese_name: '' }
  artistCreateShow.value = true
}

function linkArtistToForm(brief: ArtistBrief, tag: string) {
  if (!detailForm.artist_ids.includes(brief.id)) detailForm.artist_ids.push(brief.id)
  if (!artistPresets.value.some((o) => o.value === brief.id)) {
    artistPresets.value.unshift({ label: artistLabel(brief), value: brief.id, tag })
  }
}

async function submitArtistCreate() {
  const name = artistCreateForm.value.name.trim()
  if (!name) {
    message.warning('请填写艺人名')
    return
  }
  const chosen = await askArtistFuzzy(name)
  if (chosen === 'skip') return
  if (chosen !== 'create') {
    linkArtistToForm(chosen, '已存在')
    artistCreateShow.value = false
    message.success(`已关联库内已有艺人：${artistLabel(chosen)}`)
    return
  }
  artistCreating.value = true
  try {
    const created = await artistsApi.create({
      name,
      chinese_name: artistCreateForm.value.chinese_name.trim() || null,
    })
    linkArtistToForm(created, '已新建')
    artistCreateShow.value = false
    message.success('已创建艺人')
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    artistCreating.value = false
  }
}

const groupCreateShow = ref(false)
const groupCreating = ref(false)
const groupCreateForm = ref<{ name: string; chinese_name: string }>({ name: '', chinese_name: '' })

function openGroupCreate() {
  groupCreateForm.value = { name: '', chinese_name: '' }
  groupCreateShow.value = true
}

function linkGroupToForm(brief: GroupBrief, tag: string) {
  if (!detailForm.group_ids.includes(brief.id)) detailForm.group_ids.push(brief.id)
  if (!groupPresets.value.some((o) => o.value === brief.id)) {
    groupPresets.value.unshift({ label: groupLabel(brief), value: brief.id, tag })
  }
}

async function submitGroupCreate() {
  const name = groupCreateForm.value.name.trim()
  if (!name) {
    message.warning('请填写组合名')
    return
  }
  const chosen = await askGroupFuzzy(name)
  if (chosen === 'skip') return
  if (chosen !== 'create') {
    linkGroupToForm(chosen, '已存在')
    groupCreateShow.value = false
    message.success(`已关联库内已有组合：${groupLabel(chosen)}`)
    return
  }
  groupCreating.value = true
  try {
    const created = await groupsApi.create({
      name,
      chinese_name: groupCreateForm.value.chinese_name.trim() || null,
    })
    linkGroupToForm(created, '已新建')
    groupCreateShow.value = false
    message.success('已创建组合')
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    groupCreating.value = false
  }
}

// ===== 专辑查重提示（模糊搜索相似名，供新建前/ AI 建议时确认） =====
const albumFuzzyOpen = ref(false)
const albumFuzzyTitle = ref('')
const albumFuzzyLoading = ref(false)
const albumFuzzyCandidates = ref<AlbumFuzzyHit[]>([])
const albumFuzzyResolve = ref<((v: AlbumFuzzyHit | 'create' | 'skip') => void) | null>(null)

function closeAlbumFuzzy(v: AlbumFuzzyHit | 'create' | 'skip') {
  albumFuzzyOpen.value = false
  const r = albumFuzzyResolve.value
  albumFuzzyResolve.value = null
  r?.(v)
}

const artistFuzzyOpen = ref(false)
const artistFuzzyTitle = ref('')
const artistFuzzyLoading = ref(false)
const artistFuzzyCandidates = ref<ArtistFuzzyHit[]>([])
const artistFuzzyResolve = ref<((v: ArtistFuzzyHit | 'create' | 'skip') => void) | null>(null)

function closeArtistFuzzy(v: ArtistFuzzyHit | 'create' | 'skip') {
  artistFuzzyOpen.value = false
  const r = artistFuzzyResolve.value
  artistFuzzyResolve.value = null
  r?.(v)
}

async function askArtistFuzzy(
  name: string,
  allowedIds?: Set<number> | null,
): Promise<ArtistFuzzyHit | 'create' | 'skip'> {
  artistFuzzyLoading.value = true
  let candidates: ArtistFuzzyHit[] = []
  try {
    candidates = await artistsApi.fuzzy(name)
    if (allowedIds) candidates = candidates.filter((c) => allowedIds.has(c.id))
  } catch {
    return 'create'
  } finally {
    artistFuzzyLoading.value = false
  }
  const exact = candidates.find((c) => (c.score || 0) >= 0.999)
  if (exact) return exact
  if (!candidates.length) return 'create'
  return new Promise((resolve) => {
    artistFuzzyTitle.value = name
    artistFuzzyCandidates.value = candidates
    artistFuzzyResolve.value = resolve
    artistFuzzyOpen.value = true
  })
}

async function askAlbumFuzzy(name: string): Promise<AlbumFuzzyHit | 'create' | 'skip'> {
  albumFuzzyLoading.value = true
  let candidates: AlbumFuzzyHit[] = []
  try {
    candidates = await albumsApi.fuzzy(name)
  } catch {
    return 'create'
  } finally {
    albumFuzzyLoading.value = false
  }
  const exact = candidates.find((c) => (c.score || 0) >= 0.999)
  if (exact) return exact
  // 高置信且无歧义（第二名明显更低）→ 自动关联，不打断用户
  const [top, second] = candidates
  if (
    top &&
    (top.score || 0) >= 0.9 &&
    (!second || (second.score || 0) < 0.9)
  ) {
    return top
  }
  if (!candidates.length) return 'create'
  return new Promise((resolve) => {
    albumFuzzyTitle.value = name
    albumFuzzyCandidates.value = candidates
    albumFuzzyResolve.value = resolve
    albumFuzzyOpen.value = true
  })
}

const songFuzzyOpen = ref(false)
const songFuzzyTitle = ref('')
const songFuzzyLoading = ref(false)
const songFuzzyCandidates = ref<SongFuzzyHit[]>([])
const songFuzzyResolve = ref<((v: SongFuzzyHit | 'create' | 'skip') => void) | null>(null)

function closeSongFuzzy(v: SongFuzzyHit | 'create' | 'skip') {
  songFuzzyOpen.value = false
  const r = songFuzzyResolve.value
  songFuzzyResolve.value = null
  r?.(v)
}

async function askSongFuzzy(name: string): Promise<SongFuzzyHit | 'create' | 'skip'> {
  songFuzzyLoading.value = true
  let candidates: SongFuzzyHit[] = []
  try {
    candidates = await songsApi.fuzzy(name)
  } catch {
    return 'create'
  } finally {
    songFuzzyLoading.value = false
  }
  const exact = candidates.find((c) => (c.score || 0) >= 0.999)
  if (exact) return exact
  // 高置信且无歧义（第二名明显更低）→ 自动关联，不打断用户
  const [top, second] = candidates
  if (
    top &&
    (top.score || 0) >= 0.9 &&
    (!second || (second.score || 0) < 0.9)
  ) {
    return top
  }
  if (!candidates.length) return 'create'
  return new Promise((resolve) => {
    songFuzzyTitle.value = name
    songFuzzyCandidates.value = candidates
    songFuzzyResolve.value = resolve
    songFuzzyOpen.value = true
  })
}

const groupFuzzyOpen = ref(false)
const groupFuzzyTitle = ref('')
const groupFuzzyLoading = ref(false)
const groupFuzzyCandidates = ref<GroupFuzzyHit[]>([])
const groupFuzzyResolve = ref<((v: GroupFuzzyHit | 'create' | 'skip') => void) | null>(null)

function closeGroupFuzzy(v: GroupFuzzyHit | 'create' | 'skip') {
  groupFuzzyOpen.value = false
  const r = groupFuzzyResolve.value
  groupFuzzyResolve.value = null
  r?.(v)
}

async function askGroupFuzzy(name: string): Promise<GroupFuzzyHit | 'create' | 'skip'> {
  groupFuzzyLoading.value = true
  let candidates: GroupFuzzyHit[] = []
  try {
    candidates = await groupsApi.fuzzy(name)
  } catch {
    return 'create'
  } finally {
    groupFuzzyLoading.value = false
  }
  const exact = candidates.find((c) => (c.score || 0) >= 0.999)
  if (exact) return exact
  if (!candidates.length) return 'create'
  return new Promise((resolve) => {
    groupFuzzyTitle.value = name
    groupFuzzyCandidates.value = candidates
    groupFuzzyResolve.value = resolve
    groupFuzzyOpen.value = true
  })
}

// ===== 视频数据（自动从 json 提取展示） =====
const videoDataLines = computed<string[]>(() => {
  const lines: string[] = []
  const info = detailInfo.value
  const it = activeItem.value
  const w = info?.width ?? it?.width
  const h = info?.height ?? it?.height
  if (w || h) lines.push(`分辨率 ${formatResolution(w ?? undefined, h ?? undefined)}`)
  const dur = info?.duration ?? it?.duration
  if (dur) lines.push(`时长 ${formatDuration(dur)}`)
  const size = it?.file_size ?? info?.filesize_approx
  if (size) lines.push(`大小 ${formatFileSize(size)}`)
  if (info?.fps) lines.push(`${info.fps} fps`)
  const vc = it?.video_codec ?? info?.vcodec
  const ac = it?.audio_codec ?? info?.acodec
  if (vc || ac) lines.push([vc, ac].filter(Boolean).join(' / '))
  return lines.length ? lines : ['暂无视频数据']
})

// 行式展示用的文本：视频数据压成一行、简介取表单里的来源原文
const videoDataSummary = computed(() => videoDataLines.value.join(' · '))
const descText = computed(() => detailForm.description || '')

/* ===== 信息预览卡（v3.5.4，按用户澄清的口径）=====
 * ⚠ 口径：预览的是**右侧「编辑元数据」表单将要入库的内容**（视频类型 / 标题 / 平台 /
 *   日期 / 舞台 / 曲目 / 艺术家 / 组合 / 简介 / 入库路径），**不是**左侧 info.json 的
 *   原始元数据（那是「视频信息」卡本身在展示的）。
 *   诉求场景：手机窄窗口下表单被压缩 —— 多选 chip 挤成一团、日期两列并排、简介框很小；
 *   用 HeroUI 风格卡片把**填写结果**一行一项列出来，长内容换行、不截断不省略。
 * 名称类字段（歌曲/专辑/艺人/组合）库里只存 id → 打开时按需拉一次名称并缓存，
 * 拉不到时以 `#id` 占位（结构仍可读，不阻塞打开）。 */
const previewOpen = ref(false)

type PreviewLabelKind = 'song' | 'album' | 'artist' | 'group'
const previewLabelCache = reactive<Record<string, string>>({})

function previewLabel(kind: PreviewLabelKind, id: number): string {
  return previewLabelCache[`${kind}:${id}`] || `#${id}`
}

/** 实体 → 展示名：名字优先，中文名与名字不同才并列（与歌/专辑下路口径一致） */
function entityDisplayName(e?: { name?: string | null; chinese_name?: string | null } | null): string {
  if (!e?.name) return ''
  return e.chinese_name && e.chinese_name !== e.name ? `${e.name} / ${e.chinese_name}` : e.name
}

/** 打开预览时补齐 id → 名称，只拉缺失项（一条素材量级很小，够用且无需新接口） */
async function loadPreviewLabels() {
  const jobs: { kind: PreviewLabelKind; id: number }[] = []
  const need = (kind: PreviewLabelKind, id: number | null | undefined) => {
    if (id == null) return
    if (!previewLabelCache[`${kind}:${id}`]) jobs.push({ kind, id })
  }
  for (const row of detailForm.tracks) {
    need('song', row.song_id)
    for (const id of row.album_ids || []) need('album', id)
  }
  for (const id of detailForm.artist_ids) need('artist', id)
  for (const id of detailForm.group_ids) need('group', id)
  await Promise.all(
    jobs.map(async ({ kind, id }) => {
      try {
        const name =
          kind === 'song'
            ? entityDisplayName(await songsApi.get(id))
            : kind === 'album'
              ? entityDisplayName(await albumsApi.get(id))
              : kind === 'artist'
                ? entityDisplayName(await artistsApi.get(id))
                : entityDisplayName(await groupsApi.get(id))
        if (name) previewLabelCache[`${kind}:${id}`] = name
      } catch {
        /* 拉不到就保持 #id 占位 */
      }
    }),
  )
}

const previewRows = computed(() => {
  const rows: { k: string; v: string; mono?: boolean }[] = []
  const push = (k: string, v?: string | number | null, mono = false) => {
    if (v === null || v === undefined || v === '') return
    rows.push({ k, v: String(v), mono })
  }

  push('视频类型', detailForm.video_types.map((v) => VIDEO_TYPE_LABEL[v] || v).join(' / '))
  push('Solo（独立艺人）', detailForm.is_solo ? '是' : '否')
  push('标题', detailForm.name)
  push('视频平台', detailForm.source_platform)
  push('上传日期', detailForm.published_date)
  push('表演日期', detailForm.performance_date)
  push('舞台 / 活动', detailForm.event_name)

  // 曲目：一行一曲，歌名 + 专辑 + 待新建项都写全（含本地匹配/AI 草稿）
  const trackLines: string[] = []
  detailForm.tracks.forEach((row, i) => {
    const draftAlbums = row.draft_albums || []
    if (row.song_id == null && !(row.album_ids || []).length && !row.draft_song && !draftAlbums.length) return
    const parts: string[] = []
    if (row.song_id != null) parts.push(previewLabel('song', row.song_id))
    else if (row.draft_song) parts.push(`＋ 新建歌曲「${row.draft_song}」`)
    else parts.push('未选歌曲')
    const albums = [
      ...(row.album_ids || []).map((id) => previewLabel('album', id)),
      ...draftAlbums.map((n) => `＋ 新建「${n}」`),
    ]
    if (albums.length) parts.push(`专辑：${albums.join('、')}`)
    trackLines.push(`曲目 ${i + 1}：${parts.join('　·　')}`)
  })
  push('曲目', trackLines.join('\n'))

  push(
    '艺术家',
    [
      ...detailForm.artist_ids.map((id) => previewLabel('artist', id)),
      ...draftArtistNames.value.map((n) => `＋ 新建 ${n}`),
    ].join('、'),
  )
  push(
    '组合',
    [
      ...detailForm.group_ids.map((id) => previewLabel('group', id)),
      ...draftGroupNames.value.map((n) => `＋ 新建 ${n}`),
    ].join('、'),
  )
  push('中文简介', detailForm.chinese_description)
  push('入库路径', detailForm.destination_rel, true)
  push('入库后位置', destAbsDisplay.value, true)
  return rows
})

/* 预览卡关闭：点遮罩 / 点 ✕ / Esc（桌面习惯）；只在打开期间挂监听 */
function onPreviewKey(e: KeyboardEvent) {
  if (e.key === 'Escape') previewOpen.value = false
}
watch(previewOpen, (open) => {
  if (open) {
    window.addEventListener('keydown', onPreviewKey)
    void loadPreviewLabels()
  } else {
    window.removeEventListener('keydown', onPreviewKey)
  }
})
onBeforeUnmount(() => window.removeEventListener('keydown', onPreviewKey))

// 简介折叠：默认两行，只有真的超出两行时才给「展开/收起」入口
const descExpanded = ref(false)
const descOverflow = ref(false)
const descRef = ref<HTMLElement | null>(null)

function measureDesc() {
  const el = descRef.value
  if (!el) {
    descOverflow.value = false
    return
  }
  // 仅在折叠态测量：展开后 scrollHeight == clientHeight，会把状态判成"没超出"
  if (descExpanded.value) return
  descOverflow.value = el.scrollHeight > el.clientHeight + 1
}

function toggleDesc() {
  if (!descOverflow.value) return
  descExpanded.value = !descExpanded.value
}

// 换文件 / 简介变化时回到折叠态并重新测量
watch(
  [() => descText.value, () => activeItem.value?.path],
  async () => {
    descExpanded.value = false
    await nextTick()
    measureDesc()
  },
  { immediate: true },
)

interface AiRelBrief {
  id: number
  name: string
}

// ===== AI 建议「待新建」草稿 =====
// AI 分析只填草稿，点「入库」时才真正创建，避免错误数据直接进库
// （声明需在上方 watch 之前，watch 注册时即会读取这些 ref）

// 曲目行草稿：待新建歌曲名 / 待新建专辑名（挂在对应曲目行上）
interface DraftTrackRow extends TrackRow {
  draft_song?: string | null
  draft_albums?: string[]
  // 本地匹配命中（来自资料库关系，AI 建议只补空白不覆盖）
  local_matched?: boolean
}

/**
 * AI 建议关联：精确匹配 → 关联已有；有相似候选 → 弹窗换选；
 * 都不是 / 无候选 → 返回 'draft'（挂为「待新建」草稿，入库时才创建）。
 * 不再在 AI 阶段直接创建任何实体。
 */
async function linkOrCreateAiRel<T extends AiRelBrief>(
  name: string,
  search: (q: string) => Promise<T[]>,
  labelOf: (item: T) => string,
  ids: number[],
  presets: SaOption[],
  fuzzy?: (name: string) => Promise<T | 'create' | 'skip'>,
  allowedIds?: Set<number> | null,
): Promise<'linked' | 'draft' | 'skipped' | 'error'> {
  const n = name.trim()
  if (!n) return 'error'
  const lower = n.toLowerCase()
  let item: T | undefined
  try {
    const list = await search(n)
    item = list.find((it) => {
      if ((it.name || '').toLowerCase() === lower) return true
      const extra = it as {
        chinese_name?: string | null
        stage_name?: string | null
        english_name?: string | null
        korean_name?: string | null
      }
      return [extra.chinese_name, extra.stage_name, extra.english_name, extra.korean_name].some(
        (v) => v && v.toLowerCase() === lower,
      )
    })
  } catch {
    item = undefined
  }
  const linkTo = (hit: T, tag: string) => {
    if (!ids.includes(hit.id)) ids.push(hit.id)
    if (!presets.some((o) => o.value === hit.id)) {
      presets.push({ label: labelOf(hit), value: hit.id, tag })
    }
  }
  try {
    if (item && allowedIds && !allowedIds.has(item.id)) return 'skipped'
    if (!item) {
      if (fuzzy) {
        const chosen = await fuzzy(n)
        if (chosen === 'skip') return 'skipped'
        if (chosen === 'create') return 'draft'
        if (allowedIds && !allowedIds.has(chosen.id)) return 'skipped'
        linkTo(chosen, 'AI 建议')
        return 'linked'
      }
      return 'draft'
    }
    linkTo(item, 'AI 建议')
    return 'linked'
  } catch {
    return 'error'
  }
}

async function applyAiRelationSuggestions(
  artists?: string[] | null,
  groups?: string[] | null,
) {
  const drafted: string[] = []
  const failed: string[] = []
  const skipped: string[] = []
  const run = async <T extends AiRelBrief>(
    names: string[] | null | undefined,
    search: (q: string) => Promise<T[]>,
    labelOf: (item: T) => string,
    ids: number[],
    presets: SaOption[],
    fuzzy: (name: string) => Promise<T | 'create' | 'skip'>,
    draftTarget: 'artist' | 'group',
    allowedIds?: Set<number> | null,
  ) => {
    if (!Array.isArray(names)) return
    for (const nm of names) {
      const r = await linkOrCreateAiRel<T>(
        nm,
        search,
        labelOf,
        ids,
        presets,
        fuzzy,
        allowedIds,
      )
      const name = nm.trim()
      if (r === 'draft') {
        const list = draftTarget === 'artist' ? draftArtistNames : draftGroupNames
        if (name && !list.value.includes(name)) list.value.push(name)
        drafted.push(name)
      } else if (r === 'error') failed.push(name)
      else if (r === 'skipped') skipped.push(name)
    }
  }
  // 先挂组合，严格档才能用成员关系闸过滤艺人
  await run(
    groups,
    (q) => groupsApi.brief(q),
    (g) => groupLabel(g),
    detailForm.group_ids,
    groupPresets.value,
    (n) => askGroupFuzzy(n),
    'group',
  )
  // 自动关联策略固定为严格 → 组合相关艺人过滤恒定生效（原来这里还判断 match_mode）
  let allowedArtists: Set<number> | null = null
  if (detailForm.group_ids.length) {
    try {
      const rel = await groupsApi.relatedArtists(detailForm.group_ids)
      allowedArtists = new Set(rel.artist_ids || [])
    } catch {
      allowedArtists = new Set()
    }
  }
  await run(
    artists,
    (q) => artistsApi.brief(q),
    (a) => artistLabel(a),
    detailForm.artist_ids,
    artistPresets.value,
    (n) => askArtistFuzzy(n, allowedArtists),
    'artist',
    allowedArtists,
  )
  if (drafted.length) {
    const shown = drafted.slice(0, 5).join('、')
    message.info(
      `以下内容标注为「待新建」，入库时才会创建：${shown}${drafted.length > 5 ? ' 等' : ''}`,
    )
  }
  if (failed.length) {
    const shown = failed.slice(0, 5).join('、')
    message.warning(`以下关联处理失败：${shown}${failed.length > 5 ? ' 等' : ''}`)
  }
  if (skipped.length) {
    const shown = skipped.slice(0, 5).join('、')
    message.info(`未关联：${shown}${skipped.length > 5 ? ' 等' : ''}（已跳过，可稍后手动处理）`)
  }
}

function applyFilledText(key: 'name' | 'chinese_name' | 'event_name' | 'performance_date' | 'chinese_description', value: unknown) {
  if (typeof value === 'string' && value.trim()) {
    detailForm[key] = value.trim()
  }
}

async function aiIngest() {
  const cfg = readIngestAiConfig()
  if (!cfg) {
    const loaded = loadAi()
    if (loaded.base_url && loaded.model && !loaded.enabled) {
      message.warning('入库 AI 未启用，请到设置里打开「启用入库 AI」')
    } else {
      message.warning('请先在「入库 AI 设置」中填写 API Base URL 与模型')
    }
    emit('gotoAi')
    return
  }
  aiLoading.value = true
  aiFilling.value = true
  // 守卫：AI 请求耗时较长，期间用户可能退回列表或打开另一个视频。
  // 表单是共享的，返回结果必须校验还是同一个文件，否则会填进新视频的表单。
  const item = activeItem.value
  const stillSameItem = () => item != null && activeItem.value?.path === item.path
  try {
    const info = { ...(detailInfo.value || {}) } as Record<string, unknown>
    const desc = info.description
    if (typeof desc === 'string' && desc.length > 2000) {
      info.description = `${desc.slice(0, 2000)}…`
    }
    const aiContext = {
      ...info,
      file_name: activeItem.value?.file_name || '',
      duration: detailForm.duration ?? activeItem.value?.duration ?? null,
      width: detailForm.width ?? activeItem.value?.width ?? null,
      height: detailForm.height ?? activeItem.value?.height ?? null,
      fps: detailInfo.value?.fps ?? null,
      video_codec: detailForm.video_codec ?? activeItem.value?.video_codec ?? null,
      audio_codec: detailForm.audio_codec ?? activeItem.value?.audio_codec ?? null,
    }
    const suggestion = await aiApi.suggest({
      ...outboundAiCreds(cfg),
      context: aiContext,
      current: { ...detailForm },
    })
    if (!stillSameItem()) {
      // 用户已退回列表 / 切换到其他视频：丢弃本次 AI 结果，避免串填
      message.info('已离开当前视频，本次 AI 建议已丢弃')
      return
    }
    applyFilledText('name', suggestion.name)
    applyFilledText('chinese_name', suggestion.chinese_name)
    applyFilledText('event_name', suggestion.event_name)
    applyFilledText('performance_date', suggestion.performance_date)
    applyFilledText('chinese_description', suggestion.chinese_description)
    if (typeof suggestion.name === 'string' && suggestion.name.trim()) {
      titleTouched.value = true
    }
    if (typeof suggestion.is_solo === 'boolean') {
      detailForm.is_solo = suggestion.is_solo
    }
    if (Array.isArray(suggestion.video_types) && suggestion.video_types.length) {
      detailForm.video_types = suggestion.video_types.filter((v): v is string => Boolean(v))
    } else if (typeof suggestion.video_type === 'string' && suggestion.video_type) {
      detailForm.video_types = [suggestion.video_type]
    } else if (!detailForm.video_types || detailForm.video_types.length === 0) {
      detailForm.video_types = ['Other']
    }
    // 博主规则优先：命中「博主管理」里固定类型的博主时，类型以规则为准（后端同样会覆盖）
    if (uploaderRuleTypes.value.length) {
      detailForm.video_types = [...uploaderRuleTypes.value]
    }
    await applyAiRelationSuggestions(suggestion.suggested_artists, suggestion.suggested_groups)
    await applySuggestedTracks(
      suggestion.suggested_tracks,
      suggestion.suggested_songs,
      suggestion.suggested_albums,
    )
    if (typeof suggestion.subject_artist_name === 'string' && suggestion.subject_artist_name.trim()) {
      await applySubjectArtist(suggestion.subject_artist_name.trim())
    }
    if (suggestion.notice) {
      message.warning(suggestion.notice)
    }
    message.success(
      uploaderRuleTypes.value.length
        ? `AI 建议已填充（视频类型按博主规则固定为「${uploaderRuleLabels.value}」），请核对后入库`
        : 'AI 建议已填充，请核对后入库',
    )
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    // 只有还停留在同一个文件时才动全局加载标志，避免残留请求干扰新打开的文件
    if (stillSameItem()) {
      aiFilling.value = false
      aiLoading.value = false
      if (activeItem.value && !destRelTouched.value) {
        void refreshDestPreview()
      }
    }
  }
}

async function aiSearchAlbum() {
  const cfg = readIngestAiConfig()
  if (!cfg) {
    message.warning('请先在「入库 AI 设置」中填写 API Base URL 与模型')
    emit('gotoAi')
    return
  }
  // 以当前表单为准，而非 AI 入库时的名称快照：
  // 歌曲来自曲目栏（song_id → 名称），艺人/组合来自已选下拉项
  const songIds = detailForm.tracks
    .map((t) => t.song_id)
    .filter((id): id is number => id != null)
  await fetchSongLabels(songIds)
  const song_names = songIds
    .map((id) => songNameMap.value.get(id))
    .filter((n): n is string => {
      if (!n) return false
      return !n.startsWith('#')
    })
  const artist_names: string[] = []
  for (const id of detailForm.artist_ids) {
    try {
      artist_names.push(artistLabel(await artistsApi.get(id)))
    } catch {
      // 单个反查失败不影响其他项
    }
  }
  const group_names: string[] = []
  for (const id of detailForm.group_ids) {
    try {
      group_names.push(groupLabel(await groupsApi.get(id)))
    } catch {
      // 单个反查失败不影响其他项
    }
  }
  albumAiLoading.value = true
  try {
    const result = await aiApi.searchAlbums({
      ...outboundAiCreds(cfg),
      context: { ...(detailInfo.value || {}) },
      current: { ...detailForm },
      song_names,
      artist_names,
      group_names,
    })
    const songName = (result.song_name || '').trim()
    const performer = (result.performer || '').trim()
    const names = (result.suggested_albums || []).map((n) => n.trim()).filter(Boolean)
    const multi = (result.multi_songs || []).filter(
      (m) => m && m.song && Array.isArray(m.albums) && m.albums.length > 0,
    )
    albumAiInfo.value = { song: songName, performer }
    albumAiMulti.value = multi
    albumAiSources.value = result.sources || []
    if (multi.length) {
      albumAiOpen.value = true
      return
    }
    if (!names.length) {
      if (!songName && !performer) {
        message.info('AI 未能识别出歌曲与所属专辑，建议先运行「AI 入库」获取歌曲/组合名后再搜索')
      } else {
        albumAiOpen.value = true
        albumAiNames.value = []
      }
      return
    }
    albumAiNames.value = names
    albumAiOpen.value = true
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    albumAiLoading.value = false
  }
}

async function applySubjectArtist(name: string) {
  const ids: number[] = []
  const r = await linkOrCreateAiRel<ArtistBrief>(
    name,
    (q) => artistsApi.brief(q),
    artistLabel,
    ids,
    artistPresets.value,
    (n) => askArtistFuzzy(n),
  )
  if (ids[0]) {
    detailForm.subject_artist_id = ids[0]
    draftSubjectArtistName.value = null
  }
  if (r === 'linked') message.success(`已关联直拍对象：${name}`)
  else if (r === 'draft') {
    draftSubjectArtistName.value = name
    message.info(`直拍对象「${name}」标注为待新建，入库时才会创建`)
  }
}

async function applySuggestedTracks(
  items?: { song: string; albums?: string[] }[] | null,
  fallbackSongs?: string[] | null,
  fallbackAlbums?: string[] | null,
) {
  const failed: string[] = []
  const drafted: string[] = []

  // 关联已有歌曲；库里没有则返回 null（歌曲名挂为待新建草稿）
  const resolveSong = async (name: string): Promise<number | null> => {
    const ids: number[] = []
    const r = await linkOrCreateAiRel(
      name,
      (q) => songsApi.brief(q),
      songLabel,
      ids,
      songPresets.value,
      (n) => askSongFuzzy(n),
    )
    if (r === 'draft') drafted.push(name.trim())
    else if (r === 'error') failed.push(name.trim())
    const sid = ids[0] ?? null
    if (sid) {
      const label = songPresets.value.find((o) => o.value === sid)?.label || name
      songNameMap.value.set(sid, label)
    }
    return sid
  }

  // 专辑：关联已有的；没有的挂为待新建草稿
  const resolveAlbums = async (
    names: string[],
  ): Promise<{ albumIds: number[]; draftAlbums: string[] }> => {
    const ids: number[] = []
    const drafts: string[] = []
    for (const n of names) {
      const r = await linkOrCreateAiRel(
        n,
        (q) =>
          albumsApi.brief(q).then((l) => {
            rememberAlbumOwners(l)
            return l
          }),
        albumLabel,
        ids,
        manualAlbumPresets.value,
        (nm) => askAlbumFuzzy(nm),
      )
      if (r === 'draft') {
        if (!drafts.includes(n.trim())) drafts.push(n.trim())
      } else if (r === 'error') failed.push(n.trim())
    }
    return { albumIds: ids, draftAlbums: drafts }
  }

  const rows: DraftTrackRow[] = []
  const sourceItems =
    items && items.length
      ? items
      : (fallbackSongs || []).map((song) => ({
          song,
          albums:
            (fallbackAlbums || []).length === 1 || (fallbackSongs || []).length === 1
              ? fallbackAlbums || []
              : [],
        }))
  // 已有曲目行（本地匹配 / 用户手动选的）保留，AI 只补空白不覆盖
  const keepRows = detailForm.tracks.filter(
    (t) =>
      t.local_matched ||
      t.song_id != null ||
      !!t.draft_song ||
      (t.draft_albums && t.draft_albums.length > 0),
  )
  const existingSongIds = new Set(
    keepRows.map((t) => t.song_id).filter((id): id is number => id != null),
  )
  for (const it of sourceItems) {
    const songId = await resolveSong(it.song)
    // 该歌曲已有行（本地匹配或用户已选）：只在行内没有专辑时补 AI 建议的专辑
    if (songId != null && existingSongIds.has(songId)) {
      const row = keepRows.find((t) => t.song_id === songId)
      if (row && row.album_ids.length === 0) {
        const { albumIds, draftAlbums } = await resolveAlbums(it.albums || [])
        row.album_ids = albumIds
        row.draft_albums = draftAlbums
      }
      continue
    }
    // 歌曲未关联上也保留行：挂为待新建草稿，入库时创建
    const { albumIds, draftAlbums } = await resolveAlbums(it.albums || [])
    rows.push({
      key: `t-${Date.now()}-${rows.length}`,
      song_id: songId,
      album_ids: albumIds,
      draft_song: songId == null ? it.song.trim() : null,
      draft_albums: draftAlbums,
    })
  }
  const merged = [...keepRows, ...rows]
  if (merged.length) detailForm.tracks = merged
  if (drafted.length) {
    const shown = drafted.slice(0, 5).join('、')
    message.info(
      `以下标注为「待新建」，入库时才会创建：${shown}${drafted.length > 5 ? ' 等' : ''}`,
    )
  }
  if (failed.length) {
    message.warning(`以下关联处理失败：${failed.slice(0, 5).join('、')}`)
  }
}

async function albumAiLink(name: string, song?: string) {
  const ids: number[] = []
  const r = await linkOrCreateAiRel<AlbumBrief>(
    name,
    (q) =>
      albumsApi.brief(q).then((l) => {
        rememberAlbumOwners(l)
        return l
      }),
    albumLabel,
    ids,
    manualAlbumPresets.value,
    (n) => askAlbumFuzzy(n),
  )
  // 定位目标曲目行（按歌曲名匹配，或首个空行）
  let candidates = detailForm.tracks
  if (song) {
    const matched = detailForm.tracks.filter(
      (t) => t.song_id != null && songNameMap.value.get(t.song_id) === song,
    )
    if (matched.length) candidates = matched
    else {
      message.warning(`未找到「${song}」对应的曲目行，未关联专辑`)
      return
    }
  }
  const row =
    candidates.find((t) => t.song_id != null && t.album_ids.length === 0) ||
    candidates.find((t) => t.song_id != null) ||
    candidates[0]
  if (r === 'draft') {
    // 库内没有该专辑：挂为待新建草稿，入库时创建
    if (row) {
      const drafts = row.draft_albums || []
      const trimmed = name.trim()
      if (trimmed && !drafts.includes(trimmed)) {
        row.draft_albums = [...drafts, trimmed]
      }
    }
    message.info(`专辑「${name}」标注为待新建，入库时才会创建`)
    return
  }
  if (ids[0] && row && !row.album_ids.includes(ids[0])) row.album_ids.push(ids[0])
  if (r === 'linked') message.success(`已关联专辑：${name}`)
  else if (r === 'skipped') message.info(`已跳过：${name}`)
  else if (r === 'error') message.error(`关联失败：${name}`)
}

// ===== 草稿落库（入库前调用） =====
// 创建失败时尝试模糊匹配已有记录（可能被其他人/其他入库并发建了同名实体）；
// 返回 null 表示彻底失败，调用方中止入库
async function createOrFuzzyLink(
  create: () => Promise<{ id: number }>,
  fuzzy: () => Promise<{ id: number; score?: number }[]>,
): Promise<number | null> {
  try {
    return (await create()).id
  } catch {
    try {
      const hits = await fuzzy()
      const exact = hits.find((h) => (h.score || 0) >= 0.999)
      return exact ? exact.id : null
    } catch {
      return null
    }
  }
}

/**
 * 把「待新建」草稿真正创建并关联：艺人 → 组合 → 直拍对象 → 歌曲（曲目行）→ 专辑。
 * 任一项创建失败则中止入库（已成功创建的会保留，重试不会重复创建）。
 */
async function materializeDrafts(): Promise<boolean> {
  const failures: string[] = []

  // 艺人草稿
  for (let i = 0; i < draftArtistNames.value.length; ) {
    const name = draftArtistNames.value[i]
    const id = await createOrFuzzyLink(
      () => artistsApi.create({ name }),
      () => artistsApi.fuzzy(name),
    )
    if (id == null) {
      failures.push(`艺人「${name}」`)
      break
    }
    if (!detailForm.artist_ids.includes(id)) detailForm.artist_ids.push(id)
    draftArtistNames.value.splice(i, 1)
  }

  // 组合草稿
  for (let i = 0; i < draftGroupNames.value.length; ) {
    const name = draftGroupNames.value[i]
    const id = await createOrFuzzyLink(
      () => groupsApi.create({ name }),
      () => groupsApi.fuzzy(name),
    )
    if (id == null) {
      failures.push(`组合「${name}」`)
      break
    }
    if (!detailForm.group_ids.includes(id)) detailForm.group_ids.push(id)
    draftGroupNames.value.splice(i, 1)
  }

  // 直拍对象草稿
  if (draftSubjectArtistName.value) {
    const name = draftSubjectArtistName.value
    const id = await createOrFuzzyLink(
      () => artistsApi.create({ name }),
      () => artistsApi.fuzzy(name),
    )
    if (id == null) failures.push(`直拍对象「${name}」`)
    else {
      detailForm.subject_artist_id = id
      draftSubjectArtistName.value = null
    }
  }

  // 曲目行：先歌曲后专辑（专辑挂在歌曲所在行）
  for (const row of detailForm.tracks) {
    if (row.draft_song) {
      const name = row.draft_song
      const id = await createOrFuzzyLink(
        () => songsApi.create({ name }),
        () => songsApi.fuzzy(name),
      )
      if (id == null) {
        failures.push(`歌曲「${name}」`)
        break
      }
      row.song_id = id
      row.draft_song = null
      songNameMap.value.set(id, name)
    }
  }
  for (const row of detailForm.tracks) {
    const drafts = [...(row.draft_albums || [])]
    for (let i = 0; i < drafts.length; ) {
      const name = drafts[i]
      const id = await createOrFuzzyLink(
        () => albumsApi.create({ name }),
        () => albumsApi.fuzzy(name),
      )
      if (id == null) {
        failures.push(`专辑「${name}」`)
        break
      }
      if (!row.album_ids.includes(id)) row.album_ids.push(id)
      row.draft_albums = drafts.filter((_, idx) => idx !== i)
      drafts.splice(i, 1)
    }
  }

  if (failures.length) {
    message.error(`以下内容创建失败，已中止入库，请处理后重试：${failures.join('、')}`)
    return false
  }
  return true
}

// 草稿移除（AI 建错了直接删，不进库）
function removeDraftArtist(i: number) {
  draftArtistNames.value.splice(i, 1)
}
function removeDraftGroup(i: number) {
  draftGroupNames.value.splice(i, 1)
}
function removeDraftSong(rowIndex: number) {
  const row = detailForm.tracks[rowIndex]
  if (row) row.draft_song = null
}
function removeDraftAlbum(rowIndex: number, i: number) {
  const row = detailForm.tracks[rowIndex]
  if (row?.draft_albums) row.draft_albums.splice(i, 1)
}
const hasTrackDrafts = computed(() =>
  detailForm.tracks.some((t) => t.draft_song || (t.draft_albums && t.draft_albums.length)),
)
/**
 * 曲目行里的「AI 填写、但资料库里没匹配上」的专辑 —— **专辑来源标记的唯一触发条件**。
 *
 * 口径（2026-09-18 用户确认）：AI 从资料库里挑中/关联到的专辑本身就是**库内数据**，
 * 一律只显示「主体 - 专辑 - 歌曲」归属提示、**不写任何来源词**（谁塞进来的都一样，
 * 见 albumHintOf）；只有库内查无此专辑、AI 自己编了名字的情况才标出来 ——
 * 这种情况会挂成「待新建」草稿，入库时才真正创建。
 */
const hasTrackAlbumDrafts = computed(() => detailForm.tracks.some((t) => !!t.draft_albums?.length))
const hasTrackSongDrafts = computed(() => detailForm.tracks.some((t) => !!t.draft_song))
// 本地匹配命中的曲目行数（徽标提示：专辑来自资料库关系）
const localMatchedCount = computed(
  () => detailForm.tracks.filter((t) => t.local_matched).length,
)

async function confirmIngest() {
  if (!activeItem.value) return
  const name = String(detailForm.name ?? '').trim()
  if (!name) {
    message.warning('请填写标题')
    return
  }
  if (activeItem.value.is_duplicate) {
    const ok = window.confirm('库中已有相同文件。继续入库会再写一条记录，确定吗？')
    if (!ok) return
  }
  ingesting.value = true
  try {
    // 1. 先把「待新建」草稿落库（创建失败会中止）
    const draftsOk = await materializeDrafts()
    if (!draftsOk) return
    // 2. 草稿变成真实关联后，刷新标题与建议路径（保持自动模式语义）
    if (!titleTouched.value) await refreshTitle()
    if (!destRelTouched.value) await refreshDestPreview()
    const {
      destination_rel: _dr,
      tracks,
      performance_date,
      published_date,
      ...mvPayload
    } = detailForm
    const cleanTracks = tracks
      .filter((t): t is TrackRow & { song_id: number } => t.song_id != null)
      .map((t) => ({ song_id: t.song_id, album_ids: [...new Set(t.album_ids)] }))
    const result = await libraryApi.ingest({
      file_path: activeItem.value.path,
      music_video: {
        ...mvPayload,
        video_type: detailForm.video_types[0] || 'Other',
        // 空串日期会让后端 Optional[date] 校验 422 → 未填时转 null（与 build-title/preview 同口径）
        performance_date: performance_date.trim() || null,
        published_date: published_date.trim() || null,
        song_ids: cleanTracks.map((t) => t.song_id),
        tracks: cleanTracks,
      } as { name: string; video_type: string },
      move_file: true,
      destination_rel: detailForm.destination_rel.trim() || null,
    })
    message.success(`入库成功（#${result.music_video_id}）`)
    incomingList.value = incomingList.value.filter(
      (it) => it.path !== activeItem.value!.path,
    )
    await loadStats()
    closeDetail()
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    ingesting.value = false
  }
}

onMounted(() => {
  void loadStats()
  void loadUploaderRules()
  libraryApi.paths().then((p) => {
    libraryRoot.value = p.library || ''
  }).catch(() => {
    libraryRoot.value = ''
  })
})
</script>

<style scoped src="./settings-shared.css"></style>

<style scoped>
/* ===== 命中本地资料库的已选项 = 强调色（v3.4.8 用户定稿） =====
 * 适用：曲目行的歌曲/专辑、艺术家、组合三处（.form-field.hit-accent）。
 * 不论来源是「本地匹配」还是「AI 填写」，只要值是库内 id（选择器里渲染出的已选项）
 * 一律强调色；AI 草稿（待新建）不进选择器，走 .draft-tag 的原色中性样式。
 * 特异性 0,5,0 > db-controls.css 已选项的 0,3,0，覆盖安全。
 * ⚠ 两套已选结构都要罩：**多选**（专辑/艺术家/组合）= .n-base-selection-tag-wrapper .n-tag；
 *   **单选**（曲目歌曲 RemoteSongSelect 不带 multiple）= render-label 覆盖层
 *   （v3.4.9 曾误补成 .n-base-selection-input，v3.5.0 真机转储纠正，v3.5.6 再排除占位态）。 */
.form-field.hit-accent :deep(.sa-select .n-base-selection-tag-wrapper .n-tag) {
  color: var(--sa-accent);
}
.form-field.hit-accent :deep(.sa-select .n-base-selection-tag-wrapper .n-tag__close) {
  color: var(--sa-accent-border);
}
.form-field.hit-accent :deep(.sa-select .n-base-selection-tag-wrapper .n-tag__close:hover) {
  color: var(--sa-accent);
}
/* ⚠ v3.5.0 真机 DOM 转储纠正（旧规则只罩 .n-base-selection-input 是【假绿】的根源）：
 * 单选已选值的**可见文字不在** `.n-base-selection-input` —— 那是个不可见遮罩 input
 * （量它的 color 永远是强调色，测试 PASS 而用户看不见任何变化）。
 * SaSelect 传了 :render-label，naive 把值渲染在 **render-label 覆盖层**里：
 *   .n-base-selection-label
 *     ├─ input.n-base-selection-input                    ← 透明遮罩（量这里会骗你）
 *     └─ .n-base-selection-label__render-label.n-base-selection-overlay
 *          └─ .n-base-selection-overlay__wrapper         ← 值所在，必须单独染色
 *
 * ⚠⚠ v3.5.6 用户报「歌曲一直强调色」再纠正：**单选未选中时，占位文字用的是同一个
 * `.n-base-selection-overlay__wrapper`**，只把外层类从 `n-base-selection-label__render-label`
 * 换成 `n-base-selection-placeholder`：
 *   .n-base-selection-label
 *     └─ .n-base-selection-placeholder.n-base-selection-overlay
 *          └─ .n-base-selection-overlay__wrapper  ← 占位文字「搜索歌曲名 / 别名 / 所属专辑」
 * 旧规则里的 `.n-base-selection-overlay` / `.n-base-selection-overlay__wrapper` 两条
 * 通吃两态 → 空选择器的占位文字也被染成强调色 = 「歌曲一直强调色」（**多选**的占位走
 * `.n-base-selection-placeholder__inner`，另一套结构，所以艺术家/组合没这个问题）。
 * 结论：覆盖层一律加 `:not(.n-base-selection-placeholder)` —— 值强调、占位中性。
 * 另：`.n-base-selection-input` 也加了同样的排除（它在空态时同样承载占位语义）。 */
.form-field.hit-accent :deep(.sa-select .n-base-selection-label .n-base-selection-input:not(.n-base-selection-placeholder)) {
  color: var(--sa-accent);
}
.form-field.hit-accent :deep(.sa-select .n-base-selection-label__render-label:not(.n-base-selection-placeholder)),
.form-field.hit-accent :deep(.sa-select .n-base-selection-label .n-base-selection-overlay:not(.n-base-selection-placeholder)),
.form-field.hit-accent :deep(.sa-select .n-base-selection-label .n-base-selection-overlay:not(.n-base-selection-placeholder) .n-base-selection-overlay__wrapper),
.form-field.hit-accent :deep(.sa-select .n-base-selection-input__content) {
  color: var(--sa-accent);
}

/* 选择器底部「新建 XX」按钮（SaSelect action 槽） */
.ms-quick-create {
  border: none;
  background: transparent;
  color: var(--sa-accent, #8a5cf5);
  cursor: pointer;
  font-size: 12px;
  padding: 4px 0;
  width: 100%;
  text-align: center;
}
.ms-quick-create:hover {
  text-decoration: underline;
}
/* 博主动规则命中提示：说明视频类型为何不由 AI 决定 */
.uploader-rule-note {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--sa-text-tertiary);
  overflow-wrap: anywhere;
}
.match-mode-chip {
  flex-shrink: 0;
  height: 26px;
  padding: 0 10px;
  border-radius: 9999px;
  border: 1px solid var(--sa-border-subtle);
  background: var(--sa-subtle);
  color: var(--sa-text-secondary);
  font-size: 12px;
  cursor: pointer;
  line-height: 26px;
}
.match-mode-chip:hover {
  color: var(--sa-text-primary);
  border-color: var(--sa-border);
}
/* 策略固定后只作状态展示：不可点、无 hover 反馈 */
.match-mode-chip--static,
.match-mode-chip--static:hover {
  cursor: default;
  color: var(--sa-text-secondary);
  border-color: var(--sa-border-subtle);
}
.match-mode-chip--end {
  margin-left: auto;
}

/* 视频平台 + 上传日期并排（桌面与移动端均为两列，节省纵向空间）。
   用 minmax(0,1fr)：1fr 默认 = minmax(auto,1fr)，轨道不会缩到内容最小宽度以下，
   iOS 上 date 输入框固有最小宽度较大，会把列撑破导致溢出。 */
.form-grid-2 {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 0 10px;
  min-width: 0;
}
.form-grid-2 .form-field {
  min-width: 0;
}
/* WebKit 的 date 输入框有默认 min-width，不显式清零就无法收缩到列宽以内 */
.form-grid-2 .sa-input {
  min-width: 0;
}
@media (max-width: 768px) {
  .form-grid-2 .sa-input {
    padding: 0 8px;
  }
}
/* iOS Safari 专项：date 输入框的固有宽度由内部阴影元素决定，
   仅靠 min-width:0 压不住（实测移动端仍溢出）。
   -webkit-appearance:none 去掉原生外观后才能收缩到容器宽度以内；
   ::-webkit-date-and-time-value 是日期文本本身，同样清 min-width 并左对齐。
   @supports (-webkit-touch-callout:none) 只命中 iOS WebKit，不影响桌面与 Android。 */
@supports (-webkit-touch-callout: none) {
  input[type='date'].sa-input {
    min-width: 0;
    width: 100%;
    -webkit-appearance: none;
    appearance: none;
  }
  input[type='date'].sa-input::-webkit-date-and-time-value {
    min-width: 0;
    text-align: left;
  }
}
/* 表演日期标签行：标签在左、「同上传日期」小按钮在右 */
.form-label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.mini-btn {
  height: 20px;
  padding: 0 9px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 9999px;
  background: var(--sa-elevated);
  color: var(--sa-text-secondary);
  font-size: 11px;
  line-height: 1;
  cursor: pointer;
  transition: all 0.15s;
}
.mini-btn:hover:not(:disabled) {
  color: var(--sa-text-primary);
  background: var(--sa-hover);
}
.mini-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>

<style scoped>
/* ===== 信息预览（v3.5.4，用户诉求：手机端看不到填写内容的完整信息）=====
 * 卡片外观按项目既有「HeroUI 复刻」口径（见 EntityMediaCard / SaDatePicker）：
 * 大圆角 + 细边 + 浮层阴影 + 分节头部。排版核心是**宽松**：
 * 值列是流式文本（word-break + 保留换行），长内容整段换行而不是被挤/被截；
 * ≤560px 时标签独占一行、值整行铺开（用户口径：一行放不下就另起一行）。 */
.info-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}
.info-card-head .info-title {
  flex: 1 1 auto;
  min-width: 0;
}
/* 「编辑元数据」标题行右侧挂预览入口（标题原有 margin-bottom 由共享样式提供） */
.form-title--with-action {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.preview-trigger {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 10px;
  border: 1px solid var(--sa-border);
  border-radius: 9999px;
  background: var(--sa-subtle);
  color: var(--sa-text-secondary);
  font-size: 12px;
  font-weight: 400;
  line-height: 1;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}
.preview-trigger:hover {
  color: var(--sa-accent);
  border-color: var(--sa-accent-border, var(--sa-border));
}
.preview-trigger:active {
  opacity: 0.75;
}

.preview-mask {
  position: fixed;
  inset: 0;
  z-index: 2800;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: rgba(0, 0, 0, 0.55);
}
.preview-card {
  display: flex;
  flex-direction: column;
  width: min(560px, 100%);
  max-height: min(84vh, 760px);
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 16px;
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.45);
  overflow: hidden;
}
.preview-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 14px 16px 6px;
}
.preview-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--sa-text-primary);
}
.preview-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--sa-text-tertiary);
  cursor: pointer;
}
.preview-close:hover {
  color: var(--sa-text-primary);
  background: var(--sa-hover);
}
.preview-sub {
  padding: 0 16px 10px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--sa-text-tertiary);
  word-break: break-all;
}
.preview-rows {
  padding: 0 16px 16px;
  overflow-y: auto;
  overscroll-behavior: contain;
}
.preview-row {
  display: grid;
  grid-template-columns: 84px minmax(0, 1fr);
  gap: 10px;
  padding: 9px 0;
  border-top: 1px solid var(--sa-border-subtle);
}
.preview-row:first-child {
  border-top: none;
}
.preview-k {
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.preview-v {
  font-size: 13px;
  line-height: 1.65;
  color: var(--sa-text-primary);
  word-break: break-all;
  white-space: pre-wrap;
}
.preview-v--mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}
.preview-empty {
  padding: 6px 0;
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
@media (max-width: 560px) {
  /* 手机：标签独占一行 → 值整行铺开，长文本自然换行（不再被 110px 标签列挤压） */
  .preview-row {
    grid-template-columns: minmax(0, 1fr);
    gap: 2px;
    padding: 10px 0;
  }
  .preview-card {
    width: 100%;
    max-height: 88vh;
    border-radius: 14px;
  }
  .preview-mask {
    padding: 10px;
  }
}
</style>


<template>
            <section class="sa-section incoming-panel">
              <!-- 分区名由左侧导航/移动端标签条给出；顶部整行（标题 + 匹配策略 chip）按用户口径全部去掉 -->
              <!-- 列表视图 -->
              <template v-if="!activeItem">
                <div class="action-grid">
                  <div class="action-card action-clickable" @click="toggleIncoming">
                    <div class="action-icon"><InboxOutlined :size="18" /></div>
                    <div class="action-main">
                      <div class="action-label">待整理文件</div>
                      <div class="action-value">
                        {{ stats?.incoming_count ?? '--' }}
                        <span class="action-unit">个</span>
                      </div>
                    </div>
                    <div class="action-meta">点击查看待整理列表</div>
                  </div>
                  <div class="action-card action-clickable" @click="doScan">
                    <div class="action-icon"><RefreshOutlined :size="18" /></div>
                    <div class="action-main">
                      <div class="action-label">{{ scanning ? '正在扫描…' : '扫描目录' }}</div>
                      <div class="action-value action-value--small">立即检查新文件</div>
                    </div>
                    <div class="action-meta">不移动文件，仅发现新素材</div>
                  </div>
                </div>

                <transition name="list-fade">
                  <div v-if="incomingOpen" class="incoming-list">
                    <div class="incoming-list-head">
                      <span>待整理视频</span>
                      <button
                        class="incoming-refresh"
                        :disabled="incomingLoading"
                        aria-label="刷新列表"
                        @click="loadIncoming"
                      >
                        <RefreshOutlined :size="13" />
                      </button>
                    </div>
                    <div v-if="incomingLoading" class="incoming-empty">正在扫描…</div>
                    <div v-else-if="!incomingList.length" class="incoming-empty">
                      暂无待整理文件，点击「扫描目录」检查新素材
                    </div>
                    <ul v-else class="incoming-items">
                      <li
                        v-for="item in incomingList"
                        :key="item.path"
                        class="incoming-item incoming-item--clickable"
                        @click="openDetail(item)"
                      >
                        <div class="incoming-item-main">
                          <span class="incoming-name" :title="item.file_name">{{ item.file_name }}</span>
                          <span class="incoming-meta">
                            {{ formatResolution(item.width, item.height) }} · {{ formatDuration(item.duration) }} · {{ formatFileSize(item.file_size) }}
                          </span>
                        </div>
                        <span
                          v-if="isBilibiliFile(item.path)"
                          class="incoming-tag tag--bili"
                        >
                          B站
                        </span>
                        <span
                          class="incoming-tag"
                          :class="item.is_duplicate ? 'tag--dup' : 'tag--new'"
                        >
                          {{ item.is_duplicate ? '已存在' : '新' }}
                        </span>
                      </li>
                    </ul>
                  </div>
                </transition>
              </template>

              <!-- 详情视图 -->
              <template v-else>
                <div class="detail-head">
                  <button class="detail-back" @click="closeDetail">
                    <ArrowLeftOutlined :size="14" />
                    返回列表
                  </button>
                  <span class="detail-file" :title="activeItem.file_name">{{ activeItem.file_name }}</span>
                </div>

                <div class="detail-layout">
                  <!-- 左：视频信息（封面 + 元数据 + 来源字段，已合并为一张卡） -->
                  <div class="detail-left">
                    <div v-if="infoLoading" class="detail-hint">正在读取 info.json…</div>
                    <template v-else>
                      <div v-if="detailInfo" class="cover-card">
                        <img
                          v-if="detailCoverSrc"
                          :src="detailCoverSrc"
                          class="cover-img"
                          alt="视频封面"
                          loading="lazy"
                          referrerpolicy="no-referrer"
                          @error="onDetailCoverError"
                        />
                        <div v-else class="cover-placeholder">暂无封面</div>
                      </div>
                      <div v-if="!detailInfo" class="detail-hint">无 info.json 元数据：将由 AI 基于文件名与视频参数推断，请重点核对艺人、歌曲与类型</div>
<div class="info-card">
                        <div class="info-card-head">
                          <h3 v-if="detailInfo" class="info-title">{{ detailInfo.title || activeItem.file_name }}</h3>
                        </div>
                        <!-- 统一为「标签 : 值」的展示行（与上传日期 / 播放量同款），不再使用输入框 -->
                        <div class="info-rows">
                          <template v-if="detailInfo">
                            <div v-if="detailInfo.webpage_url" class="info-row">
                              <span class="k">链接</span>
                              <a class="v" :href="detailInfo.webpage_url" target="_blank" rel="noopener">{{ detailInfo.webpage_url }}</a>
                            </div>
                            <div v-if="detailInfo.bili_tags?.length" class="info-row">
                              <span class="k">标签</span>
                              <span class="v">{{ detailInfo.bili_tags.join(' / ') }}</span>
                            </div>
                            <div v-if="detailInfo.upload_date" class="info-row"><span class="k">上传日期</span><span class="v">{{ uploadDateToISO(detailInfo.upload_date) }}</span></div>
                            <div v-if="detailInfo.view_count != null" class="info-row">
                              <span class="k">播放量</span><span class="v">{{ detailInfo.view_count.toLocaleString() }}</span>
                            </div>
                            <div v-if="detailInfo.like_count != null" class="info-row">
                              <span class="k">点赞</span><span class="v">{{ detailInfo.like_count.toLocaleString() }}</span>
                            </div>
                          </template>
                          <!-- 以下三项在没有 info.json 时同样保留，可直接填写 -->
                          <div class="info-row">
                            <span class="k">博主</span>
                            <input
                              v-model="detailForm.original_uploader"
                              class="v info-row-input"
                              type="text"
                              placeholder="来源频道 / 上传者"
                            />
                          </div>
                          <div v-if="videoDataSummary" class="info-row">
                            <span class="k">视频数据</span>
                            <span class="v">{{ videoDataSummary }}</span>
                          </div>
                          <div v-if="descText" class="info-row">
                            <span class="k">原简介</span>
                            <div class="v">
                              <div
                                ref="descRef"
                                class="info-desc"
                                :class="{ 'is-clamped': !descExpanded, 'is-clickable': descOverflow }"
                                :title="descOverflow ? (descExpanded ? '点击收起' : '点击展开') : undefined"
                                @click="toggleDesc"
                              >
                                {{ descText }}
                              </div>
                              <!-- 展开/收起入口放在被裁剪元素之外，否则提示自己也会被裁掉 -->
                              <button v-if="descOverflow" class="desc-toggle" type="button" @click="toggleDesc">
                                {{ descExpanded ? '收起' : '展开' }}
                              </button>
                            </div>
                          </div>
                        </div>
                      </div>
                    </template>
                  </div>

                  <!-- 右：编辑元数据 -->
                  <div class="detail-right">
                    <div class="form-card">
                      <div class="form-title form-title--with-action">
                        <span>编辑元数据</span>
                        <button
                          class="preview-trigger"
                          type="button"
                          title="以卡片形式完整查看这份填写内容（长内容自动换行，不挤成一行）"
                          @click="previewOpen = true"
                        >
                          <InfoOutlined :size="13" />
                          信息预览
                        </button>
                      </div>
                      <div class="form-field">
                        <span class="form-label">视频类型</span>
                        <SaSelect
                          v-model="detailForm.video_types"
                          :options="VIDEO_TYPE_OPTIONS"
                          multiple
                          placeholder="可选择多个视频类型"
                        />
                        <div v-if="uploaderRuleTypes.length" class="uploader-rule-note">
                          已按博主规则固定为「{{ uploaderRuleLabels }}」（{{ uploaderRuleName }}），不再由 AI 判断
                        </div>
                      </div>
                      <div class="form-field">
                        <div class="form-check">
                          <input
                            id="is-solo-check"
                            v-model="detailForm.is_solo"
                            type="checkbox"
                          />
                          <label for="is-solo-check">Solo（独立艺人）</label>
                        </div>
                      </div>
                      <div class="form-field">
                        <span class="form-label">标题</span>
                        <input v-model="detailForm.name" class="sa-input" type="text" placeholder="库内展示标题" @input="titleTouched = true" />
                      </div>
                      <div class="form-grid-2">
                        <div class="form-field">
                          <span class="form-label">视频平台</span>
                          <input v-model="detailForm.source_platform" class="sa-input" type="text" readonly placeholder="根据链接自动判断 youtube / bilibili 等" />
                        </div>
                        <div class="form-field">
                          <span class="form-label">上传日期</span>
                          <input v-model="detailForm.published_date" class="sa-input" type="date" placeholder="自动填写 json 中的上传日期" />
                        </div>
                      </div>
                      <div class="form-field">
                        <div class="form-label-row">
                          <span class="form-label">表演日期</span>
                          <button class="mini-btn" type="button" :disabled="!detailForm.published_date" title="把上传日期复制到表演日期" @click="syncPerformanceFromPublished">同上传日期</button>
                        </div>
                        <input v-model="detailForm.performance_date" class="sa-input" type="date" placeholder="AI 判断表演日期，可手动修改" />
                      </div>
                      <div class="form-field">
                        <span class="form-label">舞台 / 活动</span>
                        <input v-model="detailForm.event_name" class="sa-input" type="text" placeholder="如 XX 艺术节、XX 校庆、XX 电视台节目等，AI 判断后填入" />
                      </div>
                      <div class="form-field hit-accent">
                        <span class="form-label">曲目</span>
                        <VideoTrackList
                          :tracks="detailForm.tracks"
                          :album-presets="albumPresets"
                          :search-albums="searchAlbums"
                          album-placeholder="专辑名或歌名，可多选"
                          @update:tracks="detailForm.tracks = $event"
                          @create-album="(i) => openAlbumCreate('ingest', i)"
                        />
                        <div v-if="localMatchedCount" class="local-match-note">
                          {{ localMatchedCount }} 首歌曲按资料库本地匹配（专辑来自库内关系，AI 建议不覆盖；如识别有误可直接在上方修改）
                        </div>
                        <div v-if="hasTrackDrafts" class="draft-tags">
                          <template v-for="(row, ri) in detailForm.tracks" :key="row.key">
                            <span v-if="row.draft_song" class="draft-tag">
                              曲目{{ ri + 1 }} ＋ 新建歌曲「{{ row.draft_song }}」
                              <button
                                class="draft-tag-remove"
                                type="button"
                                title="移除该待新建项"
                                @click="removeDraftSong(ri)"
                              >
                                ×
                              </button>
                            </span>
                            <span
                              v-for="(a, ai) in row.draft_albums || []"
                              :key="`${row.key}-a-${a}`"
                              class="draft-tag draft-tag--album"
                              title="资料库里没有这张专辑，入库时会按 AI 填写的内容新建"
                            >
                              <span class="draft-src">AI 填写</span>
                              曲目{{ ri + 1 }} ＋ 新建专辑「{{ a }}」
                              <button
                                class="draft-tag-remove"
                                type="button"
                                title="移除该待新建项"
                                @click="removeDraftAlbum(ri, ai)"
                              >
                                ×
                              </button>
                            </span>
                          </template>
                        </div>
                        <div v-if="hasTrackAlbumDrafts" class="draft-hint">
                          标「AI 填写」的专辑在资料库里没匹配上，入库时才按填写内容新建；若库内其实已有这张专辑，直接在上方专辑框里选它即可；建错了点 × 删除
                        </div>
                        <div v-if="hasTrackSongDrafts" class="draft-hint">
                          以上为 AI 建议的新歌曲，点「入库」时才会创建；建错了直接点 × 删除
                        </div>
                      </div>
                      <div class="form-field hit-accent">
                        <span class="form-label">艺术家</span>
                        <SaSelect
                          v-model="detailForm.artist_ids"
                          :fetch-options="searchArtists"
                          :preset-options="artistPresets"
                          multiple
                          placeholder="输入艺术家名搜索并关联"
                        >
                          <template #action>
                            <button class="ms-quick-create" type="button" @click="openArtistCreate">
                              新建艺人
                            </button>
                          </template>
                        </SaSelect>
                        <div v-if="draftArtistNames.length" class="draft-tags">
                          <span v-for="(n, i) in draftArtistNames" :key="n" class="draft-tag">
                            ＋ 新建 {{ n }}
                            <button
                              class="draft-tag-remove"
                              type="button"
                              title="移除该待新建项"
                              @click="removeDraftArtist(i)"
                            >
                              ×
                            </button>
                          </span>
                        </div>
                        <div v-if="draftArtistNames.length" class="draft-hint">
                          以上为 AI 建议的新艺人，点「入库」时才会创建；建错了直接点 × 删除
                        </div>
                      </div>
                      <div class="form-field hit-accent">
                        <span class="form-label">组合</span>
                        <SaSelect
                          v-model="detailForm.group_ids"
                          :fetch-options="searchGroups"
                          :preset-options="groupPresets"
                          multiple
                          placeholder="输入组合名搜索并关联"
                        >
                          <template #action>
                            <button class="ms-quick-create" type="button" @click="openGroupCreate">
                              新建组合
                            </button>
                          </template>
                        </SaSelect>
                        <div v-if="draftGroupNames.length" class="draft-tags">
                          <span v-for="(n, i) in draftGroupNames" :key="n" class="draft-tag">
                            ＋ 新建 {{ n }}
                            <button
                              class="draft-tag-remove"
                              type="button"
                              title="移除该待新建项"
                              @click="removeDraftGroup(i)"
                            >
                              ×
                            </button>
                          </span>
                        </div>
                        <div v-if="draftGroupNames.length" class="draft-hint">
                          以上为 AI 建议的新组合，点「入库」时才会创建；建错了直接点 × 删除
                        </div>
                      </div>
                      <div class="form-field">
                        <span class="form-label">中文简介</span>
                        <textarea v-model="detailForm.chinese_description" class="sa-textarea" rows="4" placeholder="AI 根据视频信息生成的中文简介，可手动修改（规则在「AI 设置」页可改）" />
                      </div>
                      <div class="form-field">
                        <span class="form-label">入库路径</span>
                        <div class="dest-path-preview">
                          <span class="dest-path-label">入库后位置</span>
                          <code class="dest-path-code">{{ destAbsDisplay }}</code>
                        </div>
                        <div class="dest-path-row">
                          <input
                            v-model="detailForm.destination_rel"
                            class="sa-input"
                            type="text"
                            placeholder="如：组合名/艺人名/歌曲名/文件.mp4"
                            @input="destRelTouched = true"
                          />
                          <button
                            class="sa-btn sa-btn--ai sa-btn--icon"
                            type="button"
                            title="刷新建议路径"
                            aria-label="刷新建议路径"
                            :disabled="infoLoading"
                            @click="refreshDestPreview(true)"
                          >
                            <RefreshOutlined :size="14" />
                          </button>
                          <button
                            class="sa-btn sa-btn--ghost sa-btn--icon"
                            type="button"
                            title="选择已有目录"
                            aria-label="选择已有目录"
                            @click="loadDestDirs"
                          >
                            <FolderOutlined :size="14" />
                          </button>
                        </div>
                        <div v-if="destPreview.notice" class="dest-path-notice">{{ destPreview.notice }}</div>
                      </div>
                      <div class="form-actions">
                        <button
                          :class="['sa-btn', 'sa-btn--ai', 'sa-btn--icon', { 'is-busy': albumAiLoading }]"
                          type="button"
                          :title="albumAiLoading ? 'AI 搜索中…' : '专辑搜索'"
                          :aria-label="albumAiLoading ? 'AI 搜索中…' : '专辑搜索'"
                          :disabled="albumAiLoading || aiLoading"
                          @click="aiSearchAlbum"
                        >
                          <LoadingOutlined v-if="albumAiLoading" :size="14" />
                          <SearchOutlined v-else :size="14" />
                        </button>
                        <button
                          :class="['sa-btn', 'sa-btn--ai', 'sa-btn--icon', { 'is-busy': aiLoading }]"
                          type="button"
                          :title="aiLoading ? 'AI 生成中…' : 'AI 填写'"
                          :aria-label="aiLoading ? 'AI 生成中…' : 'AI 填写'"
                          :disabled="aiLoading"
                          @click="aiIngest"
                        >
                          <LoadingOutlined v-if="aiLoading" :size="14" />
                          <RobotOutlined v-else :size="14" />
                        </button>
                        <button class="sa-btn sa-btn--primary" :disabled="ingesting" @click="confirmIngest">
                          {{ ingesting ? '入库中…' : '确认入库' }}
                        </button>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- 选择已有入库目录 -->
                <n-modal
                  v-model:show="destDirOpen"
                  preset="card"
                  title="选择已有入库目录"
                  :style="{ width: 'min(480px, calc(100vw - 24px))' }"
                >
                  <div class="dest-dir-list">
                    <button
                      v-for="d in libraryDirs"
                      :key="d"
                      class="dest-dir-item"
                      type="button"
                      @click="applyDestDir(d)"
                    >
                      <FolderOutlined :size="14" />
                      <span>{{ d }}</span>
                    </button>
                    <div v-if="!libraryDirs.length" class="dest-dir-empty">
                      正式库中暂无子目录，可关闭弹窗后手动输入相对路径
                    </div>
                  </div>
                  <template #footer>
                    <div class="modal-actions">
                      <n-button @click="destDirOpen = false">取消</n-button>
                    </div>
                  </template>
                </n-modal>
              </template>
            </section>
            <!-- 快速新建专辑（入库详情 / 数据库歌曲编辑共用） -->
            <n-modal
              v-model:show="albumCreateShow"
              preset="card"
              title="快速新建专辑"
              :style="{ width: 'min(420px, calc(100vw - 24px))' }"
            >
              <n-form label-placement="top">
                <n-form-item label="专辑名 *">
                  <n-input v-model:value="albumCreateForm.name" placeholder="如：After LIKE" @keydown.enter="submitAlbumCreate" />
                </n-form-item>
                <n-form-item label="中文名">
                  <n-input v-model:value="albumCreateForm.chinese_name" placeholder="选填" @keydown.enter="submitAlbumCreate" />
                </n-form-item>
                <n-form-item label="专辑类型">
                  <SaSelect v-model="albumCreateForm.album_type" :options="ALBUM_TYPE_OPTIONS" />
                </n-form-item>
              </n-form>
              <template #footer>
                <div class="modal-actions">
                  <n-button @click="albumCreateShow = false">取消</n-button>
                  <n-button type="primary" :loading="albumCreating" @click="submitAlbumCreate">
                    创建并关联
                  </n-button>
                </div>
              </template>
            </n-modal>

            <!-- 快速新建艺人（含查重） -->
            <n-modal
              v-model:show="artistCreateShow"
              preset="card"
              title="快速新建艺人"
              :style="{ width: 'min(420px, calc(100vw - 24px))' }"
            >
              <n-form label-placement="top">
                <n-form-item label="艺人名 *">
                  <n-input v-model:value="artistCreateForm.name" placeholder="如：IU、Kim YooYeon" @keydown.enter="submitArtistCreate" />
                </n-form-item>
                <n-form-item label="中文名">
                  <n-input v-model:value="artistCreateForm.chinese_name" placeholder="选填" @keydown.enter="submitArtistCreate" />
                </n-form-item>
              </n-form>
              <template #footer>
                <div class="modal-actions">
                  <n-button @click="artistCreateShow = false">取消</n-button>
                  <n-button type="primary" :loading="artistCreating" @click="submitArtistCreate">
                    创建并关联
                  </n-button>
                </div>
              </template>
            </n-modal>

            <!-- 快速新建组合（含查重） -->
            <n-modal
              v-model:show="groupCreateShow"
              preset="card"
              title="快速新建组合"
              :style="{ width: 'min(420px, calc(100vw - 24px))' }"
            >
              <n-form label-placement="top">
                <n-form-item label="组合名 *">
                  <n-input v-model:value="groupCreateForm.name" placeholder="如：IVE、NewJeans" @keydown.enter="submitGroupCreate" />
                </n-form-item>
                <n-form-item label="中文名">
                  <n-input v-model:value="groupCreateForm.chinese_name" placeholder="选填" @keydown.enter="submitGroupCreate" />
                </n-form-item>
              </n-form>
              <template #footer>
                <div class="modal-actions">
                  <n-button @click="groupCreateShow = false">取消</n-button>
                  <n-button type="primary" :loading="groupCreating" @click="submitGroupCreate">
                    创建并关联
                  </n-button>
                </div>
              </template>
            </n-modal>

            <n-modal
              v-model:show="artistFuzzyOpen"
              preset="card"
              title="艺人查重提示"
              :style="{ width: 'min(480px, calc(100vw - 24px))' }"
            >
              <p class="fuzzy-hint">
                输入「{{ artistFuzzyTitle }}」时，库里已有非常接近的艺人（空格、大小写、别名视为同一人）。请选择已有记录，避免建成两条：
              </p>
              <n-spin :show="artistFuzzyLoading">
                <div class="fuzzy-list">
                  <button
                    v-for="hit in artistFuzzyCandidates"
                    :key="hit.id"
                    class="fuzzy-item"
                    @click="closeArtistFuzzy(hit)"
                  >
                    <span class="fuzzy-item-name">
                      {{ hit.name }}
                      <template v-if="hit.stage_name && hit.stage_name !== hit.name">
                        （{{ hit.stage_name }}）
                      </template>
                      <template v-if="hit.korean_name"> · {{ hit.korean_name }}</template>
                      <template v-if="hit.chinese_name"> · {{ hit.chinese_name }}</template>
                    </span>
                    <span class="fuzzy-item-meta">
                      相似度 {{ Math.round((hit.score || 0) * 100) }}%
                    </span>
                  </button>
                </div>
              </n-spin>
              <template #footer>
                <div class="modal-actions">
                  <n-button @click="closeArtistFuzzy('skip')">取消</n-button>
                  <n-button quaternary @click="closeArtistFuzzy('create')">
                    都不是，保留为待新建
                  </n-button>
                </div>
              </template>
            </n-modal>

            <!-- 专辑查重确认（新建专辑 / AI 建议专辑名与库内相似时提示） -->
            <n-modal
              v-model:show="albumFuzzyOpen"
              preset="card"
              title="专辑查重提示"
              :style="{ width: 'min(480px, calc(100vw - 24px))' }"
            >
              <p class="fuzzy-hint">
                输入「{{ albumFuzzyTitle }}」时，数据库中检测到以下相似专辑。不同来源对同一张专辑的命名可能略有差异（如尾缀不同），请选择库内已有的专辑，避免重复入库：
              </p>
              <n-spin :show="albumFuzzyLoading">
                <div class="fuzzy-list">
                  <button
                    v-for="hit in albumFuzzyCandidates"
                    :key="hit.id"
                    class="fuzzy-item"
                    @click="closeAlbumFuzzy(hit)"
                  >
                    <span class="fuzzy-item-name">
                      {{ hit.name }}
                      <template v-if="hit.chinese_name">（{{ hit.chinese_name }}）</template>
                    </span>
                    <span class="fuzzy-item-meta">
                      {{ hit.album_type || '未知类型' }} · 相似度
                      {{ Math.round((hit.score || 0) * 100) }}%
                    </span>
                  </button>
                </div>
              </n-spin>
              <template #footer>
                <div class="modal-actions">
                  <n-button @click="closeAlbumFuzzy('skip')">取消</n-button>
                  <n-button quaternary @click="closeAlbumFuzzy('create')">
                    都不是，保留为待新建
                  </n-button>
                </div>
              </template>
            </n-modal>

            <n-modal
              v-model:show="songFuzzyOpen"
              preset="card"
              title="歌曲查重提示"
              :style="{ width: 'min(480px, calc(100vw - 24px))' }"
            >
              <p class="fuzzy-hint">
                输入「{{ songFuzzyTitle }}」时，库里已有非常接近的歌曲（中英韩文名、空格差异视为同一首）。请选择已有记录，避免建成两条：
              </p>
              <n-spin :show="songFuzzyLoading">
                <div class="fuzzy-list">
                  <button
                    v-for="hit in songFuzzyCandidates"
                    :key="hit.id"
                    class="fuzzy-item"
                    @click="closeSongFuzzy(hit)"
                  >
                    <span class="fuzzy-item-name">
                      {{ hit.name }}
                      <template v-if="hit.english_name && hit.english_name !== hit.name">
                        · {{ hit.english_name }}
                      </template>
                      <template v-if="hit.korean_name"> · {{ hit.korean_name }}</template>
                      <template v-if="hit.chinese_name"> · {{ hit.chinese_name }}</template>
                    </span>
                    <span class="fuzzy-item-meta">
                      相似度 {{ Math.round((hit.score || 0) * 100) }}%
                    </span>
                  </button>
                </div>
              </n-spin>
              <template #footer>
                <div class="modal-actions">
                  <n-button @click="closeSongFuzzy('skip')">取消</n-button>
                  <n-button quaternary @click="closeSongFuzzy('create')">
                    都不是，保留为待新建
                  </n-button>
                </div>
              </template>
            </n-modal>

            <n-modal
              v-model:show="groupFuzzyOpen"
              preset="card"
              title="组合查重提示"
              :style="{ width: 'min(480px, calc(100vw - 24px))' }"
            >
              <p class="fuzzy-hint">
                输入「{{ groupFuzzyTitle }}」时，库里已有非常接近的组合。请选择已有记录，避免建成两条：
              </p>
              <n-spin :show="groupFuzzyLoading">
                <div class="fuzzy-list">
                  <button
                    v-for="hit in groupFuzzyCandidates"
                    :key="hit.id"
                    class="fuzzy-item"
                    @click="closeGroupFuzzy(hit)"
                  >
                    <span class="fuzzy-item-name">
                      {{ hit.name }}
                      <template v-if="hit.english_name && hit.english_name !== hit.name">
                        · {{ hit.english_name }}
                      </template>
                      <template v-if="hit.korean_name"> · {{ hit.korean_name }}</template>
                      <template v-if="hit.chinese_name"> · {{ hit.chinese_name }}</template>
                    </span>
                    <span class="fuzzy-item-meta">
                      相似度 {{ Math.round((hit.score || 0) * 100) }}%
                    </span>
                  </button>
                </div>
              </n-spin>
              <template #footer>
                <div class="modal-actions">
                  <n-button @click="closeGroupFuzzy('skip')">取消</n-button>
                  <n-button quaternary @click="closeGroupFuzzy('create')">
                    都不是，保留为待新建
                  </n-button>
                </div>
              </template>
            </n-modal>

            <!-- AI 专辑识别结果（根据歌曲名 + 组合/艺人名判断所属专辑） -->
            <n-modal
              v-model:show="albumAiOpen"
              preset="card"
              title="AI 专辑识别结果"
              :style="{ width: 'min(480px, calc(100vw - 24px))' }"
            >
              <p v-if="albumAiMulti.length && albumAiInfo.performer" class="album-ai-ident">
                表演者：{{ albumAiInfo.performer }}
              </p>
              <template v-if="albumAiMulti.length">
                <div v-for="m in albumAiMulti" :key="m.song" class="album-ai-group">
                  <p class="album-ai-group-title">{{ m.song }}</p>
                  <div class="fuzzy-list">
                    <div v-for="name in m.albums" :key="name" class="album-ai-item">
                      <span class="album-ai-name">{{ name }}</span>
                      <button
                        class="sa-btn sa-btn--ai album-ai-link"
                        @click="albumAiLink(name, m.song)"
                      >
                        <SearchOutlined :size="13" />
                        搜索并关联
                      </button>
                    </div>
                  </div>
                </div>
              </template>
              <template v-else>
                <p v-if="albumAiInfo.song || albumAiInfo.performer" class="album-ai-ident">
                  <template v-if="albumAiInfo.song">歌曲：{{ albumAiInfo.song }}</template>
                  <template v-if="albumAiInfo.performer"> · 表演者：{{ albumAiInfo.performer }}</template>
                </p>
                <p v-if="albumAiNames.length" class="fuzzy-hint">
                  该歌曲可能收录于以下专辑：
                </p>
                <p v-else class="fuzzy-hint">
                  AI 已识别出歌曲信息，但未能确定所属专辑，可点击下方搜索或稍后手动关联。
                </p>
                <div v-if="albumAiNames.length" class="fuzzy-list">
                  <div
                    v-for="name in albumAiNames"
                    :key="name"
                    class="album-ai-item"
                  >
                    <span class="album-ai-name">{{ name }}</span>
                    <button
                      class="sa-btn sa-btn--ai album-ai-link"
                      @click="albumAiLink(name)"
                    >
                      <SearchOutlined :size="13" />
                      搜索并关联
                    </button>
                  </div>
                </div>
              </template>
              <div v-if="albumAiSources.length" class="ai-source-block">
                <p class="ai-source-title">来源核对</p>
                <div
                  v-for="(s, i) in albumAiSources"
                  :key="`${s.field}-${s.value}-${i}`"
                  class="ai-source-item"
                >
                  <span class="ai-source-field">{{ s.field }}</span>
                  <span class="ai-source-value">{{ s.value }}</span>
                  <span class="ai-source-src">{{ s.source }}</span>
                </div>
              </div>
              <template #footer>
                <div class="modal-actions">
                  <n-button @click="albumAiOpen = false">关闭</n-button>
                </div>
              </template>
            </n-modal>

    <!-- 信息预览卡（v3.5.4）：HeroUI 风格大圆角卡片 + 宽松排版 ——
         展示右侧表单的填写内容（入库时写入什么，这里就显示什么），
         一行放不下就让值换行（窄屏时标签独占一行），不出现「挤成一行 / 看不全」。 -->
    <Teleport to="body">
      <div v-if="previewOpen" class="preview-mask" @click.self="previewOpen = false">
        <div class="preview-card" role="dialog" aria-modal="true" aria-label="填写内容预览">
          <div class="preview-head">
            <div class="preview-title">填写内容预览</div>
            <button class="preview-close" type="button" aria-label="关闭" @click="previewOpen = false">
              <CloseOutlined :size="14" />
            </button>
          </div>
          <div v-if="activeItem" class="preview-sub">
            {{ activeItem.file_name }} · 点「确认入库」后将按以下内容写入资料库
          </div>
          <div class="preview-rows">
            <div v-for="row in previewRows" :key="row.k" class="preview-row">
              <div class="preview-k">{{ row.k }}</div>
              <div class="preview-v" :class="{ 'preview-v--mono': row.mono }">{{ row.v }}</div>
            </div>
            <div v-if="!previewRows.length" class="preview-empty">这条素材还没有填写内容</div>
          </div>
        </div>
      </div>
    </Teleport>
</template>
