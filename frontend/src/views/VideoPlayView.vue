<script lang="ts">
export default { name: 'VideoPlayView' }
</script>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton,
  NCheckbox,
  NDatePicker,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NSpace,
  NSpin,
  useMessage,
} from 'naive-ui'
import VideoPlayer from '@/components/VideoPlayer.vue'
import { nativeCapabilities } from '@/native/android'
import SaHeader from '@/components/SaHeader.vue'
import SaSelect from '@/components/SaSelect.vue'
import type { SaOption } from '@/components/SaSelect.vue'
import VideoTrackList from '@/components/VideoTrackList.vue'
import type { TrackRow } from '@/components/VideoTrackList.vue'
import StageMatrixDock from '@/components/StageMatrixDock.vue'
import VideoInfoDock from '@/components/VideoInfoDock.vue'
import ChapterSegmentBar from '@/components/ChapterSegmentBar.vue'
import { albumsApi } from '@/api/albums'
import { artistsApi } from '@/api/artists'
import { groupsApi } from '@/api/groups'
import { musicVideosApi, type VideoSubtitleTrack } from '@/api/musicVideos'
import { playbackApi, type CreatePlaybackSessionPayload } from '@/api/playback'
import { dashboardApi } from '@/api/dashboard'
import { systemApi } from '@/api/system'
import type {
  AlbumBrief,
  ArtistBrief,
  GroupBrief,
  MusicVideo,
  MusicVideoBrief,
  MusicVideoRelated,
  MusicVideoRelatedArtist,
  PlaybackSession,
  SongBrief,
  StageMatrix,
  VideoScore,
  VideoChapter,
  VideoPathSuggestion,
  VideoType,
} from '@/types/models'
import { CAMERA_ANGLE_OPTIONS, QUALITY_LABEL, VIDEO_TYPE_OPTIONS, VIDEO_TYPE_LABEL } from '@/types/models'
import {
  PlayArrowOutlined,
  EditOutlined,
  MovieOutlined,
  BookmarkFilled,
  BookmarkOutlined,
  FolderOutlined,
  RefreshOutlined,
  StarOutlined,
} from '@/components/icons'
import VideoCollectPicker from '@/components/VideoCollectPicker.vue'
import CoverFramePicker from '@/components/CoverFramePicker.vue'
import FolderPickerModal from '@/components/FolderPickerModal.vue'
import { videoCollectionsApi } from '@/api/videoCollections'
import { formatDuration } from '@/utils/format'
import { artistPath, fetchByRouteParam, groupPath, videoPath } from '@/utils/routes'
import { IMG_W_FULL, IMG_W_THUMB } from '@/utils/imageSizes'
import { RESOLUTION_TIERS } from '@/utils/resolution'
import { useSettingsStore } from '@/stores/settings'
import { readScoringAiConfig } from '@/views/settings/scoringLocal'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const settings = useSettingsStore()

const video = ref<MusicVideo | null>(null)
// 外挂同名字幕轨（后端统一转 VTT；随视频加载拉取，失败静默）
const subtitleTracks = ref<VideoSubtitleTrack[]>([])
const notFound = ref(false)
const loading = ref(false)
const playing = ref(false)
const session = ref<PlaybackSession | null>(null)
const sessionLoading = ref(false)
const activeQuality = ref<string>('original')
const userPickedQuality = ref(false)
const playerBuffering = ref(false)
const playerError = ref('')
const lastKnownTime = ref(0)
// 用户拖动进度条（Emby 式任意 seek）期间为 true：显示「正在跳转…」缓冲提示，
// 直到新转码会话（-ss 目标时间）建好并切到新清单
const isSeeking = ref(false)
// 换源后让 VideoPlayer 在流内精确定位（seekInto = 目标绝对时间 - 新会话 start_offset）
const seekInto = ref(0)
// seek 序列号：丢弃过期的异步回调（用户快速连续拖动时旧请求不应覆盖新会话）
let seekSeq = 0
const SEEK_DEBOUNCE_MS = 280
let seekDebounceTimer: number | null = null
let seekUiGen = 0

function stopSession(id: number | null | undefined) {
  if (!id) return Promise.resolve()
  return playbackApi.stop(id, { force: true }).catch(() => {})
}

function isBusyError(e: unknown): boolean {
  const err = e as Error & { status?: number }
  if (err.status === 429 || err.status === 503) return true
  return /转码并发上限|转码繁忙/.test(err.message || '')
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

// 加载序列号：丢弃过期的异步回调（快速切换路由时旧请求不应覆盖新数据）
let loadSeq = 0
// 是否曾经开始过播放：一旦为 true，预播唤出（悬停/单击显示菜单栏）不再生效，
// seek 跳转中只显示「正在跳转…」缓冲提示，不会掉回待播放再从头播
const hasEverPlayed = ref(false)
const brokenThumbs = ref<Set<number>>(new Set())
const brokenRelatedThumbs = ref<Set<number>>(new Set())
const related = ref<MusicVideoBrief[]>([])
/** 右栏相关艺人/组合（同组合成员、同公司，可点进艺人页） */
const relatedPeople = ref<MusicVideoRelatedArtist[]>([])

/** 窄屏判定：相关作品 PC 显示 10 条、手机 5 条 */
const isNarrow = ref(false)
let narrowMq: MediaQueryList | null = null
function syncNarrow(e: { matches: boolean }) {
  isNarrow.value = e.matches
}
/** 相关作品：PC 前 10 条，手机前 5 条（保持服务端给的顺序，通常是最相关的） */
const relatedShown = computed(() => related.value.slice(0, isNarrow.value ? 5 : 10))
/** 相关艺人：随机最多 5 个（每次载入重排一次，避免每次滚动都变） */
const peopleShown = ref<MusicVideoRelatedArtist[]>([])
watch(
  relatedPeople,
  (list) => {
    const arr = [...list]
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1))
      const t = arr[i]
      arr[i] = arr[j]
      arr[j] = t
    }
    peopleShown.value = arr.slice(0, 5)
  },
  { immediate: true },
)
const collectOpen = ref(false)
const savedCollectionIds = ref<number[]>([])

const isCollected = computed(() => savedCollectionIds.value.length > 0)

function applyVideoMembership(_id: number, ids: number[]) {
  savedCollectionIds.value = ids
}

// ===== AI 视频评分（实验功能） =====
// ===== 视频章节（实验性） =====
const playerRef = ref<InstanceType<typeof VideoPlayer> | null>(null)
const chapters = ref<VideoChapter[]>([])

async function loadChapters() {
  if (!video.value) {
    chapters.value = []
    return
  }
  try {
    chapters.value = (await musicVideosApi.chapters(video.value.id)).items
  } catch {
    chapters.value = []
  }
}

function jumpToChapter(time: number) {
  const pr = playerRef.value
  if (pr && hasEverPlayed.value) {
    pr.seekTo(time)
    return
  }
  // 未开播:直接从该时间点起播(转码路径会按需重建会话)
  void startPlayAt(time)
}

// ===== 打歌期三维矩阵（右侧「打歌场次 & 机位切换」舱） =====
const stageMatrix = ref<StageMatrix | null>(null)

// ===== 移动端设计稿（2026-11-13）：标题卡 + 双分段 Pager =====
/** 只在 ≤768px 生效；桌面保持原来的「左档案 + 右场次」两栏 */
const mobileSegment = ref<'cams' | 'archive'>('cams')
const mobileActor = computed(() => relatedArtists.value[0] || null)
let mTouchX = 0
let mTouchY = 0
let mTouchIgnore = false
function onMobileTouchStart(e: TouchEvent) {
  const t = e.touches[0]
  mTouchX = t?.clientX ?? 0
  mTouchY = t?.clientY ?? 0
  // 场次机位舱里有自己的横滑内容（场次下拉、机位列表、相册墙），
  // 从这些区域开始的触摸不参与换页，否则会抢手势导致操作卡顿。
  mTouchIgnore = !!(e.target as Element | null)?.closest?.(
    '.dock-shell, .rail-extra, .v-m-header, .a-m-works',
  )
}
function onMobileTouchEnd(e: TouchEvent) {
  if (mTouchIgnore) return
  const t = e.changedTouches[0]
  if (!t) return
  const dx = t.clientX - mTouchX
  const dy = t.clientY - mTouchY
  // 只在明确横滑时切页，避免和竖向滚动抢手势
  if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.4) return
  mobileSegment.value = dx < 0 ? 'archive' : 'cams'
}

/**
 * 右舱「波浪入场」开关。
 * - 首次进入 / 切 Tab：播一次（700ms 后自动关掉）
 * - 切场次/机位：**不播** —— 每次切换 11~13 张卡重放动画，正是「切起来一顿一顿」的来源
 */
const dockWave = ref(true)
let dockWaveTimer: number | null = null
function flashDockWave(ms = 700) {
  dockWave.value = false
  void nextTick(() => {
    dockWave.value = true
  })
  if (dockWaveTimer !== null) window.clearTimeout(dockWaveTimer)
  dockWaveTimer = window.setTimeout(() => {
    dockWave.value = false
  }, ms)
}
// 档案已下沉到播放器下方，右舱只剩场次与机位：
// 首次进入播一次波浪入场，随后关掉；切场次/机位不再重放（重放会一顿一顿）。
onMounted(() => flashDockWave(900))

/**
 * 打歌期矩阵缓存：按 **song_id** 存。
 *
 * 同一首歌的所有机位/场次拿到的矩阵几乎一样（服务端按 ±45 天窗口取同曲候选），
 * 每次切换都重拉其实是在浪费一次往返。命中条件：缓存的候选里确实包含目标视频，
 * 否则（跨窗口/换歌）照常重拉，保证不会拿错数据。
 */
const stageMatrixCache = new Map<number, StageMatrix>()
async function loadStageMatrix(videoId: number) {
  const songId = video.value?.song_id ?? null
  if (songId) {
    const hit = stageMatrixCache.get(songId)
    if (hit) {
      const present = (hit.days || []).some((d) => (d.videos || []).some((v) => v.id === videoId))
      if (present) {
        // 复用缓存：只把 current 标记挪到当前视频
        stageMatrix.value = {
          ...hit,
          days: hit.days.map((d) => ({
            ...d,
            videos: d.videos.map((v) => ({ ...v, current: v.id === videoId })),
          })),
        }
        return
      }
    }
  }
  try {
    const res = await musicVideosApi.stageMatrix(videoId)
    stageMatrix.value = res
    if (songId && res?.available) stageMatrixCache.set(songId, res)
  } catch {
    stageMatrix.value = null
  }
}

// 矩阵内切换 = 普通路由跳转（videos/:uid 复用本组件），接力逻辑在路由 watch 里统一处理
function onStageSelect(uid: string) {
  if (!uid || uid === routeKey.value) return
  router.push(videoPath(uid))
}

// mm:ss（章节提示用）
function formatClock(sec?: number | null): string {
  const s = Math.max(0, Math.floor(Number(sec) || 0))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

// 当前章节名（分段条上方提示行）
const activeChapterTitle = computed(() => {
  const list = chapters.value
  const t = lastKnownTime.value
  if (!list.length) return ''
  let title = ''
  for (const c of list) {
    if (c.time <= t) title = c.title
    else break
  }
  return title
})

// 右舱底部关联标签的日期指示器
const scoreOpen = ref(false)
const scoring = ref(false)
const scoreResult = ref<VideoScore | null>(null)
const scoreError = ref('')
const scoreLoadedOnce = ref(false)

async function openScore() {
  scoreOpen.value = true
  if (scoreLoadedOnce.value || !video.value) return
  await loadLatestScore()
}

// 综合分 = 各维度平均（四舍五入）；无评分时 null
const scoreOverall = computed<number | null>(() => {
  const dims = scoreResult.value?.dims
  if (!dims) return null
  const values = Object.values(dims).map((d) => Number(d.score) || 0)
  if (!values.length) return null
  return Math.round(values.reduce((a, b) => a + b, 0) / values.length)
})

// 环形进度：满环 = 10 分
const RING_CIRC = 2 * Math.PI * 15.5
const ringDash = computed(() => {
  const overall = scoreOverall.value
  if (overall === null) return ''
  return `${(Math.max(0, Math.min(10, overall)) / 10) * RING_CIRC} ${RING_CIRC}`
})

async function loadLatestScore() {
  if (!video.value) return
  try {
    scoreResult.value = await musicVideosApi.latestAiScore(video.value.id)
    scoreLoadedOnce.value = true
  } catch {
    scoreResult.value = null
  }
}

async function runScore() {
  if (!video.value || scoring.value) return
  const cfg = readScoringAiConfig()
  if (!cfg) {
    scoreError.value = '未启用或未配置完整：请到「设置 · AI 设置 · 视频评分」填写（模型需支持图片输入）'
    return
  }
  scoring.value = true
  scoreError.value = ''
  try {
    scoreResult.value = await musicVideosApi.aiScore(video.value.id, {
      provider: cfg.provider,
      base_url: cfg.base_url,
      api_key: cfg.api_key,
      model: cfg.model,
      frames_mode: cfg.frames_mode,
    })
    scoreLoadedOnce.value = true
  } catch (e) {
    scoreError.value = (e as Error).message
  } finally {
    scoring.value = false
  }
}

// 换视频:重置评分状态。评分改为**懒加载** —— 只有真的打开评分面板才拉
// （以前每次切换都无条件打一发 ai-score，只为按钮上的小圆环）
watch(
  () => video.value?.id,
  () => {
    scoreResult.value = null
    scoreLoadedOnce.value = false
    scoreError.value = ''
  },
)

function scoreClass(score: number): string {
  if (score >= 8) return 'good'
  if (score <= 4) return 'bad'
  return 'mid'
}

async function loadVideoMemberships(videoId: number) {
  try {
    const r = await videoCollectionsApi.memberships([videoId])
    savedCollectionIds.value =
      r.memberships[String(videoId)] || r.memberships[videoId as unknown as string] || []
  } catch {
    savedCollectionIds.value = []
  }
}

// 触屏/移动设备上「用系统播放器打开」无意义（后端打开的是服务端文件，手机上看不到），隐藏入口
const isMobile = /Android|iPhone|iPad|iPod|IEMobile|Opera Mini|HarmonyOS/i.test(navigator.userAgent || '')

// 播放器画框**固定 16:9 横向**，不随片源比例变形。
// 竖屏直拍（1080×1920）也放进同一个横向画框里，画面 contain 居中、左右留黑边 ——
// 之前「竖屏片自动变成竖着的播放器」会让整页高度来回跳、切场次时机位矩阵被推走。
const mediaPaused = ref(true)
// 「本次换源是否续播」= 用户最近一次真实 play/pause 意图。
// `playing` 只代表页面已进入播放模式（暂停时仍为 true），不能拿它判断是否续播。
const shouldAutoPlay = ref(true)

function onPlayerPlay() {
  mediaPaused.value = false
  shouldAutoPlay.value = true
}
function onPlayerPause() {
  mediaPaused.value = true
  shouldAutoPlay.value = false
}

/**
 * 全屏已交给原生播放器：停掉窗口态会话并回到封面态。
 *
 * 不停的话服务端会同时跑两路（一路给暂停的网页、一路给原生），
 * 转码片源在低功耗 NAS 上直接顶不住。退出全屏后点播放，从交出去的进度续。
 */
function onNativeHandoff(position: number) {
  lastKnownTime.value = Math.max(0, position)
  playing.value = false
  playerArmed.value = false
  // 交接后回到「封面」态；hasEverPlayed 置回 false，
  // 否则 onHeroTap 会因为「播过一次」提前 return，点了没反应
  hasEverPlayed.value = false
  mediaPaused.value = true
  shouldAutoPlay.value = false
  const sid = session.value?.session_id
  session.value = null
  stopHeartbeat()
  stopWindowPoll()
  if (sid) void stopSession(sid)
}

/** 原生播放器退出：只记进度，不自动播放（用户可能只是退出全屏）。 */
function onNativePlayerClosed(position: number, ended: boolean) {
  if (position > 0) lastKnownTime.value = position
  // 退出全屏后直接把控制栏亮出来，一次点击就能从原位置继续
  playerArmed.value = true
  void ended
}

const routeKey = computed(() => String(route.params.uid || ''))
const id = computed(() => video.value?.id ?? 0)

// 画质阶梯（与后端 transcoding_profile.py QUALITY_LADDER 一致）
// ⚠ 口径 = **短边**：max_h 是「短边上限」、max_w 是「长边上限」，档位表按横屏书写。
// 竖屏直拍 2160×3840 与横屏 3840×2160 同属 2160p（与后端 + 统计页 resolution_tier 同一套规则）。
// v3.4.4：转码档位收敛为 2160p / 1080p / 720p 三档（与后端 QUALITY_LADDER 一致）。
const QUALITY_LADDER_FRONT = [
  { name: 'original', max_h: 0, max_w: 0 },
  { name: '2160p', max_h: 2160, max_w: 3840 },
  { name: '1080p', max_h: 1080, max_w: 1920 },
  { name: '720p', max_h: 720, max_w: 1280 },
]

// 播放前可用画质：根据视频分辨率前端计算（与后端 available_qualities 逐字同口径）。
// 注意：① 不再用 window.screen 裁剪——Chrome 中 window.screen.height 是 CSS 像素，
// 受 Windows DPI 缩放影响（1080p 屏 + 150% 缩放 → 720），会把 1080p 误过滤掉；
// ② 必须按**短边**判，不能按 height —— 按 height 会让竖屏片看到「比自己还高」的档位
// （1080×1920 出现 1440p），选了反而被降分辨率转码（缩到 810×1440），
// 而横屏选同名档位却是直出；宽高任一缺失时不猜档位，只给 original。
const preQualities = computed(() => {
  const v = video.value
  if (!v || !v.width || !v.height || v.width <= 0 || v.height <= 0) return ['original']
  const shortSide = Math.min(v.width, v.height)
  const result = ['original']
  for (const r of QUALITY_LADDER_FRONT.slice(1)) {
    if (shortSide < r.max_h) continue
    result.push(r.name)
  }
  return result
})

// 可用画质：播放后由服务端 session.available_qualities 提供
const qualities = computed(() => session.value?.available_qualities ?? preQualities.value)

const hwInfo = ref<{ enabled: boolean; method: string }>({ enabled: false, method: 'auto' })

async function refreshHwAccel() {
  try {
    const r = await systemApi.hwAccel()
    hwInfo.value = { enabled: r.enabled, method: r.method }
  } catch {
    hwInfo.value = { enabled: false, method: 'auto' }
  }
}

const transcodePromptShow = ref(false)
const transcodePromptDetail = ref('')
const transcodePromptQuality = ref('1080p')
const transcodePromptOptions = ref<{ label: string; value: string }[]>([])
const transcodePromptResumeAt = ref(0)
let originalDecodeFailed = false

// ===== 原画直出失败记忆（v3.4.5）=====
// 现象：4K60p 竖屏 VP9 这类「解码规格远超普通硬解」的片源，原画直出在软解
// 机器上必然中途窒息（实测 Chrome 软解要持续吃满 ~10 核，弱机直接 media
// error），于是每次播放都走「先试原画 → 中途失败 → 提示已自动转码」一遍，
// 用户感知就是「总是提示无法原画播放」。浏览器没有 API 能查询自身硬解能力，
// 服务端也无法预判 —— 所以按「视频编码 + 分辨率档」记住实际失败（30 天），
// 同类片进入页面默认直接选转码档位；原画仍可手动选。
// v3.4.9 键升版：v1 条目全部写于「AV1/VP9 直出被旧 HLS 内核兜底 avc1 打挂」的年代，
// 直出修复后那些「失败」不再是真实能力边界 —— 整体作废重来，避免同类片被旧记忆
// 无谓地压到转码档。
// v3.5.0：HLS 内核换 hls.js，AV1/VP9 可直出，真实失败大幅减少；记忆 TTL 收紧到 7 天，
// 只对「原画直出 + 非转码」情形下的解码失败标记（见 onPlayerState），避免陈旧误判
// 长时间把同类片压到转码档。原画始终可手动选（弹窗提供「仍尝试原画」）。
// ★ v3：v2 时期把「transcode + original」误判成「原画失败」，凡是 MKV 源
// （即几乎所有本地库）都被记了失败，7 天内默认压到转码档 —— 表现为
// 「点原画播出来是 720P」。升级 key 让这批陈旧误判一次性作废。
// ★ v4→v5：旧逻辑把网络错误/切片超时也记成「原画解码失败」，这类污染条目
// 会让同类片 7 天内无端绕开原画；升级 key 一次性清掉，之后只记 code 3/4。
const ORIG_FAIL_KEY = 'kpml_origfail_classes_v5'
const ORIG_FAIL_TTL_MS = 7 * 24 * 3600 * 1000

function origFailClassKey(v: MusicVideo | null): string {
  if (!v) return ''
  const codec = String(v.video_codec || '').toLowerCase()
  if (!codec) return ''
  const w = Number(v.width) || 0
  const h = Number(v.height) || 0
  if (w <= 0 || h <= 0) return ''
  const short = Math.min(w, h)
  const tier = RESOLUTION_TIERS.find((t) => short >= t.floor)
  return tier ? `${codec}:${tier.key}` : ''
}

function loadOrigFailMap(): Record<string, number> {
  try {
    const raw = JSON.parse(localStorage.getItem(ORIG_FAIL_KEY) || '{}') as Record<string, unknown>
    const now = Date.now()
    const out: Record<string, number> = {}
    for (const [k, ts] of Object.entries(raw || {})) {
      if (typeof ts === 'number' && now - ts < ORIG_FAIL_TTL_MS) out[k] = ts
    }
    return out
  } catch {
    return {}
  }
}

function markOrigFailClass(v: MusicVideo | null) {
  const key = origFailClassKey(v)
  if (!key) return
  const map = loadOrigFailMap()
  map[key] = Date.now()
  try {
    localStorage.setItem(ORIG_FAIL_KEY, JSON.stringify(map))
  } catch {
    /* 存储满 / 隐私模式：失败记忆不可用，退化为每次提示 */
  }
}

function hasOrigFailClass(v: MusicVideo | null): boolean {
  const key = origFailClassKey(v)
  return !!key && !!loadOrigFailMap()[key]
}

function transcodeChoices(list?: string[] | null): string[] {
  return (list || []).filter((q) => q !== 'original')
}

function suggestedTranscodeQuality(list: string[]): string {
  const cap =
    hwInfo.value.enabled && (hwInfo.value.method === 'vaapi' || hwInfo.value.method === 'qsv')
      ? 1080
      : 720
  const ladder = QUALITY_LADDER_FRONT.filter((r) => r.name !== 'original')
  const found = list.find((q) => {
    const entry = ladder.find((r) => r.name === q)
    return !!entry && entry.max_h > 0 && entry.max_h <= cap
  })
  return found ?? list[list.length - 1] ?? '720p'
}

function openTranscodePrompt(detail: string, qualities: string[], resumeAt: number) {
  const choices = transcodeChoices(qualities.length ? qualities : preQualities.value)
  transcodePromptDetail.value = detail.replace(/^Transcode[：:]\s*/, '') || '浏览器无法直接播放此容器或编码。'
  transcodePromptOptions.value = choices.map((q) => ({
    label: QUALITY_LABEL[q] || q.toUpperCase(),
    value: q,
  }))
  transcodePromptQuality.value = suggestedTranscodeQuality(choices)
  transcodePromptResumeAt.value = resumeAt
  transcodePromptShow.value = true
}

let confirmingTranscode = false

function cancelTranscodePrompt() {
  transcodePromptShow.value = false
  if (confirmingTranscode) return
  activeQuality.value = 'original'
  if (!session.value) {
    playing.value = false
    hasEverPlayed.value = false
    mediaPaused.value = true
  }
}

async function confirmTranscodePrompt() {
  const q = transcodePromptQuality.value
  if (!q) return
  confirmingTranscode = true
  transcodePromptShow.value = false
  userPickedQuality.value = true
  originalDecodeFailed = false
  activeQuality.value = q
  try {
    await startPlayAt(transcodePromptResumeAt.value)
  } finally {
    confirmingTranscode = false
  }
}

async function fallbackTranscodePlay(
  detail: string,
  qualities: string[],
  resumeAt: number,
  seq = seekSeq,
) {
  const choices = transcodeChoices(qualities.length ? qualities : preQualities.value)
  const q = suggestedTranscodeQuality(choices)
  if (!q) {
    openTranscodePrompt(detail, qualities, resumeAt)
    if (!session.value) {
      playing.value = false
      hasEverPlayed.value = false
      mediaPaused.value = true
    }
    return
  }
  userPickedQuality.value = true
  activeQuality.value = q
  // 不再弹 toast：这条信息（为什么转码）改由播放器左上角的常驻徽标承载，
  // 悬停可看完整原因 —— toast 3 秒就消失，长句根本来不及读。
  try {
    const next = await createSessionWithRetry(q, resumeAt)
    if (seq !== seekSeq) {
      await stopSession(next.session_id)
      return
    }
    await attachPlaybackSession(next, resumeAt, seq)
  } catch (e) {
    if (seq !== seekSeq) return
    openTranscodePrompt((e as Error).message || detail, choices, resumeAt)
  }
}

async function attachPlaybackSession(next: PlaybackSession, target: number, seq: number) {
  const prev = session.value
  if (next.play_mode !== 'transcode') {
    activeQuality.value = 'original'
  }
  seekInto.value = Math.max(0, target - (next.start_offset || 0))
  if (prev && prev.session_id !== next.session_id) {
    await stopSession(prev.session_id)
  }
  if (seq !== seekSeq) {
    await stopSession(next.session_id)
    return
  }
  originalDecodeFailed = false
  session.value = next
  playing.value = true
  hasEverPlayed.value = true
  // 外部承载没有「已转出范围」的概念（B 站自己管缓冲），不轮询窗口；
  // 若之前是转码会话，顺手停掉残留的定时器
  if (next.play_mode === 'bilibili') {
    stopWindowPoll()
    return
  }
  // 新会话 = 新的「已可播范围」，重新开始轮询（内部会先归零）
  startWindowPoll()
}

/**
 * 外部承载（B 站 iframe）。
 *
 * 触发条件由后端决定（本地没有片源 + 已绑定 status=ok 的 BV），前端只负责渲染：
 * 非空时用 iframe 替代 <VideoPlayer>，本地流字段此时全是 null。
 * 注意不要用 `video.publish.playable` 判断——那是「这个视频有没有可播的源」，
 * 而这里是「这一路会话是不是外部承载」，两者在切换画质/重建会话时不同步。
 */
const externalPlay = computed(() => {
  const s = session.value
  if (!s || s.play_mode !== 'bilibili' || !s.embed_url) return null
  return {
    embedUrl: s.embed_url,
    watchUrl: s.watch_url || '',
    bvid: s.embed_bvid || '',
    page: s.embed_page || 1,
  }
})

const hasSourceFile = computed(
  () =>
    !!(
      video.value &&
      // 本地有文件 → 原逻辑；在线版无文件但绑定了可播的 B 站视频 → 也算有源
      (video.value.file_path || video.value.publish?.playable)
    ),
)

// ===== B 站分发（P0 在线版播放源）=====
const biliInput = ref('')
const biliSaving = ref(false)

function publishStatusLabel(status?: string) {
  const map: Record<string, string> = {
    ok: '可播放',
    reviewing: '审核中',
    locked: '已锁定',
    missing: '已失效',
    pending: '待发布',
  }
  return map[status || ''] || '未知状态'
}

function publishOriginLabel(origin?: string) {
  const map: Record<string, string> = {
    self_upload: '自己投稿',
    third_party: '复用他人',
    manual: '手动绑定',
  }
  return map[origin || ''] || '手动绑定'
}

async function saveBili() {
  const raw = biliInput.value.trim()
  if (!raw || !video.value) return
  biliSaving.value = true
  try {
    // 先让后端归一化（完整地址 / 分享文案 / 裸 BV 都认），再落库，
    // 避免前端自己写一套 BV 正则跟后端漂移。
    const parsed = await musicVideosApi.parsePublish(video.value.id, raw)
    if (parsed.error || !parsed.bvid) {
      message.warning(parsed.error || '没找到 BV 号')
      return
    }
    const row = await musicVideosApi.putPublish(video.value.id, {
      bvid: parsed.bvid,
      page: parsed.page,
      origin: 'manual',
      status: 'ok',
    })
    video.value.publish = row
    biliInput.value = ''
    message.success('已绑定 B 站视频')
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    biliSaving.value = false
  }
}

async function unbindBili() {
  if (!video.value) return
  try {
    await musicVideosApi.deletePublish(video.value.id)
    video.value.publish = null
    message.success('已解绑')
  } catch (e) {
    message.error((e as Error).message)
  }
}

const sources = computed(() => {
  const s = session.value
  if (!s) return []
  const label = qualityLabel(activeQuality.value)
  if (s.manifest_url) {
    // 兜底：没有 MSE 的浏览器（vivo/华为自带浏览器）走这条渐进转码流，
    // 不经过 HLS。地址从会话 id 推导，省得为一个边缘场景改后端 schema。
    const m = s.manifest_url.match(/\/sessions\/(\d+)\//)
    // ★ 必须沿用清单里的 ?exp=&sig=：这些内核会把 <video> 劫持给**系统原生播放器**，
    //   那是没有浏览器 Cookie 的独立进程。只带登录态的媒体地址在它手里必然 401，
    //   表现就是厂商自己的「视频播放失败，可尝试以下操作 / 刷新网页」错误页。
    const q = s.manifest_url.includes('?')
      ? s.manifest_url.slice(s.manifest_url.indexOf('?'))
      : ''
    // ⚠ compat/progressive 端点只接受 transcode 会话（direct_stream 请求会 400）。
    // 不能因为 UA 命中就无条件下发，否则「MSE 明明可用、服务端判了 direct_stream」
    // 的设备会被前端强制切到 compat → 400 → 黑屏。
    const canMp4Fallback = s.play_mode === 'transcode' && !!m
    return [
      {
        src: s.manifest_url,
        type: 'application/x-mpegURL',
        label,
        fallbackSrc: canMp4Fallback
          ? `/api/playback/sessions/${m[1]}/progressive.mp4${q}`
          : undefined,
        // 兼容版：普通 MP4（moov 前置、有真实时长、支持 Range），给被劫持的内核用
        compatSrc: canMp4Fallback
          ? `/api/playback/sessions/${m[1]}/compat.mp4${q}`
          : undefined,
      },
    ]
  }
  if (s.stream_url) {
    const ext = (s.container || '').toLowerCase()
    const type =
      ext === 'webm' || ext === 'mkv' ? 'video/webm'
      : ext === 'mp4' || ext === 'm4v' || ext === 'mov' ? 'video/mp4'
      : 'video/mp4'
    return [{ src: s.stream_url, type, label, fallbackSrc: undefined, compatSrc: undefined }]
  }
  return []
})

const activeLabel = computed(() => qualityLabel(activeQuality.value))

function qualityLabel(key: string) {
  return QUALITY_LABEL[key] || key.toUpperCase()
}

/**
 * 把「播放失败」的现场写回服务端日志。
 * 手机自带浏览器上看不到控制台，而服务端只看到「没有请求」—— 中间发生了什么
 * 只能靠这条回传：MSE 有无、hls 可用性、媒体元素错误码、当前源与兜底地址。
 */
function reportDiag(kind: string, extra: Record<string, unknown> = {}) {
  try {
    const probe = document.createElement('video')
    let mseAvc: boolean | string = 'no-ms'
    try {
      if (typeof MediaSource !== 'undefined') {
        mseAvc = !!MediaSource.isTypeSupported('video/mp4; codecs="avc1.42E01E"')
      }
    } catch {
      mseAvc = 'err'
    }
    const body = {
      kind,
      ua: (navigator.userAgent || '').slice(0, 200),
      mse: typeof MediaSource !== 'undefined',
      mseAvc,
      nativeHls: !!probe.canPlayType('application/vnd.apple.mpegurl'),
      session: session.value
        ? {
            id: session.value.session_id,
            mode: session.value.play_mode,
            q: session.value.quality,
          }
        : null,
      sources: sources.value.map((s) => ({
        src: String(s.src || '').slice(0, 70),
        fb: !!s.fallbackSrc,
      })),
      ...extra,
    }
    void fetch('/api/playback/client-log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).catch(() => {})
  } catch {
    /* 诊断本身失败不能影响播放 */
  }
}

function onPlayerState(payload: {
  buffering: boolean
  error: string | null
  errorKind?: 'network' | 'decode' | 'capability' | 'unknown'
  errorCode?: number
}) {
  playerBuffering.value = payload.buffering
  playerError.value = payload.error || ''
  if (payload.error) {
    reportDiag('player-state', {
      error: payload.error.slice(0, 160),
      kind: payload.errorKind || 'unknown',
      code: payload.errorCode || 0,
    })
  }
  if (payload.error && /401|未登录|unauthorized/i.test(payload.error)) {
    router.replace({ name: 'login', query: { redirect: route.fullPath } })
    return
  }
  // 只对确定的解码/格式失败自动降级并写「原画失败」记忆。
  // 断网、切片 5xx、超时属于临时故障，自动转码既救不了，还会把同类片
  // 在 7 天内全部误压到低档位。
  const decodeFailure =
    payload.errorKind === 'decode' ||
    payload.errorCode === 3 ||
    payload.errorCode === 4
  if (
    payload.error &&
    decodeFailure &&
    !originalDecodeFailed &&
    session.value &&
    activeQuality.value === 'original' &&
    session.value.play_mode !== 'transcode'
  ) {
    originalDecodeFailed = true
    markOrigFailClass(video.value)
    const s = session.value
    const quals = s.available_qualities?.length ? s.available_qualities : preQualities.value
    void stopSession(s.session_id)
    session.value = null
    void fallbackTranscodePlay(
      '原画直出失败，已自动转码播放。',
      quals,
      lastKnownTime.value || 0,
    )
  }
}

function onTimeUpdate(currentTime: number) {
  lastKnownTime.value = currentTime
}

// 用户拖动进度条完成（Emby 式任意 seek）。
// - 未播放：直接 startPlayAt(目标)
// - Direct Play：原生 seek 已跳转，无需重建会话
// - 转码：按目标时间新建会话（-ss target），停旧会话，换源
async function onSeeked(targetTime: number) {
  const target = Math.max(0, targetTime)
  const s = session.value
  const keepPaused = !shouldAutoPlay.value
  playing.value = true
  hasEverPlayed.value = true
  if (!s) {
    await startPlayAt(target, { autoPlay: !keepPaused })
    return
  }
  if (s.play_mode === 'direct_play') return
  if (Math.abs(target - lastKnownTime.value) < 1.2) return
  lastKnownTime.value = target
  isSeeking.value = true
  playerBuffering.value = true
  const uiGen = ++seekUiGen
  if (seekDebounceTimer !== null) window.clearTimeout(seekDebounceTimer)
  seekDebounceTimer = window.setTimeout(() => {
    seekDebounceTimer = null
    void (async () => {
      try {
        await startPlayAt(target, { autoPlay: !keepPaused })
      } finally {
        if (uiGen === seekUiGen) isSeeking.value = false
      }
    })()
  }, SEEK_DEBOUNCE_MS)
}

// 封面应用后递增，强制刷新 poster 缓存
const posterBust = ref(0)
const poster = computed(() => {
  if (!settings.showCovers || !video.value || brokenThumbs.value.has(video.value.id)) return undefined
  return musicVideosApi.thumbnailUrl(id.value, posterBust.value || undefined, IMG_W_FULL)
})

/**
 * 实际显示的封面：**新图 decode 完再替换**。
 *
 * 直接绑定 `poster` 时，切场次/机位瞬间 src 就换了，但新图要 150ms 左右才解码完
 * （NAS 上首次还要现生成变体），中间 hero 是空白 —— 实测 4~5 帧空白。
 * 这里保留旧图直到新图可用，切换视觉上就是「直接换过去」而不是「闪一下」。
 */
const displayedPoster = ref('')
let posterSwapToken = 0
watch(
  poster,
  (src) => {
    const token = ++posterSwapToken
    if (!src) {
      displayedPoster.value = ''
      return
    }
    const img = new Image()
    img.onload = () => {
      if (token === posterSwapToken) displayedPoster.value = src
    }
    img.onerror = () => {
      if (token === posterSwapToken) displayedPoster.value = src
    }
    img.src = src
    // 命中缓存时 onload 可能已经错过：补一次同步判断
    if (img.complete && img.naturalWidth > 0 && token === posterSwapToken) {
      displayedPoster.value = src
    }
  },
  { immediate: true },
)

/** 矩阵胶囊 hover 预取下一张封面：真点下去时图片已在缓存里，切图零空窗 */
const prefetchedPosters = new Set<number>()
function prefetchPoster(videoId: number) {
  if (!videoId || prefetchedPosters.has(videoId)) return
  prefetchedPosters.add(videoId)
  const img = new Image()
  img.src = musicVideosApi.thumbnailUrl(videoId, undefined, IMG_W_FULL)
}

function onThumbError() {
  if (video.value) brokenThumbs.value = new Set(brokenThumbs.value).add(video.value.id)
}

// ===== 手动选帧封面 =====
const coverPickerShow = ref(false)

function onCoverApplied() {
  posterBust.value += 1
  // 刷新详情拿到 cover_manual 状态（按钮恢复自动入口依赖它）
  void load({ keepPlayback: true })
}

function onCoverReset() {
  posterBust.value += 1
  void load({ keepPlayback: true })
}

function onRelatedThumbError(itemId: number) {
  brokenRelatedThumbs.value = new Set(brokenRelatedThumbs.value).add(itemId)
}

async function load(opts: { keepPlayback?: boolean } = {}) {
  const seq = ++loadSeq
  const param = routeKey.value.trim()
  // 切换前是否**真的在出画**：暂停时切场次/机位应保持暂停，不能因为
  // `playing`（页面播放模式）仍为 true 就自动出声。
  const wasPlaying = !mediaPaused.value && !opts.keepPlayback
  if (!param) {
    if (seq !== loadSeq) return
    notFound.value = true
    video.value = null
    return
  }
  loading.value = true
  notFound.value = false
  try {
    const mv = await fetchByRouteParam(param, musicVideosApi.getByUid, musicVideosApi.get)
    if (seq !== loadSeq) return
    video.value = mv
    if (mv.uid && mv.uid !== param) {
      router.replace(videoPath(mv.uid))
    }
    if (!opts.keepPlayback) {
      playing.value = false
      hasEverPlayed.value = false
      mediaPaused.value = true
      shouldAutoPlay.value = false
      playerBuffering.value = false
      playerError.value = ''
      // 切场次/机位：右舱内容直接换，不重放波浪入场
      dockWave.value = false
    }
    if (!userPickedQuality.value) {
      // 原画直出失败过的同类片（编码+分辨率档）：默认直接转码档位，
      // 不再每次都「先失败 → 提示 → 转码」（见 ORIG_FAIL_KEY 注释）
      const choices = transcodeChoices(preQualities.value)
      const sug = hasOrigFailClass(mv) && choices.length ? suggestedTranscodeQuality(choices) : ''
      activeQuality.value = sug || 'original'
    }
    originalDecodeFailed = false
    transcodePromptShow.value = false
    collectOpen.value = false
    void loadVideoMemberships(mv.id)
    void loadSubtitles(mv.id)
  } catch (e) {
    if (seq !== loadSeq) return
    video.value = null
    notFound.value = true
    message.error((e as Error).message)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
  if (seq !== loadSeq) return
  await loadRelated()
  void loadChapters()
  const loaded = video.value
  if (!loaded) return
  void loadStageMatrix(loaded.id)
  // 切场次/机位：不继承上一段进度，但**保持播放**（从头播）。
  // 关键：必须无条件重建播放会话 —— 否则 <video> 仍指向旧机位的 session，
  // 页面已经换成新机位、画面却还在播上一个（实测会话 579 切完后继续播了 6s）。
  if (wasPlaying && hasSourceFile.value) {
    lastKnownTime.value = 0
    void startPlayAt(0)
  }
}

/** 外挂同名字幕轨（后端统一转 VTT）。加载失败静默 —— 字幕是锦上添花，不能挡播放 */
async function loadSubtitles(videoId: number) {
  try {
    subtitleTracks.value = (await musicVideosApi.subtitles(videoId)).items
  } catch {
    subtitleTracks.value = []
  }
}

/**
 * 相关作品缓存：按 song_id 存。
 * 同一首歌的「相关作品」列表与当前看的是哪个机位无关，切机位时直接复用，
 * 不再每次多打一发 `music-videos?page_size=6&song_id=…`。
 */
const relatedCache = new Map<number, MusicVideoRelated>()
function applyRelated(res: MusicVideoRelated) {
  const current = video.value?.id
  const works = (res.works || []).filter((w) => w.video.id !== current)
  related.value = works.map((w) => w.video)
  relatedPeople.value = res.artists || []
}
async function loadRelated() {
  const v = video.value
  if (!v) return
  const cached = relatedCache.get(v.id)
  if (cached) {
    applyRelated(cached)
    return
  }
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), 8000)
  try {
    const res = await musicVideosApi.related(v.id)
    relatedCache.set(v.id, res)
    applyRelated(res)
  } catch {
    related.value = []
    relatedPeople.value = []
  } finally {
    window.clearTimeout(timer)
  }
}

// ===== 预播态交互 =====
// 预播 = 纯封面（不挂播放器组件）。唤出（armed）后显示播放器但不出画：
// PC 悬停视频区域唤出、点击即播；移动端单击唤出、双击（350ms 内两次点击）即播。
const playerArmed = ref(false)
const playerVisible = computed(() => playing.value || playerArmed.value)
// 悬停语义只在真指针设备启用（触屏设备 matchMedia('hover:hover') 为 false，走单击唤出）
const canHover =
  typeof window !== 'undefined' &&
  window.matchMedia('(hover: hover) and (pointer: fine)').matches

function onHeroEnter() {
  if (!canHover || !hasSourceFile.value || playing.value || hasEverPlayed.value) return
  playerArmed.value = true
}

function onHeroLeave() {
  // 仅预播态回退：播放过一次后播放器常驻，不再收起
  if (!canHover || playing.value || hasEverPlayed.value) return
  playerArmed.value = false
}

function onHeroTap() {
  // 单击只负责唤出菜单栏（不直接播放）—— PC 与触屏一致
  if (!hasSourceFile.value || playing.value || hasEverPlayed.value) return
  playerArmed.value = true
}

/** 封面状态或菜单栏已出现时，双击播放器区域 = 起播 */
function onHeroDblClick() {
  if (!hasSourceFile.value || playing.value || hasEverPlayed.value) return
  startPlay()
}

async function startPlay() {
  if (!video.value) return
  playing.value = true
  hasEverPlayed.value = true
  shouldAutoPlay.value = true
  // 已有会话且源就绪：只把 pending 关掉，播放器 watch 会接着 play()
  // 外部承载没有 sources（iframe 自带播放控制），也要走这个早退分支
  if (session.value && (sources.value.length || externalPlay.value)) return
  await startPlayAt(lastKnownTime.value || 0, { autoPlay: true })
}

// 在指定时间点创建会话并开始播放（Emby 式任意 seek 的统一入口）。
// 先把 playing/hasEverPlayed 置位，等会话时不要掉回待播放遮罩。
async function createSessionWithRetry(quality: string, startSeconds: number): Promise<PlaybackSession> {
  try {
    return await createSession(quality, startSeconds)
  } catch (e) {
    if (!isBusyError(e)) throw e
    await sleep(1500)
    return await createSession(quality, startSeconds)
  }
}

async function startPlayAt(
  t: number,
  opts: { autoPlay?: boolean } = {},
) {
  if (!video.value) return
  const target = Math.max(0, t)
  const requested = activeQuality.value
  const wantOriginal = requested === 'original'
  playing.value = true
  hasEverPlayed.value = true
  // 这里不再提前把 mediaPaused 置 false：会话还没建、媒体还没出画。
  // 观看计时只由真实 @play 启动，避免「只是打开页面 / 自动播放被拒」也计观看。
  shouldAutoPlay.value = opts.autoPlay ?? true
  sessionLoading.value = true
  playerError.value = ''
  const seq = ++seekSeq
  try {
    const next = await createSessionWithRetry(requested, target)
    if (seq !== seekSeq) {
      await stopSession(next.session_id)
      return
    }
    // ★ 只有服务端**明确降档**（quality !== original）才算「原画不可用」。
    // `play_mode === 'transcode'` 本身不代表降档：MKV/无 MSE 设备上，
    // 「transcode + original」是正常且最优的路径 —— 服务端按源尺寸输出，
    // 前端再走 compat remux 成 MP4 直连（实测 4K 5 秒、8K 13 秒，零重编码）。
    // 旧条件只判 play_mode，等于把所有「需要换容器」的片子都强行降档
    // （业主 2026-11-09 实拍：点原画播出来是 720P h264）。
    if (wantOriginal && next.play_mode === 'transcode' && next.quality !== 'original') {
      // ⚠ 这里**不记** markOrigFailClass：服务端按「分辨率超上限」降档属于
      // 能力判定，不是解码失败。记进去会让这个编码+档位的所有片子
      // 默认压到转码档，且用户再也没机会试原画（实拍症状：8K 永远 720P）。
      // 真正的解码失败记忆只由 onPlayerState 的 originalDecodeFailed 负责。
      await stopSession(next.session_id)
      await fallbackTranscodePlay(
        next.decision_reason || '原画无法直出，已自动转码播放。',
        next.available_qualities,
        target,
        seq,
      )
      return
    }
    await attachPlaybackSession(next, target, seq)
  } catch (e) {
    if (seq !== seekSeq) return
    const busy = isBusyError(e)
    const text = busy ? '转码繁忙，请稍后再试' : (e as Error).message
    // 错误改为播放器下方常驻显示（playerError），不再弹一闪而过的 toast
    playerError.value = text
    if (!session.value) {
      playing.value = false
      hasEverPlayed.value = false
    }
  } finally {
    if (seq === seekSeq) sessionLoading.value = false
  }
}

// ===== Playback Session（统一播放链路，播放策略由服务端决定） =====
/**
 * 播放模式徽标。
 *
 * ⚠ `transcode + original` **不等于降质转码**：设备端能解源编码时服务端只换容器
 * （remux，`-c:v copy`），画面逐像素与原画一致。把它标成「转码」会让业主以为
 * 被偷偷降质了（2026-11-09 实测反馈）。只有真的压到低档位才写「转码」。
 */
function playModeLabel(mode: string, quality?: string) {
  if (mode === 'direct_play') return '原画直连'
  if (mode === 'direct_stream') return '原画直出'
  if (mode === 'bilibili') return 'B 站外链'
  // 只有明确 original 才敢说原画；quality 缺失时保守按转码显示
  if (quality === 'original') return '原画'
  return '转码'
}

// 描述浏览器能力：供服务端做 Direct Play / Direct Stream / Transcode 决策。
//
// v3.5.0：canPlayType / MediaSource.isTypeSupported 彻底替换为
// navigator.mediaCapabilities.decodingInfo()（MediaCapabilities API）——
// 它基于浏览器真实解码管线（含硬解）评估 supported/smooth/powerEfficient，
// 而 canPlayType 只是 MIME 嗅探查询，对 AV1/HEVC 常漏报，导致大量误判转码。
//
// v3.5.1：decodingInfo 自身也会漏报（HEVC 依赖平台硬解查询，个别驱动/GPU 组合
// 返回 supported=false 但 file:// 实际可硬解播放）—— HEVC/VP9 判定加回
// canPlayType=='probably' 兜底（见下方 canProbably），两层探测取并集；
// HEVC 探测串 L93（Level 3.1，最高仅 720p）修正为 L153（Level 5.1，覆盖 4K）。
//
// 探测口径：
//   - 'file'         渐进直连（Direct Play 走的路径）
//   - 'media-source'  MSE（Direct Stream fMP4 HLS 走的路径）
//   一个编码任一路径可解即上报（服务端按交付方式决策）。
//   - 探测分辨率取片源实际尺寸（高度按档位向上归并 2160/1440/1080/720/480）：
//     同一编码 4K 与 1080p 的硬解能力可能不同。
//   - 模块级缓存：同档位+编码只探测一次（decodingInfo 异步且有开销）。
//   - 无该 API 的旧浏览器回退 canPlayType 粗判（legacyClientCapabilities）。
// iPhone/iPad 原生管线（无 MSE，原生 HLS）：VP9/AV1/Opus/Vorbis/FLAC 不可解，照旧剔除。
interface DecodeProbe {
  supported: boolean
  smooth: boolean
  powerEfficient: boolean
}

const probeCache = new Map<string, DecodeProbe>()
const UNSUPPORTED_PROBE: DecodeProbe = { supported: false, smooth: false, powerEfficient: false }

async function probeDecoding(
  type: MediaDecodingType,
  config: VideoConfiguration | AudioConfiguration,
): Promise<DecodeProbe> {
  const key = `${type}|${JSON.stringify(config)}`
  const cached = probeCache.get(key)
  if (cached) return cached
  let result = UNSUPPORTED_PROBE
  try {
    const mc = navigator.mediaCapabilities
    if (typeof mc?.decodingInfo === 'function') {
      const r = await mc.decodingInfo({ type, ...config })
      result = {
        supported: !!r.supported,
        smooth: !!r.smooth,
        powerEfficient: !!r.powerEfficient,
      }
    }
  } catch {
    // 探测异常按不支持处理，由服务端转码兜底
  }
  probeCache.set(key, result)
  return result
}

/** 视频探测配置：高度按档位向上归并（缓存粒度有限，避免每个视频一个 key）。 */
/** 编码名归一：与服务端 `_VIDEO_ALIASES` 同口径，避免 mkv 里的 h265/avc 认不出 */
function normalizeCodec(raw?: string | null): string {
  const n = (raw || '').toLowerCase().trim()
  if (n === 'avc' || n === 'avc1' || n === 'avc3' || n === 'h264') return 'h264'
  if (n === 'h265' || n === 'hev1' || n === 'hvc1' || n === 'hevc') return 'hevc'
  if (n === 'av01' || n === 'av1') return 'av1'
  if (n === 'vp09' || n === 'vp9') return 'vp9'
  return n
}

function probeVideoConfig(width?: number, height?: number): VideoConfiguration {
  const srcW = Math.max(2, Math.round(width || 1920))
  const srcH = Math.max(2, Math.round(height || 1080))
  const short = Math.min(srcW, srcH)
  // 档位按**短边**选（与后端 resolve_target_dimensions 同口径）。
  // ★ 必须含 4320（8K）：业主库内实测 8K 源 93 条（7680×4320 AV1）。
  //   上限只到 2160 时，8K 会被探成 3840×2160 → 上报的 max 小于源 →
  //   服务端判「超上限」→ 降档转码（实拍表现：点原画播出来 720P）。
  const TIERS = [480, 720, 1080, 1440, 2160, 4320]
  const tier = TIERS.find((t) => short <= t) ?? 4320
  const scale = short > tier ? tier / short : 1
  const w = Math.max(16, Math.round((srcW * scale) / 2) * 2)
  const h = Math.max(16, Math.round((srcH * scale) / 2) * 2)
  const bitrate =
    tier >= 4320 ? 40_000_000
      : tier >= 2160 ? 15_000_000
        : tier >= 1440 ? 9_000_000
          : tier >= 1080 ? 6_000_000
            : tier >= 720 ? 3_500_000
              : 1_800_000
  return {
    contentType: 'video/mp4',
    width: w,
    height: h,
    bitrate,
    framerate: 30,
  }
}

/**
 * 上报给服务端的「解码尺寸上限」。
 *
 * ★ 2026-11-09 业主口径：**设备端能解就别转码，服务端转码只是兜底**。
 * 旧实现把移动端写死成 1920×1080，结果平板明明硬解 4K，服务端也只给
 * 降档转码 —— 低功耗 NAS 软编顶不住，表现就是「安卓平板一直转圈」。
 *
 * ⚠ 字段语义必须按服务端口径：
 *   `max_height` = **短边**上限、`max_width` = **长边**上限
 *   （见 `playback_resolver._source_exceeds_client`）。
 *   竖屏 2026×3840 要报 `{max_width: 3840, max_height: 2026}`；
 *   直接传源宽高会让长边 3840 撞上 2026 的"宽度上限"，被判超限降档。
 *
 * 判据：源编码在设备能力表里 → 允许到源尺寸（`probeVideoConfig` 已按源算好、
 * 只降不升）；解不了才退回 1080 保守档。
 */
function decodeCapFor(
  dims: VideoConfiguration,
  sourceCodec: string,
  codecs: string[],
): { max_width: number; max_height: number } {
  const codec = normalizeCodec(sourceCodec)
  const decodable = !codec || codecs.includes(codec)
  if (!decodable) return { max_width: 1920, max_height: 1080 }
  const short = Math.min(dims.width, dims.height)
  const long = Math.max(dims.width, dims.height)
  return { max_width: long, max_height: short }
}

async function clientCapabilities(): Promise<Record<string, unknown>> {
  if (typeof document === 'undefined') return {}
  // 平板客户端：直接用原生 MediaCodecList 探测的真实能力上报。
  // 服务端据此走**直连**（默认不转码）；只有用户在画质菜单里主动选非「原画」档，
  // 才会带 requested_quality 让服务端转码 —— 转码是用户的选择，不是兜底。
  const nativeCaps = nativeCapabilities()
  if (nativeCaps && nativeCaps.supported_video_codecs) return nativeCaps
  const ua = typeof navigator !== 'undefined' ? navigator.userAgent || '' : ''
  const isIOS = /iPhone|iPad|iPod/i.test(ua)
  const isAppleSafari =
    isIOS || (/Safari/i.test(ua) && !/Chrome|CriOS|Chromium/i.test(ua))

  // 无 MediaCapabilities API 的旧浏览器：回退 canPlayType 粗判（旧口径整体保留）
  if (typeof navigator.mediaCapabilities?.decodingInfo !== 'function') {
    return legacyClientCapabilities(
      ua,
      isIOS,
      isAppleSafari,
      probeVideoConfig(
        video.value?.width ?? undefined,
        video.value?.height ?? undefined,
      ),
      video.value?.video_codec || '',
    )
  }

  const dims = probeVideoConfig(video.value?.width ?? undefined, video.value?.height ?? undefined)

  const [
    h264Anchor, h264, hevc, av1File, av1Ms, vp9File, vp9Ms,
    aac, opus, mp3, flac, vorbis, ac3, eac3, mpegts,
  ] = await Promise.all([
    // v3.5.4 可信度锚点：720p H.264 是任何正常环境的底线解码能力。
    // 个别环境（GPU 查询异常/远程桌面）decodingInfo 全 false（连 H.264 都不报），
    // 此时才能信任 canPlayType 兜底；正常环境以 decodingInfo 为准 ——
    // canPlayType 只查「格式注册」，不查分辨率/硬解性能，正常手机上用 probably
    // 兜底会把「4K VP9 硬解不动」误报为可解（安卓 Direct Play 4K → 起播失败）。
    probeDecoding('file', {
      width: 1280,
      height: 720,
      bitrate: 3_500_000,
      framerate: 30,
      contentType: 'video/mp4; codecs="avc1.42E01E"',
    }),
    // 视频编码：file（渐进直连）+ media-source（MSE/fMP4）双路径
    // HEVC 串用 L153（Level 5.1，覆盖 4K）—— L93 是 Level 3.1（最高仅 720p），
    // 与 2160 档探测尺寸矛盾；另见下方 canProbably 兜底
    probeDecoding('file', { ...dims, contentType: 'video/mp4; codecs="avc1.42E01E"' }),
    probeDecoding('file', { ...dims, contentType: 'video/mp4; codecs="hvc1.1.6.L153.B0"' }),
    // ★ AV1 / VP9 的 file 探测必须用 **MP4**，不能用 WebM：
    //   remux 产物是 MP4，而部分设备恰恰「能解 MP4 里的 AV1/VP9、解不了 WebM」
    //   （实测 vivo Pad / Android 16：can_av1_mp4=probably，
    //    MediaCapabilities VP9 4K file/mp4 supported=true，file/webm=false）。
    //   用 webm 探测会把这类设备的 AV1/VP9 能力报成「不可解」，
    //   于是库内 96% 的 VP9/AV1 源全部退化成服务端转码。
    probeDecoding('file', { ...dims, contentType: 'video/mp4; codecs="av01.0.05M.08"' }),
    probeDecoding('media-source', { ...dims, contentType: 'video/mp4; codecs="av01.0.13M.08"' }),
    probeDecoding('file', { ...dims, contentType: 'video/mp4; codecs="vp09.00.51.08"' }),
    probeDecoding('media-source', { ...dims, contentType: 'video/mp4; codecs="vp09.00.51.08"' }),
    // 音频编码
    probeDecoding('file', { contentType: 'audio/mp4; codecs="mp4a.40.2"', bitrate: 128_000 }),
    probeDecoding('file', { contentType: 'audio/webm; codecs="opus"', bitrate: 128_000 }),
    probeDecoding('file', { contentType: 'audio/mpeg', bitrate: 192_000 }),
    probeDecoding('file', { contentType: 'audio/flac', bitrate: 900_000 }),
    probeDecoding('file', { contentType: 'audio/webm; codecs="vorbis"', bitrate: 128_000 }),
    probeDecoding('file', { contentType: 'audio/mp4; codecs="ac-3"', bitrate: 384_000 }),
    probeDecoding('file', { contentType: 'audio/mp4; codecs="ec-3"', bitrate: 384_000 }),
    // 交付形态：MPEG-TS（H.264 转码）。fMP4 的可交付性只认 MediaSource.isTypeSupported，
    // 见下方 supports_fmp4 —— decodingInfo 会误报「伪 MSE」环境。
    probeDecoding('file', { ...dims, contentType: 'video/mp2t; codecs="avc1.42E01E, mp4a.40.2"' }),
  ])

  // canPlayType 兜底：decodingInfo 对依赖平台硬解的 HEVC / 部分 VP9 组合可能漏报
  // （supported=false 但 file:// 实际可硬解播放）。canPlayType 返回 'probably' 是浏览器
  // 明确声明可解 —— 与 file:// 直播能力同源，以此补齐 decodingInfo 的漏报。
  const probeEl = document.createElement('video')
  const canProbably = (mime: string) =>
    (probeEl.canPlayType?.(mime) || '') === 'probably'
  // v3.5.4：视频编码的 canProbably 兜底仅在 decodingInfo 整体不可信时启用
  // （锚点 720p H.264 都不报 = 查询异常环境，如远程桌面）。正常环境以
  // decodingInfo 的分辨率/性能判定为准 —— canPlayType 的 'probably' 只声明
  // 「容器+编码注册可解」，不区分 4K VP9 硬解不动的手机，正常手机上兜底
  // 会误报 VP9/AV1 可解 → Direct Play 4K → 起播失败。音频无分辨率维度，不受此限。
  const decodingInfoTrusted = h264Anchor.supported
  const canProbablySafe = (mime: string) => !decodingInfoTrusted && canProbably(mime)

  // v3.5.2：MSE 一票否决。Direct Stream（fMP4 HLS）在非 Apple 浏览器走 hls.js+MSE，
  // 交付是否可行由 MediaSource.isTypeSupported 说了算 —— 它为 false 时，
  // 即使渐进管线（file:// / canPlayType webm）能解，MSE append 也会 fatal（起播转圈）。
  // Apple Safari 走 <video> 原生 HLS（不经 MSE），不受此约束。
  const mseOk = (mime: string) => {
    try {
      return typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported(mime)
    } catch {
      return false
    }
  }
  const mseGate = (mime: string) => (isAppleSafari ? true : mseOk(mime))

  const videoCodecs: string[] = []
  if (h264.supported || mseOk('video/mp4; codecs="avc1.42E01E"')) videoCodecs.push('h264')
  if (
    (hevc.supported || canProbablySafe('video/mp4; codecs="hvc1.1.6.L153.B0"')) &&
    mseGate('video/mp4; codecs="hvc1.1.6.L153.B0"')
  ) videoCodecs.push('hevc')
  if ((av1File.supported || av1Ms.supported) && mseGate('video/mp4; codecs="av01.0.08M.08"')) videoCodecs.push('av1')
  if (
    (vp9File.supported || vp9Ms.supported || canProbablySafe('video/mp4; codecs="vp09.00.51.08"')) &&
    // vp09.00.00.08 与服务端 remux 清单的 CODECS 串保持一致（hls.js 按清单串查 MSE）
    mseGate('video/mp4; codecs="vp09.00.00.08"')
  ) videoCodecs.push('vp9')

  const audioCodecs: string[] = []
  if (aac.supported || mseOk('audio/mp4; codecs="mp4a.40.2"')) audioCodecs.push('aac')
  if (opus.supported) audioCodecs.push('opus')
  if (mp3.supported) audioCodecs.push('mp3')
  if (flac.supported) audioCodecs.push('flac')
  if (vorbis.supported) audioCodecs.push('vorbis')

  // iPhone/iPad 原生管线（无 MSE、原生 HLS 交付）：保守剔除，由服务端转码。
  if (isIOS) {
    for (const drop of ['vp9', 'av1', 'opus', 'vorbis', 'flac']) {
      let i = videoCodecs.indexOf(drop)
      if (i >= 0) videoCodecs.splice(i, 1)
      i = audioCodecs.indexOf(drop)
      if (i >= 0) audioCodecs.splice(i, 1)
    }
  }

  // v3.5.3：渐进直连（Direct Play）双轨口径。
  // supported_video_codecs 只代表 MSE（fMP4 HLS）可解 —— Direct Stream / Transcode
  // 决策用；progressive_video_codecs 代表渐进管线（<video> src 直连原文件，
  // Direct Play 用），与 file:// 播放同源（decodingInfo('file') / canPlayType）。
  // 个别环境（GPU 查询异常/远程桌面）渐进可硬解 VP9/AV1 4K 但 MSE 不认 fMP4 ——
  // 此时 Direct Play 直连原文件才是正解，而非降档转码。
  const progressiveVideo: string[] = []
  if (h264.supported || canProbablySafe('video/mp4; codecs="avc1.42E01E"')) progressiveVideo.push('h264')
  if (hevc.supported || canProbablySafe('video/mp4; codecs="hvc1.1.6.L153.B0"')) progressiveVideo.push('hevc')
  if (av1File.supported || canProbablySafe('video/mp4; codecs="av01.0.05M.08"')) progressiveVideo.push('av1')
  if (vp9File.supported || canProbablySafe('video/mp4; codecs="vp09.00.51.08"')) progressiveVideo.push('vp9')
  const progressiveAudio: string[] = []
  if (aac.supported || canProbably('audio/mp4; codecs="mp4a.40.2"')) progressiveAudio.push('aac')
  if (opus.supported || canProbably('audio/webm; codecs="opus"')) progressiveAudio.push('opus')
  if (mp3.supported || canProbably('audio/mpeg')) progressiveAudio.push('mp3')
  if (flac.supported || canProbably('audio/flac')) progressiveAudio.push('flac')
  if (vorbis.supported || canProbably('audio/webm; codecs="vorbis"')) progressiveAudio.push('vorbis')
  if (isIOS) {
    for (const drop of ['vp9', 'av1', 'opus', 'vorbis', 'flac']) {
      let i = progressiveVideo.indexOf(drop)
      if (i >= 0) progressiveVideo.splice(i, 1)
      i = progressiveAudio.indexOf(drop)
      if (i >= 0) progressiveAudio.splice(i, 1)
    }
  }

  // 渐进容器：桌面 Chromium 内置 Matroska demuxer（file:// 能播 MKV 即同源能力），
  // 渐进可解任一 Matroska 内视频编码时把 mkv 报进容器列表（Direct Play 资格）。
  // Apple 原生管线不支持 Matroska，不报。
  const containers = isAppleSafari
    ? ['mp4', 'm4v', 'mov']
    : ['mp4', 'm4v', 'mov', 'webm']
  /**
   * Matroska 只能靠**实测口径**判断，不能问 canPlayType：
   * Chrome/Edge 内核明明带 Matroska demuxer（实测 8K MKV 直连 33ms 出首帧），
   * 但对 `video/x-matroska` 统一返回空串 → 旧写法永远报不上去，
   * 后端看到「容器不支持: mkv」就把每条 4K/8K 都判成转码。
   * 口径：非 Apple、非 Firefox（Firefox 只认 WebM，不认 MKV），且渐进可解任一视频编码。
   */
  const isFirefox = /Firefox|FxiOS/i.test(ua)
  /**
   * 手机不上报 mkv：桌面 Chromium 有内置 Matroska demuxer，手机内核未必有
   * （实测 vivo 自带浏览器：直连 MKV 只请求一次 /stream 就中断）。
   * 手机也不报「无上限」分辨率 —— 4K60 AV1 直连在手机上是必挂项。
   */
  const isMobile = /Android|iPhone|iPad|iPod|Mobile/i.test(ua)
  if (!isAppleSafari && !isFirefox && !isMobile && progressiveVideo.length > 0) {
    containers.push('mkv')
  }

  return {
    supported_video_codecs: videoCodecs,
    supported_audio_codecs: audioCodecs,
    // 渐进直连（Direct Play）口径：详见上方 v3.5.3 注释
    progressive_video_codecs: progressiveVideo,
    progressive_audio_codecs: progressiveAudio,
    // MSE 交付容器（remux 后），Direct Play 容器资格以渐进 demuxer 实测为准（见上）
    supported_containers: containers,
    // 本播放器：Apple 走 <video> 原生 HLS，其它浏览器走 hls.js（MSE）。
    // ⚠ 不能无条件报 true：vivo / 华为等定制内核会没有可用的 MediaSource，
    // 报了 true 服务端就只给 HLS，前端却根本挂不上（manifest 一次都不请求）。
    supports_hls: isAppleSafari || hlsCapable(),
    supports_mpegts: isAppleSafari ? true : mpegts.supported,
    // fMP4 交付必须**真的**能 append：`MediaSource.isTypeSupported` 是唯一真值。
    // ⚠ vivo/华为定制内核存在「伪 MSE」—— `MediaSource` 对象在、`decodingInfo`
    //   对 4K HEVC 报 supported=true，但 `isTypeSupported` 全 false（实测
    //   vivo Pad / Android 16 / VivoBrowser 31）。带上 decodingInfo 的结论会让
    //   服务端给 direct_stream，前端 hls.js 根本挂不上，compat 又只服务 transcode
    //   → 400 → 黑屏。这里只认 isTypeSupported。
    supports_fmp4: isAppleSafari ? true : mseOk('video/mp4; codecs="avc1.42E01E"'),
    supports_h264: videoCodecs.includes('h264'),
    supports_hevc: videoCodecs.includes('hevc'),
    supports_av1: videoCodecs.includes('av1'),
    supports_vp9: videoCodecs.includes('vp9'),
    supports_aac: audioCodecs.includes('aac'),
    supports_ac3: ac3.supported,
    supports_eac3: eac3.supported,
    // ★ 设备端优先：**必须用 progressiveVideo（<video> 文件口径）**判断，
    //   不能用 videoCodecs（MSE 口径）—— 伪 MSE 设备（vivo/华为定制内核）
    //   的 videoCodecs 恒为空，会把它误判成「什么都解不了」而永远降档。
    ...decodeCapFor(dims, video.value?.video_codec || '', progressiveVideo),
    user_agent: ua,
  }
}

/**
 * 这台机器到底能不能走 HLS 交付。
 * Apple 有原生 HLS；其余浏览器必须靠 MSE，而 MSE 能不能吃 H.264 是设备/内核决定的
 * —— 与 `Hls.isSupported()` 的判据保持一致，避免「上报说支持、播放器却挂不上」。
 */
function hlsCapable(): boolean {
  try {
    if (typeof MediaSource === 'undefined') return false
    return !!MediaSource.isTypeSupported('video/mp4; codecs="avc1.42E01E"')
  } catch {
    return false
  }
}

/** 无 MediaCapabilities API 的旧浏览器回退：canPlayType / isTypeSupported 粗判（旧口径）。 */
function legacyClientCapabilities(
  ua: string,
  isIOS: boolean,
  isAppleSafari: boolean,
  dims: VideoConfiguration,
  sourceCodec: string,
): Record<string, unknown> {
  const v = document.createElement('video')
  // 只认 'probably'（浏览器明确声明可解码），'maybe' 不算支持，避免误报解码能力
  const can = (mime: string) => (v.canPlayType?.(mime) || '') === 'probably'
  const canMaybe = (mime: string) => {
    const r = v.canPlayType?.(mime) || ''
    return r === 'probably' || r === 'maybe'
  }
  // HLS/MSE 才是 Direct Stream（fMP4 清单）真正走的路径。
  const mseOk = (mime: string) => {
    try {
      return typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported(mime)
    } catch {
      return false
    }
  }
  const videoCodecs: string[] = []
  const audioCodecs: string[] = []
  const addCodec = (codec: string, mime: string, list: string[], allowMaybe = false) => {
    if (list.includes(codec)) return
    if (can(mime) || mseOk(mime) || (allowMaybe && canMaybe(mime))) list.push(codec)
  }
  // Chromium 桌面：AV1/VP9 的 canPlayType 偶发 maybe，但 MSE 能解，允许 maybe。
  addCodec('h264', 'video/mp4; codecs="avc1.42E01E"', videoCodecs)
  addCodec('hevc', 'video/mp4; codecs="hvc1.1.6.L93.B0"', videoCodecs)
  addCodec('av1', 'video/mp4; codecs="av01.0.05M.08"', videoCodecs, !isIOS)
  addCodec('av1', 'video/mp4; codecs="av01.0.08M.08"', videoCodecs, !isIOS)
  addCodec('av1', 'video/webm; codecs="av01.0.05M.08"', videoCodecs, !isIOS)
  addCodec('vp9', 'video/webm; codecs="vp9"', videoCodecs, !isIOS)
  addCodec('vp9', 'video/mp4; codecs="vp09.00.51.08"', videoCodecs, !isIOS)
  addCodec('aac', 'audio/mp4; codecs="mp4a.40.2"', audioCodecs)
  addCodec('opus', 'audio/webm; codecs="opus"', audioCodecs)
  addCodec('vorbis', 'audio/webm; codecs="vorbis"', audioCodecs)
  addCodec('mp3', 'audio/mpeg', audioCodecs)
  addCodec('flac', 'audio/flac', audioCodecs)
  // 桌面 Chrome/Edge：canPlayType 对 AV1 经常漏报，但 dav1d 一定能解。
  const isChromiumDesktop =
    !isIOS && /Chrome|Chromium|Edg\//i.test(ua) && !/iPhone|iPad|iPod/i.test(ua)
  if (isChromiumDesktop) {
    for (const c of ['h264', 'av1', 'vp9']) {
      if (!videoCodecs.includes(c)) videoCodecs.push(c)
    }
    for (const c of ['aac', 'opus']) {
      if (!audioCodecs.includes(c)) audioCodecs.push(c)
    }
  }
  // iPhone/iPad 原生无法解码 VP9/AV1。
  if (isIOS) {
    for (const drop of ['vp9', 'av1', 'opus', 'vorbis', 'flac']) {
      const i = videoCodecs.indexOf(drop)
      if (i >= 0) videoCodecs.splice(i, 1)
    }
    for (const drop of ['opus', 'vorbis', 'flac']) {
      const i = audioCodecs.indexOf(drop)
      if (i >= 0) audioCodecs.splice(i, 1)
    }
  }
  // v3.5.2：MSE 一票否决（同主探测路径）。非 Apple 浏览器走 hls.js+MSE 交付，
  // canPlayType webm 'probably'/'maybe' 只代表渐进管线可解，
  // MSE 不认 fMP4 该编码时直出会在起播处 fatal。
  // 过滤前的列表即渐进直连（Direct Play）口径 —— 单独留存上报（v3.5.3 双轨）。
  const progressiveVideo = [...videoCodecs]
  const progressiveAudio = [...audioCodecs]
  if (!isAppleSafari) {
    const mseGate: Record<string, string> = {
      h264: 'video/mp4; codecs="avc1.42E01E"',
      hevc: 'video/mp4; codecs="hvc1.1.6.L93.B0"',
      av1: 'video/mp4; codecs="av01.0.08M.08"',
      vp9: 'video/mp4; codecs="vp09.00.00.08"',
    }
    for (const [codec, mime] of Object.entries(mseGate)) {
      const i = videoCodecs.indexOf(codec)
      if (i >= 0 && !mseOk(mime)) videoCodecs.splice(i, 1)
    }
  }
  // 渐进容器：Matroska demuxer 可用时允许 Direct Play 直连 MKV（同主路径口径）
  const containers = isAppleSafari
    ? ['mp4', 'm4v', 'mov']
    : ['mp4', 'm4v', 'mov', 'webm']
  const isMobileUa = /Android|iPhone|iPad|iPod|Mobile/i.test(ua)
  if (
    !isAppleSafari &&
    !isMobileUa &&
    progressiveVideo.length > 0 &&
    (canMaybe('video/x-matroska; codecs="vp9"') || canMaybe('video/x-matroska; codecs="avc1.42E01E"'))
  ) {
    containers.push('mkv')
  }
  return {
    supported_video_codecs: videoCodecs,
    supported_audio_codecs: audioCodecs,
    progressive_video_codecs: progressiveVideo,
    progressive_audio_codecs: progressiveAudio,
    supported_containers: containers,
    supports_hls: isAppleSafari || hlsCapable(),
    supports_mpegts: isAppleSafari ? true : can('video/mp2t'),
    supports_fmp4:
      can('video/mp4; codecs="avc1.42E01E, mp4a.40.2"')
      || mseOk('video/mp4; codecs="avc1.42E01E"')
      || videoCodecs.includes('h264'),
    supports_h264: videoCodecs.includes('h264'),
    supports_hevc: videoCodecs.includes('hevc'),
    supports_av1: videoCodecs.includes('av1'),
    supports_vp9: videoCodecs.includes('vp9'),
    supports_aac: audioCodecs.includes('aac'),
    supports_ac3: can('audio/mp4; codecs="ac-3"'),
    supports_eac3: can('audio/mp4; codecs="ec-3"'),
    // ★ 设备端优先：同上，用 progressiveVideo（文件口径）而非 MSE 口径
    ...decodeCapFor(dims, sourceCodec, progressiveVideo),
    user_agent: ua,
  }
}

/** 8K 只能靠客户端硬解；一旦落到转码就必然卡顿 —— 只提示一次，别在切画质时反复弹 */
let uhd8kWarned = false

async function createSession(quality: string, startSeconds: number): Promise<PlaybackSession> {
  const payload: CreatePlaybackSessionPayload = {
    music_video_id: id.value,
    requested_quality: quality,
    start_seconds: startSeconds,
    client: await clientCapabilities(),
  }
  const session = await playbackApi.create(payload)
  const w = Number(video.value?.width) || 0
  const h = Number(video.value?.height) || 0
  const is8k = Math.min(w, h) >= 4320
  if (is8k && session.play_mode === 'transcode' && !uhd8kWarned) {
    uhd8kWarned = true
    message.warning(
      '这是 8K 片源，当前设备没能直接解码，正在实时转码——会明显卡顿。建议换支持 AV1 硬解的设备，或切到 4K 档。',
      { duration: 6000 },
    )
  }
  return session
}

// 切换画质：播放中新建对应规格的 PlaybackSession，旧会话交由服务端引用计数关闭；待播放时仅更新预选画质
async function selectQuality(key: string) {
  if (!video.value) return
  if (key === activeQuality.value) {
    if (!playing.value) return
    if (key !== 'original' || session.value?.play_mode !== 'transcode') return
  }
  // 暂停状态切画质只换源，不恢复播放；播放中才续播。
  const resume = !mediaPaused.value
  userPickedQuality.value = true
  activeQuality.value = key
  if (!playing.value) return
  await startPlayAt(lastKnownTime.value || 0, { autoPlay: resume })
}

let heartbeatTimer: number | null = null

function startHeartbeat() {
  stopHeartbeat()
  heartbeatTimer = window.setInterval(() => {
    const s = session.value
    if (s && playing.value) playbackApi.heartbeat(s.session_id).catch(() => {})
  }, 30_000)
}

function stopHeartbeat() {
  if (heartbeatTimer !== null) {
    window.clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
}

// ===== 本会话「已可播范围」轮询（seek 决策依据）=====
// 转码进行中 HLS 清单无 ENDLIST → 浏览器 MSE 的 duration 恒为 Infinity，
// 播放器无法自己判断「seek 目标转出来了没有」（见 VideoPlayer.canSeekInPlace）。
// 这里按 2s 低频轮询后端 /window，把 available_until（**片源绝对秒**）喂给播放器：
// 目标超出它就说明分片还没转出来 → 播放器会 emit seeked → 按目标重建会话。
const WINDOW_POLL_MS = 2_000
const availableUntil = ref(0)
let windowTimer: number | null = null

async function refreshWindow() {
  const s = session.value
  if (!s || s.play_mode === 'direct_play') {
    stopWindowPoll()
    return
  }
  try {
    const w = await playbackApi.sessionWindow(s.session_id)
    if (session.value?.session_id !== s.session_id) return
    if (typeof w.available_until === 'number') availableUntil.value = w.available_until
    // 转码已结束：上界不再变化（等于片源总时长），无需继续轮询
    if (w.finished) stopWindowPoll()
  } catch {
    /* 静默忽略：下一轮补上，不影响播放 */
  }
}

function startWindowPoll() {
  stopWindowPoll()
  availableUntil.value = 0
  void refreshWindow()
  windowTimer = window.setInterval(() => {
    if (playing.value) void refreshWindow()
  }, WINDOW_POLL_MS)
}

function stopWindowPoll() {
  if (windowTimer !== null) {
    window.clearInterval(windowTimer)
    windowTimer = null
  }
}

const WATCH_PING_MS = 15_000
let watchPingTimer: number | null = null

function watchPayload(extra: { playing?: boolean; ended?: boolean } = {}) {
  const v = video.value
  if (!v) return null
  const duration = session.value?.source_duration || v.duration || 0
  return {
    music_video_id: v.id,
    position: lastKnownTime.value || 0,
    duration: duration || undefined,
    playing: extra.playing ?? (playing.value && !mediaPaused.value),
    ended: extra.ended ?? false,
  }
}

function pingWatch(extra: { playing?: boolean; ended?: boolean } = {}) {
  const payload = watchPayload(extra)
  if (!payload) return
  dashboardApi.watchPing(payload).catch(() => {})
}

function startWatchPings() {
  stopWatchPings()
  pingWatch({ playing: true })
  watchPingTimer = window.setInterval(() => {
    if (playing.value && !mediaPaused.value) pingWatch({ playing: true })
  }, WATCH_PING_MS)
}

function stopWatchPings() {
  if (watchPingTimer !== null) {
    window.clearInterval(watchPingTimer)
    watchPingTimer = null
  }
}

function onPlayerEnded() {
  mediaPaused.value = true
  pingWatch({ playing: false, ended: true })
  stopWatchPings()
}

watch(playing, (p) => {
  if (p) {
    const s = session.value
    if (s) playbackApi.heartbeat(s.session_id).catch(() => {})
    startHeartbeat()
    if (!mediaPaused.value) startWatchPings()
  } else {
    stopHeartbeat()
    stopWatchPings()
  }
})

watch(mediaPaused, (paused) => {
  if (!playing.value) return
  if (paused) {
    pingWatch({ playing: false })
    stopWatchPings()
  } else {
    startWatchPings()
  }
})

watch(transcodePromptShow, (show, was) => {
  if (was && !show && !confirmingTranscode) {
    activeQuality.value = 'original'
    if (!session.value) {
      playing.value = false
      hasEverPlayed.value = false
      mediaPaused.value = true
    }
  }
})

// 页面级异常也回传：手机上「播放失败」有时是 JS 抛错（例如内核不认某个语法），
// 这种既不会走播放器 error 事件，也不会出现在服务端日志里。
function onWindowError(e: ErrorEvent) {
  reportDiag('js-error', {
    msg: String(e.message || '').slice(0, 160),
    src: String(e.filename || '').split('/').pop()?.slice(0, 60),
    line: e.lineno,
  })
}
function onUnhandledRej(e: PromiseRejectionEvent) {
  const r = e.reason as { message?: string } | string | undefined
  const msg = typeof r === 'string' ? r : r?.message || ''
  reportDiag('promise-rejection', { msg: String(msg).slice(0, 160) })
}

onMounted(() => {
  window.addEventListener('error', onWindowError)
  window.addEventListener('unhandledrejection', onUnhandledRej)
  // 原生播放器退出时回调；挂在页面上而不是播放器组件上 ——
  // 交接后播放器会被卸载，挂它身上会丢回调
  window.__idolNativePlayerClosed = onNativePlayerClosed
  narrowMq = window.matchMedia('(max-width: 768px)')
  isNarrow.value = narrowMq.matches
  narrowMq.addEventListener('change', syncNarrow)
  load()
  refreshHwAccel()
})

onBeforeUnmount(() => {
  narrowMq?.removeEventListener('change', syncNarrow)
  window.removeEventListener('error', onWindowError)
  window.removeEventListener('unhandledrejection', onUnhandledRej)
  if (window.__idolNativePlayerClosed === onNativePlayerClosed) {
    delete window.__idolNativePlayerClosed
  }
  if (seekDebounceTimer !== null) {
    window.clearTimeout(seekDebounceTimer)
    seekDebounceTimer = null
  }
  seekSeq += 1
  seekUiGen += 1
  pingWatch({ playing: false })
  stopWatchPings()
  stopHeartbeat()
  stopWindowPoll()
  playing.value = false
  mediaPaused.value = true
  shouldAutoPlay.value = false
  const s = session.value
  if (s) void stopSession(s.session_id)
})

// 路由参数变化（如点击「相关作品」跳转另一个视频）会复用本组件实例：
// 必须先停掉旧播放会话与心跳、重置状态，再按新 id 重新加载
watch(
  () => route.params.uid,
  (newId, oldId) => {
    if (!newId || newId === oldId) return
    if (video.value && (video.value.uid === String(newId) || String(video.value.id) === String(newId))) {
      return
    }
    // 切换后从头播放：不再记录上一段的播放秒数
    const s = session.value
    if (s) {
      pingWatch({ playing: false })
      stopWatchPings()
      stopHeartbeat()
      stopWindowPoll()
      void stopSession(s.session_id)
    }
    playing.value = false
    mediaPaused.value = true
    shouldAutoPlay.value = false
    availableUntil.value = 0
    if (seekDebounceTimer !== null) {
      window.clearTimeout(seekDebounceTimer)
      seekDebounceTimer = null
    }
    seekSeq += 1
    seekUiGen += 1
    session.value = null
    userPickedQuality.value = false
    activeQuality.value = 'original'
    originalDecodeFailed = false
    transcodePromptShow.value = false
    lastKnownTime.value = 0
    // 注意：不清空 stageMatrix —— 切机位时保留上一帧矩阵，
    // 命中缓存会同步替换，避免右舱先闪成「暂无数据」再回来
    brokenThumbs.value = new Set()
    brokenRelatedThumbs.value = new Set()
    related.value = []
    load()
    refreshHwAccel()
  }
)

async function openSystem() {
  try {
    await musicVideosApi.openInSystemPlayer(id.value)
  } catch (e) {
    message.error((e as Error).message)
  }
}

// ===== 编辑表单 =====
const editShow = ref(false)
const saving = ref(false)

interface EditFormModel {
  name: string
  original_title: string
  video_types: string[]
  aliases: string[] | null
  tracks: TrackRow[]
  artist_ids: number[]
  group_ids: number[]
  event_name: string
  camera_angle: string | null
  session_label: string
  performance_date: string | null
  release_date: string | null
  published_date: string | null
  source_platform: string
  source_id: string
  source_url: string
  original_uploader: string
  description: string
  chinese_description: string
  is_short: boolean
  is_solo: boolean
}

const editForm = ref<EditFormModel>({
  name: '',
  original_title: '',
  video_types: ['Other'],
  aliases: [],
  tracks: [{ key: 'e-init', song_id: null, album_ids: [] }],
  artist_ids: [],
  group_ids: [],
  event_name: '',
  camera_angle: null,
  session_label: '',
  performance_date: null,
  release_date: null,
  published_date: null,
  source_platform: '',
  source_id: '',
  source_url: '',
  original_uploader: '',
  description: '',
  chinese_description: '',
  is_short: false,
  is_solo: false,
})

const editSongPresets = ref<SaOption[]>([])
const editAlbumPresets = ref<SaOption[]>([])
const editArtistPresets = ref<SaOption[]>([])
const editGroupPresets = ref<SaOption[]>([])

// ===== 更改存储路径（仅已入库视频） =====
const pathSuggestion = ref<VideoPathSuggestion | null>(null)
const pathSuggestionLoading = ref(false)
const relocateTarget = ref('')
const relocating = ref(false)
const destDirOpen = ref(false)
// 移动文件属高危操作：默认折叠，与表单保存/取消在视觉上隔开
const advancedOpen = ref(false)

// 仅正式库内、带相对路径的视频支持移动；incoming / 绝对路径（未搬文件入库）不支持
const canRelocate = computed(
  () => !!(video.value && video.value.ingestion_status === 'library' && video.value.file_path),
)

async function refreshPathSuggestion() {
  if (!canRelocate.value || !video.value) return
  pathSuggestionLoading.value = true
  try {
    pathSuggestion.value = await musicVideosApi.pathSuggestion(video.value.id)
  } catch {
    pathSuggestion.value = null
  } finally {
    pathSuggestionLoading.value = false
  }
}

function fillSuggestedPath() {
  if (pathSuggestion.value?.suggested_path) {
    relocateTarget.value = pathSuggestion.value.suggested_path
  }
}

function applyDestDir(dir: string) {
  const fileName = video.value?.file_name || ''
  const base = dir.replace(/^\/+|\/+$/g, '')
  relocateTarget.value = base ? `${base}/${fileName}` : fileName
  destDirOpen.value = false
}

async function doRelocate() {
  if (!video.value || relocating.value) return
  const target = relocateTarget.value.trim()
  if (!target && !pathSuggestion.value?.suggested_path) {
    message.warning('该视频暂无规则推荐路径，请填写目标路径')
    return
  }
  const fromPath = pathSuggestion.value?.current_path || video.value.file_path || ''
  const toPath = target || pathSuggestion.value?.suggested_path || ''
  if (!window.confirm(`确定移动文件？\n\n从：${fromPath}\n到：${toPath}`)) return
  relocating.value = true
  try {
    const r = await musicVideosApi.relocate(video.value.id, target || null)
    message.success(
      `已移动到 ${r.file_path}${r.moved_sidecars.length ? `（伴随文件 ${r.moved_sidecars.length} 个一并迁移）` : ''}`,
    )
    editShow.value = false
    await load({ keepPlayback: true })
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    relocating.value = false
  }
}

function songLabel(s: SongBrief): string {
  return s.chinese_name ? `${s.name} / ${s.chinese_name}` : s.name
}
function albumLabel(a: AlbumBrief): string {
  return a.chinese_name ? `${a.name} / ${a.chinese_name}` : a.name
}
function artistLabel(a: ArtistBrief): string {
  const parts = [a.name]
  if (a.stage_name && a.stage_name !== a.name) parts.push(`(${a.stage_name})`)
  return parts.join(' / ')
}
function groupLabel(g: GroupBrief): string {
  return g.chinese_name ? `${g.name} / ${g.chinese_name}` : g.name
}

async function searchAlbums(q: string): Promise<SaOption[]> {
  const list = await albumsApi.brief(q || undefined)
  return list.map((a) => ({ label: albumLabel(a), value: a.id }))
}
async function searchArtists(q: string): Promise<SaOption[]> {
  const list = await artistsApi.brief(q || undefined)
  return list.map((a) => ({ label: artistLabel(a), value: a.id }))
}
async function searchGroups(q: string): Promise<SaOption[]> {
  const list = await groupsApi.brief(q || undefined)
  return list.map((g) => ({ label: groupLabel(g), value: g.id }))
}


function openEdit() {
  if (!video.value) return
  const v = video.value
  const artistIds = v.artist_ids && v.artist_ids.length ? [...v.artist_ids] : (v.artists || []).map((a) => a.id)
  const groupIds = v.group_ids && v.group_ids.length ? [...v.group_ids] : (v.groups || []).map((g) => g.id)
  editForm.value = {
    name: v.name,
    original_title: v.original_title ?? '',
    video_types:
      v.video_types && v.video_types.length ? [...v.video_types] : v.video_type ? [v.video_type] : ['Other'],
    aliases: v.aliases ?? [],
    tracks: (() => {
      const fromTracks = (v.tracks || []).map((t, i) => ({
        key: `e-${t.song_id}-${i}`,
        song_id: t.song_id,
        album_ids: [...(t.album_ids || [])],
      }))
      if (fromTracks.length) return fromTracks
      const fromSongs = (v.songs || []).map((s, i) => ({
        key: `e-${s.id}-${i}`,
        song_id: s.id,
        album_ids: [...(s.album_ids || [])],
      }))
      return fromSongs.length
        ? fromSongs
        : [{ key: 'e-empty', song_id: v.song_id ?? null, album_ids: [] }]
    })(),
    artist_ids: artistIds,
    group_ids: groupIds,
    event_name: v.event_name ?? '',
    camera_angle: v.camera_angle ?? null,
    session_label: v.session_label ?? '',
    performance_date: v.performance_date ?? null,
    release_date: v.release_date ?? null,
    published_date: v.published_date ?? null,
    source_platform: v.source_platform ?? '',
    source_id: v.source_id ?? '',
    source_url: v.source_url ?? '',
    original_uploader: v.original_uploader ?? '',
    description: v.description ?? '',
    chinese_description: v.chinese_description ?? '',
    is_short: v.is_short ?? false,
    is_solo: v.is_solo ?? false,
  }
  editSongPresets.value = (v.songs || []).map((s) => ({ label: songLabel(s), value: s.id }))
  editAlbumPresets.value = (v.albums || []).map((a) => ({ label: albumLabel(a), value: a.id }))
  editArtistPresets.value = (v.artists || []).map((a) => ({ label: artistLabel(a), value: a.id }))
  editGroupPresets.value = (v.groups || []).map((g) => ({ label: groupLabel(g), value: g.id }))
  // 更改存储路径：每次打开都重置并按最新元数据取建议
  advancedOpen.value = false
  relocateTarget.value = ''
  pathSuggestion.value = null
  void refreshPathSuggestion()
  editShow.value = true
}

function strOrNull(s: unknown): string | null {
  const v = typeof s === 'string' ? s.trim() : ''
  return v || null
}

async function saveEdit() {
  if (!editForm.value.name || !editForm.value.name.trim()) {
    message.warning('请填写视频名称')
    return
  }
  saving.value = true
  try {
    const f = editForm.value
    const types = f.video_types && f.video_types.length ? f.video_types : ['Other']
    const payload: Partial<MusicVideo> = {
      name: f.name.trim(),
      original_title: strOrNull(f.original_title),
      video_type: types[0],
      video_types: types as VideoType[],
      aliases: Array.isArray(f.aliases) && f.aliases.length ? f.aliases : null,
      tracks: f.tracks
        .filter((t) => t.song_id != null)
        .map((t) => ({ song_id: t.song_id as number, album_ids: t.album_ids })),
      song_ids: f.tracks.map((t) => t.song_id).filter((id): id is number => id != null),
      artist_ids: f.artist_ids.length ? f.artist_ids : null,
      group_ids: f.group_ids.length ? f.group_ids : null,
      song_id: f.tracks.find((t) => t.song_id != null)?.song_id || null,
      event_name: strOrNull(f.event_name),
      camera_angle: f.camera_angle || null,
      // 空串 = 交给后端按日期/活动名重算；非空 = 人工 override
      session_label: f.session_label.trim(),
      performance_date: f.performance_date || null,
      release_date: f.release_date || null,
      published_date: f.published_date || null,
      source_platform: strOrNull(f.source_platform),
      source_id: strOrNull(f.source_id),
      source_url: strOrNull(f.source_url),
      original_uploader: strOrNull(f.original_uploader),
      description: strOrNull(f.description),
      chinese_description: strOrNull(f.chinese_description),
      is_solo: !!f.is_solo,
    }
    await musicVideosApi.update(id.value, payload)
    message.success('已保存')
    editShow.value = false
    await load({ keepPlayback: true })
    // 元数据（类型/关联/标记）变化会影响推荐路径，保存后静默刷新
    void refreshPathSuggestion()
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    saving.value = false
  }
}

// ===== 资料字段 =====
// 11 项档案网格已收纳进右侧「视频档案」舱（VideoInfoDock.vue），此处仅保留底部关联标签


// ===== 侧栏艺人胶囊 =====
const avatarPalettes = [
  ['#2a2140', '#15151b'],
  ['#22242a', '#15151b'],
  ['#2a2220', '#15151b'],
  ['#1e2a2e', '#15151b'],
  ['#241e2e', '#15151b'],
]
function avatarStyle(i: number) {
  const [c1, c2] = avatarPalettes[i % avatarPalettes.length]
  return { background: `linear-gradient(135deg, ${c1}, ${c2})` }
}
function initialOf(name?: string | null) {
  return (name || '?').trim().charAt(0).toUpperCase()
}

const relatedArtists = computed(() => {
  const v = video.value
  if (!v) return []
  const list: { key: string; id: number | null; uid: string; kind: 'artist' | 'group'; name: string; sub: string; index: number; avatar: string | null }[] = []
  for (const a of v.artists || []) {
    const name = a.chinese_name || a.name
    list.push({ key: `a-${a.id}`, id: a.id, uid: a.uid, kind: 'artist', name, sub: a.stage_name || a.name, index: list.length, avatar: a.avatar_path || null })
  }
  for (const g of v.groups || []) {
    const name = g.chinese_name || g.name
    list.push({ key: `g-${g.id}`, id: g.id, uid: g.uid, kind: 'group', name, sub: g.group_type || g.name, index: list.length, avatar: g.avatar_path || null })
  }
  // 视频参与者：仅展示（带头像、不跳转——参与者不进艺人页/统计）
  for (const p of v.participants || []) {
    list.push({
      key: `p-${p.id ?? p.name}`,
      id: p.id ?? null,
      uid: '',
      kind: 'artist',
      name: p.name,
      sub: '参与者',
      index: list.length,
      avatar: p.id ? `/api/artists/${p.id}/avatar?w=96` : null,
    })
  }
  return list
})

function relatedAvatarUrl(item: { id: number | null; kind: 'artist' | 'group' }) {
  if (item.id === null) return ''
  return item.kind === 'group'
    ? `/api/groups/${item.id}/avatar?w=96`
    : `/api/artists/${item.id}/avatar?w=96`
}

function goRelatedEntity(item: { uid?: string; kind: 'artist' | 'group' }) {
  if (!item.uid) return
  if (item.kind === 'group') router.push(groupPath(item.uid))
  else router.push(artistPath(item.uid))
}

function goRelated(item: MusicVideoBrief) {
  router.push(videoPath(item.uid))
}


</script>

<template>
  <div class="mv-page" :class="`mobile-seg-${mobileSegment}`">
    <SaHeader>
    </SaHeader>
    <div class="mv-container">
    <n-spin :show="loading">
      <div v-if="notFound && !loading" class="hero-status">
        <div class="hero-status-card">
          <span class="hero-status-title">找不到这个视频</span>
          <span class="hero-status-sub">记录不存在或已删除</span>
        </div>
      </div>
      <div v-else-if="video" class="main-grid" @touchstart.passive="onMobileTouchStart" @touchend.passive="onMobileTouchEnd">
        <!-- 背景动态环境光 -->
        <div class="ambient ambient--a" aria-hidden="true" />
        <div class="ambient ambient--b" aria-hidden="true" />
        <div class="main-col">
          <!-- Hero Cover：固定 16:9 横向画框（不随片源比例变形） -->
          <div
            class="hero-cover"
            @mouseenter="onHeroEnter"
            @mouseleave="onHeroLeave"
            @click="onHeroTap"
            @dblclick="onHeroDblClick"
          >
            <template v-if="hasSourceFile">
              <!-- 预播态：纯封面（不挂播放器）；唤出（armed）或播放后由播放器盖在封面之上 -->
              <img
                v-if="displayedPoster"
                v-show="!playerVisible"
                :src="displayedPoster"
                :alt="video.name"
                class="hero-img"
                @error="onThumbError"
              />
              <div v-if="!displayedPoster && !playerVisible" class="hero-placeholder">
                <span class="hero-placeholder-play"><PlayArrowOutlined :size="26" /></span>
              </div>
              <div v-if="playerVisible" class="hero-player">
                <!-- 外部承载：B 站官方 iframe。后端只在「本地无片源 + BV 可播」时下发 embed_url -->
                <div v-if="externalPlay" class="bili-embed">
                  <iframe
                    :src="externalPlay.embedUrl"
                    :title="video.name"
                    class="bili-embed-frame"
                    scrolling="no"
                    frameborder="0"
                    allow="autoplay; fullscreen; picture-in-picture"
                    allowfullscreen
                    referrerpolicy="no-referrer-when-downgrade"
                  ></iframe>
                  <a
                    v-if="externalPlay.watchUrl"
                    class="bili-embed-link"
                    :href="externalPlay.watchUrl"
                    target="_blank"
                    rel="noopener noreferrer"
                  >在 B 站打开</a>
                </div>
                <template v-else>
                <VideoPlayer
                  ref="playerRef"
                  :sources="sources"
                  :poster="displayedPoster"
                  :active-label="playing ? activeLabel : ''"
                  :qualities="playing ? qualities : preQualities"
                  :active-quality="activeQuality"
                  :play-mode-label="playing && session ? playModeLabel(session.play_mode, session.quality) : ''"
                  :play-mode-reason="playing && session ? session.decision_reason ?? '' : ''"
                  :play-mode="playing && session ? session.play_mode : ''"
                  :restore-time="playing && session ? session.play_mode === 'direct_play' : false"
                  :buffering="playerBuffering"
                  :pending="!playing"
                  :duration="session?.source_duration || video.duration || 0"
                  :start-offset="session?.start_offset || 0"
                  :native-seek="session?.play_mode === 'direct_play'"
                  :auto-play="shouldAutoPlay"
:seek-into="seekInto"
:available-until="availableUntil"
:subtitles="subtitleTracks"
:native-video-id="id"
:native-title="video.name"
@state="onPlayerState"
                  @timeupdate="onTimeUpdate"
                  @seeked="onSeeked"
                  @quality-select="selectQuality"
                  @play-request="startPlay"
                  @native-handoff="onNativeHandoff"
                  @play="onPlayerPlay"
                  @pause="onPlayerPause"
                  @ended="onPlayerEnded"
                />
                <div v-if="playing && !sources.length" class="hero-empty">
                  <n-spin v-if="sessionLoading" size="small" />
                  <template v-else>
                    <PlayArrowOutlined />
                    <p>暂无可用播放源</p>
                    <button v-if="!isMobile" class="hero-system-btn" @click="openSystem">用系统播放器打开</button>
                  </template>
                </div>
                <!-- Emby 式任意 seek：拖动进度条后正按目标时间新建转码会话（-ss） -->
                <div v-if="playing && isSeeking" class="hero-seeking">
                  <n-spin size="small" />
                  <p>正在跳转…</p>
                </div>
                </template>
              </div>
            </template>
            <template v-else>
              <img
                v-if="displayedPoster"
                :src="displayedPoster"
                :alt="video.name"
                class="hero-img"
                @error="onThumbError"
              />
              <div v-else class="hero-placeholder">
                <span class="hero-placeholder-play"><PlayArrowOutlined :size="26" /></span>
              </div>
              <div class="hero-status">
                <div class="hero-status-card">
                  <span class="hero-status-title">暂无可用播放源</span>
                  <span class="hero-status-sub">请检查源文件是否丢失</span>
                </div>
                <div v-if="!isMobile" class="hero-status-actions">
                  <button class="hero-system-btn" @click="openSystem">用系统播放器打开</button>
                </div>
              </div>
            </template>

            <!-- 章节分段条 + 当前章节提示（叠播放器底部控制栏上方；保留播放器原生进度条） -->
            <div
              v-if="chapters.length && (session?.source_duration || video.duration)"
              class="hero-chapters"
            >
              <div class="hero-chapter-hint mono">
                <span v-if="activeChapterTitle" class="hero-chapter-name">{{ activeChapterTitle }}</span>
                <span>{{ formatClock(lastKnownTime) }} / {{ formatClock(session?.source_duration || video.duration || 0) }}</span>
              </div>
              <ChapterSegmentBar
                :chapters="chapters"
                :current-time="lastKnownTime"
                :duration="session?.source_duration || video.duration || 0"
                @seek="jumpToChapter"
              />
            </div>
          </div>

          <!-- 播放器下方只保留播放模式；机位 + 画幅徽标已删除。 -->
          <div v-if="playing && session" class="hero-meta-bar">
            <span
              class="mode-chip"
              :class="{ 'is-transcode': session.play_mode === 'transcode' && session.quality !== 'original' }"
              :title="session.decision_reason || playModeLabel(session.play_mode, session.quality)"
            >
              <span class="mode-dot" />
              {{ playModeLabel(session.play_mode, session.quality) }}
            </span>
          </div>

          <!-- 转码错误提示（画质选择已移入播放器控制栏齿轮菜单） -->
          <div v-if="playing && playerError" class="hero-error">{{ playerError }}</div>

          <!-- 移动端标题信息卡（桌面隐藏） -->
          <div class="v-m-header">
            <h2 class="v-m-title">{{ video.name }}</h2>
            <div class="v-m-actions">
              <button
                v-if="mobileActor"
                type="button"
                class="v-m-actor"
                @click="mobileActor.uid && goRelatedEntity(mobileActor)"
              >
                <img
                  v-if="mobileActor.avatar && mobileActor.id"
                  class="v-m-actor-avatar"
                  :src="relatedAvatarUrl(mobileActor)"
                  alt=""
                />
                <span v-else class="v-m-actor-avatar v-m-actor-avatar--empty" />
                <span class="v-m-actor-text">
                  <b>{{ mobileActor.name }}</b>
                  <i>{{ mobileActor.sub }}</i>
                </span>
              </button>

              <div class="v-m-tools">
                <!-- 分段切换：单个按钮，标签显示「另一个页面」，点一下互换 -->
                <button
                  type="button"
                  class="v-m-switch"
                  :aria-label="mobileSegment === 'cams' ? '切换到视频档案' : '切换到场次机位'"
                  @click="mobileSegment = mobileSegment === 'cams' ? 'archive' : 'cams'"
                >{{ mobileSegment === 'cams' ? '档案' : '场次' }}</button>

                <!-- 与 PC 同一套图标按钮 -->
                <div class="v-m-quick">
                  <button
                    class="btn-ghost btn-ghost--icon"
                    :class="{ 'btn-ghost--on': isCollected }"
                    title="收藏"
                    @click="collectOpen = !collectOpen"
                  >
                    <BookmarkFilled v-if="isCollected" />
                    <BookmarkOutlined v-else />
                  </button>
                  <VideoCollectPicker
                    v-if="collectOpen"
                    class="collect-pop"
                    :video="video"
                    @change="applyVideoMembership"
                  />
                  <button
                    v-if="scoreOverall !== null"
                    class="score-ring-btn"
                    :class="scoreClass(scoreOverall)"
                    :disabled="!video"
                    :title="`AI 评分（实验）：综合 ${scoreOverall}/10`"
                    @click="openScore"
                  >
                    <svg viewBox="0 0 36 36" width="24" height="24">
                      <circle class="ring-track" cx="18" cy="18" r="15.5" />
                      <circle class="ring-arc" cx="18" cy="18" r="15.5" :stroke-dasharray="ringDash" />
                      <text class="ring-text" x="18" y="22">{{ scoreOverall }}</text>
                    </svg>
                  </button>
                  <button
                    v-else
                    class="btn-ghost btn-ghost--icon"
                    :disabled="!video"
                    title="AI 评分（实验）"
                    @click="openScore"
                  >
                    <StarOutlined />
                  </button>
                  <button class="btn-ghost btn-ghost--icon" :disabled="!video" title="编辑" @click="openEdit">
                    <EditOutlined />
                  </button>
                </div>
              </div>
            </div>
          </div>

          <!-- 视频档案：整体下沉到播放器下方；右侧只保留场次与机位 -->
          <section class="info-below">
            <VideoInfoDock
              :video="video"
              :session="session"
              :artists="relatedArtists"
              :title="video.name"
              :description="video.description || ''"
              :chinese-description="video.chinese_description || ''"
              :wave="dockWave"
              @go-entity="goRelatedEntity"
            >
              <template #actions>
                <div class="title-actions">
                    <button
                      class="btn-ghost btn-ghost--icon"
                      :class="{ 'btn-ghost--on': isCollected }"
                      title="收藏"
                      @click="collectOpen = !collectOpen"
                    >
                      <BookmarkFilled v-if="isCollected" />
                      <BookmarkOutlined v-else />
                    </button>
                    <VideoCollectPicker
                      v-if="collectOpen"
                      class="collect-pop"
                      :video="video"
                      @change="applyVideoMembership"
                    />
                    <button
                      v-if="scoreOverall !== null"
                      class="score-ring-btn"
                      :class="scoreClass(scoreOverall)"
                      :disabled="!video"
                      :title="`AI 评分（实验）：综合 ${scoreOverall}/10`"
                      @click="openScore"
                    >
                      <svg viewBox="0 0 36 36" width="24" height="24">
                        <circle class="ring-track" cx="18" cy="18" r="15.5" />
                        <circle
                          class="ring-arc"
                          cx="18"
                          cy="18"
                          r="15.5"
                          :stroke-dasharray="ringDash"
                          transform="rotate(-90 18 18)"
                        />
                        <text class="ring-text" x="18" y="22">{{ scoreOverall }}</text>
                      </svg>
                    </button>
                    <button
                      v-else
                      class="btn-ghost btn-ghost--icon"
                      :disabled="!video"
                      title="AI 评分（实验）"
                      @click="openScore"
                    >
                      <StarOutlined />
                    </button>
                    <button class="btn-ghost btn-ghost--icon" :disabled="!video" title="编辑" @click="openEdit">
                      <EditOutlined />
                    </button>
                </div>
              </template>
            </VideoInfoDock>



          </section>
        </div>

        <!-- 右侧：只保留打歌期场次与机位，档案已下沉到播放器下方 -->
        <aside class="side-col">
          <div class="dock-shell">
            <div>
              <div class="dock-tabs">
                <span class="dock-tab-label dock-tab-label--head">场次与机位</span>
              </div>

              <div class="dock-body">
                <div class="dock-panel">
                  <StageMatrixDock
                    v-if="stageMatrix && stageMatrix.available"
                    :matrix="stageMatrix"
                    :wave="dockWave"
                    @select="onStageSelect"
                    @prefetch="prefetchPoster"
                  />
                  <div v-else class="dock-empty">该视频暂无打歌期场次数据</div>
                </div>
              </div>
            </div>
          </div>
          <!-- 相关作品 / 相关艺人：跟随场次机位放到右栏 -->
          <div v-if="related.length || relatedPeople.length" class="rail-extra">
            <details v-if="related.length" class="side-fold" open>
              <summary>相关作品（{{ relatedShown.length }}）</summary>
              <div class="related-list">
                <button
                  v-for="item in relatedShown"
                  :key="item.id"
                  class="related-item"
                  @click="goRelated(item)"
                >
                  <div class="related-thumb">
                    <img
                      v-if="settings.showCovers && !brokenRelatedThumbs.has(item.id)"
                      :src="musicVideosApi.thumbnailUrl(item.id, undefined, IMG_W_THUMB)"
                      alt=""
                      @error="onRelatedThumbError(item.id)"
                    />
                    <span v-else class="thumb-placeholder"><MovieOutlined :size="16" /></span>
                  </div>
                  <div class="related-info">
                    <div class="related-title">{{ item.name }}</div>
                    <div class="related-meta">
                      {{ VIDEO_TYPE_LABEL[item.video_type] || item.video_type
                      }}{{ item.duration ? ` · ${formatDuration(item.duration)}` : '' }}
                    </div>
                  </div>
                </button>
              </div>
            </details>

            <details v-if="relatedPeople.length" class="side-fold" open>
              <summary>相关艺人（{{ peopleShown.length }}）</summary>
              <div class="artist-list">
                <button
                  v-for="(a, i) in peopleShown"
                  :key="`${a.kind}-${a.id}`"
                  class="artist-capsule"
                  :class="{ 'artist-capsule--link': !!a.uid }"
                  :disabled="!a.uid"
                  @click="a.uid && goRelatedEntity({ uid: a.uid, kind: a.kind })"
                >
                  <div class="artist-avatar" :style="avatarStyle(i)">
                    <img
                      v-if="a.avatar_path"
                      class="artist-avatar-img"
                      :src="relatedAvatarUrl(a)"
                      alt=""
                      loading="lazy"
                    />
                    <template v-else>{{ initialOf(a.chinese_name || a.name) }}</template>
                  </div>
                  <div class="artist-info">
                    <div class="artist-name">{{ a.chinese_name || a.name }}</div>
                  </div>
                </button>
              </div>
            </details>
          </div>
        </aside>
      </div>
    </n-spin>
    </div>

    <!-- 选帧封面弹窗 -->
    <CoverFramePicker
      v-model:show="coverPickerShow"
      :video-id="id"
      :cover-manual="!!video?.cover_manual"
      @applied="onCoverApplied"
      @reset-auto="onCoverReset"
    />

    <!-- 编辑弹窗 -->
    <n-modal v-model:show="editShow" preset="card" title="编辑视频" :style="{ width: 'min(720px, calc(100vw - 24px))' }" :bordered="false">
      <div class="edit-form">
        <n-form label-placement="top">
          <div class="form-grid">
            <n-form-item label="视频类型" class="col-2">
              <SaSelect
                v-model="editForm.video_types"
                :options="VIDEO_TYPE_OPTIONS"
                multiple
                placeholder="可选择多个视频类型"
              />
            </n-form-item>
            <n-form-item label="原始标题">
              <n-input v-model:value="editForm.original_title" placeholder="来源原始标题，锁定为原文" readonly />
            </n-form-item>
            <n-form-item label="标题" required class="col-2">
              <n-input v-model:value="editForm.name" placeholder="库内展示标题" />
            </n-form-item>
            <n-form-item label="别名" class="col-2">
              <SaSelect
                v-model="editForm.aliases"
                multiple
                tag
                placeholder="输入别名后回车添加"
              />
            </n-form-item>
            <n-form-item label="原始链接" class="col-2">
              <n-input v-model:value="editForm.source_url" placeholder="https://…" />
            </n-form-item>
            <n-form-item label="视频平台">
              <n-input v-model:value="editForm.source_platform" readonly placeholder="如 YouTube / Twitter" />
            </n-form-item>
            <n-form-item label="上传日期">
              <n-date-picker
                v-model:formatted-value="editForm.published_date"
                type="date"
                value-format="yyyy-MM-dd"
                clearable
                style="width: 100%"
              />
            </n-form-item>
            <n-form-item label="表演日期">
              <n-date-picker
                v-model:formatted-value="editForm.performance_date"
                type="date"
                value-format="yyyy-MM-dd"
                clearable
                style="width: 100%"
              />
            </n-form-item>
            <n-form-item label="发布日期">
              <n-date-picker
                v-model:formatted-value="editForm.release_date"
                type="date"
                value-format="yyyy-MM-dd"
                clearable
                style="width: 100%"
              />
            </n-form-item>
            <n-form-item label="博主（频道）">
              <n-input v-model:value="editForm.original_uploader" placeholder="上传者 / 频道" />
            </n-form-item>
            <n-form-item label="舞台 / 活动" class="col-2">
              <n-input v-model:value="editForm.event_name" placeholder="如 MBC Show! Music Core" />
            </n-form-item>
            <n-form-item label="机位">
              <SaSelect
                v-model="editForm.camera_angle"
                :options="CAMERA_ANGLE_OPTIONS"
                placeholder="留空 = 未标注"
              />
            </n-form-item>
            <n-form-item label="场次">
              <n-input
                v-model:value="editForm.session_label"
                placeholder="YYMMDD - 活动名（留空自动生成）"
              />
            </n-form-item>
            <n-form-item label="曲目" class="col-2">
              <VideoTrackList
                :tracks="editForm.tracks"
                :album-presets="editAlbumPresets"
                :search-albums="searchAlbums"
                :allow-create-album="false"
                @update:tracks="editForm.tracks = $event"
              />
            </n-form-item>
            <n-form-item label="艺术家" class="col-2">
              <SaSelect
                v-model="editForm.artist_ids"
                :fetch-options="searchArtists"
                :preset-options="editArtistPresets"
                multiple
                placeholder="输入艺术家名搜索并关联"
              />
            </n-form-item>
            <n-form-item label="组合" class="col-2">
              <SaSelect
                v-model="editForm.group_ids"
                :fetch-options="searchGroups"
                :preset-options="editGroupPresets"
                multiple
                placeholder="输入组合名搜索并关联"
              />
            </n-form-item>
            <n-form-item label="来源 ID">
              <n-input v-model:value="editForm.source_id" placeholder="视频 ID" />
            </n-form-item>
            <n-form-item label="是否短视频（由类型自动判断）" class="col-2">
              <span class="muted-tip">{{ editForm.is_short ? '是' : '否' }}（仅「短视频」视频类型判定，与时长无关）</span>
            </n-form-item>
            <n-form-item class="col-2">
              <n-checkbox v-model:checked="editForm.is_solo">Solo（独立艺人）</n-checkbox>
            </n-form-item>
            <n-form-item label="原简介（来源原文，只读）" class="col-2">
              <n-input v-model:value="editForm.description" type="textarea" :rows="3" readonly placeholder="来源 json 中的原始简介" />
            </n-form-item>
            <n-form-item label="中文简介" class="col-2">
              <n-input v-model:value="editForm.chinese_description" type="textarea" :rows="3" placeholder="AI 根据视频信息生成的中文简介，可手动修改（规则在「AI 设置」页可改）" />
            </n-form-item>
          </div>

          <!-- 封面：低频功能，从 meta 行按钮合并进编辑弹窗 -->
          <div class="relocate-block">
            <div class="relocate-head">
              <span class="relocate-title">封面</span>
              <span class="cover-state">
                {{ video?.cover_manual ? '当前：手动选帧' : '当前：自动截取' }}
              </span>
            </div>
            <n-button size="small" @click="coverPickerShow = true">选择封面</n-button>
          </div>

          <!-- B 站播放源：原详情页右栏那一块，收进编辑弹窗（业主 2026-11-13） -->
          <div v-if="video" class="relocate-block">
            <div class="relocate-head">
              <span class="relocate-title">B 站播放源</span>
              <span class="cover-state">
                {{ video.publish ? publishStatusLabel(video.publish.status) : '未绑定' }}
              </span>
            </div>
            <div class="bili-bind">
              <div v-if="video.publish?.bvid" class="bili-bind-current">
                <a
                  :href="video.publish.watch_url || '#'"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="bili-bind-bv"
                >{{ video.publish.bvid }}<span v-if="(video.publish.page || 1) > 1"> · P{{ video.publish.page }}</span></a>
                <span class="bili-bind-origin">{{ publishOriginLabel(video.publish.origin) }}</span>
                <button class="btn-ghost bili-bind-unlink" @click="unbindBili">解绑</button>
              </div>
              <div class="bili-bind-input">
                <n-input
                  v-model:value="biliInput"
                  size="small"
                  clearable
                  placeholder="粘贴 B 站视频地址或 BV 号"
                  @keyup.enter="saveBili"
                />
                <n-button
                  size="small"
                  type="primary"
                  :loading="biliSaving"
                  :disabled="!biliInput.trim()"
                  @click="saveBili"
                >绑定</n-button>
              </div>
              <p class="bili-bind-hint">
                绑定后，在线版（没有本地文件的镜像站点）会用 B 站 iframe 播放这个视频。
                只接受公开且可播放的稿件；被锁定的稿件请先解绑再换一个。
              </p>
            </div>
          </div>

          <!-- 更改存储路径（高危，默认折叠）：入库时选错目录（如落在 unknown）可在此移动文件；
               与保存/取消分开，避免误触真实搬运磁盘文件 -->
          <div v-if="canRelocate" class="relocate-block">
            <div class="relocate-head">
              <button type="button" class="relocate-toggle" @click="advancedOpen = !advancedOpen">
                <span class="relocate-danger-tag">高危</span>
                <span class="relocate-title">更改存储路径（移动文件）</span>
                <span class="relocate-arrow" :class="{ 'relocate-arrow--open': advancedOpen }">▾</span>
              </button>
              <n-button v-if="advancedOpen" quaternary size="tiny" :loading="pathSuggestionLoading" @click="refreshPathSuggestion">
                <template #icon><RefreshOutlined :size="14" /></template>
                刷新推荐
              </n-button>
            </div>
            <template v-if="advancedOpen">
            <div class="relocate-line">
              <span class="relocate-label">当前</span>
              <code>{{ pathSuggestion?.current_path || video?.file_path }}</code>
            </div>
            <div v-if="pathSuggestion?.suggested_path" class="relocate-line">
              <span class="relocate-badge">推荐</span>
              <code>{{ pathSuggestion.suggested_path }}</code>
              <n-button size="tiny" quaternary type="primary" @click="fillSuggestedPath">填入</n-button>
            </div>
            <div v-else class="relocate-norule">
              {{ pathSuggestion?.notice || '自动规则不适用（缺少组合 / 歌曲 / 类型标记等关联信息），可先保存元数据后点「刷新推荐」，或直接手动填写目标路径' }}
            </div>
            <div class="relocate-row">
              <n-input
                v-model:value="relocateTarget"
                size="small"
                placeholder="留空则按推荐路径移动；也可填写如 组合名/歌曲名/文件.mp4"
              />
              <n-button size="small" @click="destDirOpen = true">
                <template #icon><FolderOutlined :size="14" /></template>
                已有目录
              </n-button>
              <n-button size="small" type="warning" :loading="relocating" @click="doRelocate">
                移动文件
              </n-button>
            </div>
            <div class="relocate-hint">移动会真实搬运磁盘文件并做哈希校验（同名自动加后缀防覆盖），同目录的 info.json 与同名封面会一并迁移；路径以正式库目录为根、用 / 分隔。</div>
            </template>
          </div>
          <div v-else-if="video && video.ingestion_status !== 'library'" class="relocate-block relocate-block--muted">
            该视频尚未正式入库（当前状态：{{ video.ingestion_status }}），仅已入库视频支持更改存储路径。
          </div>
        </n-form>
      </div>
      <template #footer>
        <n-space justify="end">
          <n-button @click="editShow = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="saveEdit">保存</n-button>
        </n-space>
      </template>
    </n-modal>

    <n-modal
      v-model:show="transcodePromptShow"
      preset="card"
      title="无法以原画播放"
      :style="{ width: 'min(440px, calc(100vw - 24px))' }"
      :mask-closable="false"
    >
      <p class="transcode-prompt-lead">原画无法直接播放，需要转码后观看。</p>
      <p v-if="transcodePromptDetail" class="transcode-prompt-detail">{{ transcodePromptDetail }}</p>
      <n-form-item v-if="transcodePromptOptions.length" label="转码画质" :show-feedback="false">
        <SaSelect v-model="transcodePromptQuality" :options="transcodePromptOptions" />
      </n-form-item>
      <p v-else class="transcode-prompt-detail">没有可用的转码档位。</p>
      <template #footer>
        <n-space justify="end">
          <n-button @click="cancelTranscodePrompt">仍尝试原画</n-button>
          <n-button
            type="primary"
            :disabled="!transcodePromptOptions.length"
            @click="confirmTranscodePrompt"
          >
            转码播放
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- AI 视频评分（实验） -->
    <n-modal
      v-model:show="scoreOpen"
      preset="card"
      title="AI 视频评分"
      :style="{ width: 'min(520px, calc(100vw - 24px))' }"
    >
      <div v-if="video" class="score-head">
        <span class="score-video-name">{{ video.name }}</span>
        <span v-if="scoreResult" class="score-meta">
          {{ scoreResult.model }} · {{ scoreResult.frames }} 帧 · {{ (scoreResult.scored_at || '').slice(0, 10) }}
        </span>
      </div>

      <div v-if="scoreError" class="score-error">{{ scoreError }}</div>

      <div v-if="scoreResult" class="score-dims">
        <div v-for="(d, key) in scoreResult.dims" :key="key" class="score-dim">
          <span class="score-dim-name">{{ d.label }}</span>
          <span class="score-dim-score" :class="scoreClass(d.score)">{{ d.score }}</span>
          <span class="score-dim-reason">{{ d.reason }}</span>
        </div>
        <p v-if="scoreResult.comment" class="score-comment">{{ scoreResult.comment }}</p>
      </div>
      <div v-else-if="!scoring" class="score-empty">
        还没有评分记录。评分会抽取若干帧发送给视觉模型（帧数随抽帧策略，约 30-120 秒）。
      </div>

      <div v-if="scoring" class="score-loading">
        <n-spin size="small" />
        评分中…抽帧与模型推理，视帧数约需 30-120 秒
      </div>

      <template #footer>
        <n-space justify="end">
          <n-button :disabled="scoring" @click="scoreOpen = false">关闭</n-button>
          <n-button type="primary" :loading="scoring" @click="runScore">
            {{ scoreResult ? '重新评分' : '开始评分' }}
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 选择目标文件夹（通用软件式：左侧常用位置 + 右侧逐级浏览） -->
    <FolderPickerModal v-model:show="destDirOpen" @select="applyDestDir" />
  </div>
</template>

<style scoped>
/* 统一弹簧曲线（复刻设计稿 --spring） */
.mv-page {
  --spring: cubic-bezier(0.16, 1, 0.3, 1);
  min-height: 100vh;
  background: var(--sa-bg);
  color: var(--sa-text-primary);
  -webkit-font-smoothing: antialiased;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

/* 背景动态环境光（accent 弱光晕，纯装饰） */
.main-grid {
  position: relative;
}
.ambient {
  position: absolute;
  border-radius: 999px;
  filter: blur(120px);
  pointer-events: none;
  z-index: 0;
}
.ambient--a {
  top: -60px;
  left: 6%;
  width: 520px;
  height: 320px;
  background: color-mix(in oklab, var(--sa-accent) 13%, transparent);
}
.ambient--b {
  top: 260px;
  right: 2%;
  width: 420px;
  height: 280px;
  background: color-mix(in oklab, var(--sa-accent) 9%, transparent);
}
.main-col,
.side-col {
  position: relative;
  z-index: 1;
}

/* ===== 播放器下方信息条（仅播放模式）===== */
.hero-meta-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
  min-height: 26px;
}
/* 播放模式：绿点=直连，黄点=转码；常驻可读，悬停看完整原因 */
.mode-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.5;
  color: var(--sa-text-secondary);
  background: var(--sa-subtle);
  border: 1px solid var(--sa-border-subtle);
  cursor: default;
  white-space: nowrap;
}
.mode-dot {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  flex-shrink: 0;
  background: #22c55e;
  box-shadow: 0 0 0 2px color-mix(in oklab, #22c55e 22%, transparent);
}
.mode-chip.is-transcode .mode-dot {
  background: #f59e0b;
  box-shadow: 0 0 0 2px color-mix(in oklab, #f59e0b 22%, transparent);
}

/* ===== 章节分段条（叠播放器底部） ===== */
.hero-chapters {
  position: absolute;
  left: 14px;
  right: 14px;
  bottom: 62px;
  z-index: 6;
  pointer-events: none;
}
.hero-chapter-hint {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-size: 11px;
  color: rgba(255, 255, 255, 0.88);
  margin-bottom: 6px;
  text-shadow: 0 1px 6px rgba(0, 0, 0, 0.6);
}
.hero-chapter-name {
  color: color-mix(in oklab, var(--sa-accent) 60%, #fff);
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hero-chapters :deep(.chapter-bar) {
  pointer-events: auto;
}

.type-chip {
  padding: 2px 9px;
  border-radius: 7px;
  font-size: 11px;
  font-weight: 700;
  color: var(--sa-accent);
  background: var(--sa-accent-subtle);
}
.meta-strong {
  color: var(--sa-text-primary);
  font-weight: 600;
}
.title-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 6px;
  position: relative;
}
/* 艺人/组合双联胶囊已移入 VideoInfoDock（档案舱顶部） */

/* ===== 播放器下方的视频档案（无卡片外框，与老版一样直接铺在页面上）===== */
.info-below {
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin-top: 24px;
}

/* ===== 右侧控制中枢外壳 ===== */
.side-col {
  min-width: 0;
}
.dock-shell {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 4px;
  height: 100%;
  padding: 20px 22px 18px;
  border-radius: 16px;
  /* 「苹果系物理微光层」：发丝级边框 + 三层复合微阴影。
     旧的 26px 大圆角 + 6% 描边 + 单层 hover 阴影在同色底上会显得「厚」且边缘发灰，
     换成 16px 圆角 + 5% 发丝边 + 微光阴影后，轮廓是收敛的一条线，卡片是通透的。 */
  background: var(--sa-elevated);
  border: 1px solid var(--sa-hairline);
  box-shadow: var(--sa-shadow-premium);
}
/* 方案 I：下划线式双 Tab —— 无胶囊底板，只有 1px 基线与滑动下划线 */
.dock-tabs {
  position: relative;
  display: flex;
  align-items: center;
  gap: 18px;
  gap: 22px;
  padding-bottom: 14px;
  margin-bottom: 0;
  /* 卡片内的分区线：用最淡的一档，别在纯白卡上再压一条深线 */
  border-bottom: 1px solid var(--sa-border-subtle);
}
.dock-tab {
  position: relative;
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 0 0 10px;
  margin-bottom: -11px;
  border: none;
  background: transparent;
  font: inherit;
  cursor: pointer;
  white-space: nowrap;
  color: var(--sa-text-tertiary);
  transition: color 0.3s ease;
}
.dock-tab::after {
  content: '';
  position: absolute;
  left: 0;
  bottom: 0;
  width: 0;
  height: 1.5px;
  background: var(--sa-text-primary);
  transition: width 0.4s var(--spring);
}
.dock-tab--active::after {
  width: 100%;
}
.dock-tab:hover:not(.dock-tab--active) {
  color: var(--sa-text-secondary);
}
.dock-tab--active {
  color: var(--sa-text-primary);
}
.dock-tabs-actions {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.dock-tab-no {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 10px;
  letter-spacing: 0.08em;
  opacity: 0.75;
}
.dock-tab-label {
  font-size: 12px;
  letter-spacing: 0.12em;
}
/* 右舱只剩一个静态标题（不再有 Tab 切换）：按常规模块标题排，不再用大写字距 */
.dock-tab-label--head {
  font-size: 15px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--sa-text-primary);
}
.dock-tab--active .dock-tab-label {
  font-weight: 700;
}
.dock-tabs-tag {
  margin-left: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 9.5px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--sa-text-tertiary);
  opacity: 0.75;
}
.dock-body {
  min-width: 0;
  padding-top: 16px;
}
.dock-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}
.dock-empty {
  padding: 28px 0;
  text-align: center;
  font-size: 13px;
  color: var(--sa-text-tertiary);
}

@media (min-width: 1024px) {
  .side-col {
    position: sticky;
    top: 12px;
  }
}
@media (max-width: 768px) {
  .title-actions {
    margin-left: 0;
    width: 100%;
  }
  .hero-chapters {
    left: 10px;
    right: 10px;
    bottom: 56px;
  }
  .bio-card {
    flex-direction: column;
  }
}
.mv-container {
  /* 与顶栏品牌同宽：不设 max-width，左右只保留 --sa-gutter，
     和 logo / 导航的边界对齐，不再额外居中留白。 */
  max-width: none;
  margin: 0 auto;
  /* 顶栏 77px 之后不再额外留大间距，只留一条呼吸线 */
  padding: 12px var(--sa-gutter, 36px) 48px;
}
@media (max-width: 768px) {
  .mv-container {
    /* 顶部不留白，让播放器能顶到页面最上边 */
    padding: 0 16px 16px;
  }
  /* B 站式移动端视频详情页：播放器左右 + 上边全部顶边、去圆角。
     仅调整容器外观，播放器组件与功能不变。
     用 .mv-container 前缀提权，盖过后面同优先级的 .hero-cover 基础规则。 */
  .mv-container .hero-cover {
    border-radius: 0;
    margin: 0 -16px 16px;
  }

  /* 移动端视频档案：不要外层的白卡框，面板直接落在页面底色上 */
  .dock-shell {
    /* 桌面用 height:100% 与左列等高；移动端单列时这个 100% 会撑出一大块空白，
       把下面的「相关作品/相关艺人」顶到很下面去，所以必须回到内容高度。 */
    height: auto;
    padding: 0;
    border: none;
    border-radius: 0;
    background: transparent;
    box-shadow: none;
  }
  /* 档案已下沉到播放器下方：移动端收紧顶部间距 */
  .info-below {
    margin-top: 8px;
  }
  /* 右栏推荐移动端同样不要白卡框 */
  .rail-extra {
    margin-top: 12px;
    padding: 0;
    border: none;
    border-radius: 0;
    background: transparent;
    box-shadow: none;
  }
  /* 窄屏保持原尺寸，避免缩略图把标题挤没 */
  .rail-extra .related-thumb {
    width: 84px;
    height: 52px;
  }
  .rail-extra .artist-avatar {
    width: 44px;
    height: 44px;
    font-size: 17px;
  }
  /* 没有卡片框之后，tab 下面那条通栏基线会变成一条孤立的线，一并去掉；
     当前 tab 仍有自己的下划线，状态不受影响 */
  .dock-tabs {
    border-bottom: none;
  }
}

/* ===== 按钮 ===== */
.btn-ghost {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 500;
  color: var(--sa-text-secondary);
  background: var(--sa-subtle);
  border: 1px solid var(--sa-border-subtle);
  cursor: pointer;
  transition: all 0.2s;
}
.btn-ghost--icon {
  width: 32px;
  height: 32px;
  padding: 0;
  justify-content: center;
  border-radius: 9999px;
}
.meta-actions {
  display: none;
}
.collect-pop {
  position: absolute;
  top: 40px;
  right: 40px;
  z-index: 20;
}
.btn-ghost--on {
  color: var(--sa-accent);
}
.btn-ghost:hover:not(:disabled) {
  color: var(--sa-text-primary);
  background: var(--sa-hover);
}
.btn-ghost:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ===== 主布局 ===== */
.main-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 32px;
}
@media (min-width: 1024px) {
  .main-grid {
    /* 按比例分栏（约 1.8 : 1），而不是固定像素：
       窄窗口时右侧不会突然占掉近一半；右栏给 320px 下限保底。 */
    grid-template-columns: minmax(0, 1.8fr) minmax(320px, 1fr);
    gap: 24px;
    align-items: start;
  }
}

/* ===== Hero ===== */
.hero-cover {
  position: relative;
  aspect-ratio: 16 / 9;
  border-radius: 24px;
  overflow: hidden;
  background: var(--sa-subtle);
  margin-bottom: 0;
  box-shadow:
    0 28px 75px -15px rgba(0, 0, 0, 0.28),
    0 0 0 1px var(--sa-border-subtle);
}
/* 播放器画框固定 16:9：竖屏片也放进同一个横向框（画面 contain 居中、左右留黑边），
   不再按片源比例把整块变成竖的 —— 那会让页面高度来回跳、切场次时机位矩阵被推走。 */
.hero-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  /* 预播态封面可点击（PC 悬停唤出播放器 / 移动端单击唤出） */
  cursor: pointer;
}
.hero-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, var(--sa-subtle), var(--sa-elevated));
}
.hero-placeholder-play {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 64px;
  height: 64px;
  border-radius: 9999px;
  color: rgba(255, 255, 255, 0.9);
  background: rgba(0, 0, 0, 0.35);
  backdrop-filter: blur(8px);
  border: 1px solid rgba(255, 255, 255, 0.2);
}
.hero-player {
  position: relative;
  width: 100%;
  height: 100%;
}
/* B 站 iframe 承载：铺满 hero-cover，由 B 站播放器自己按片源比例留黑边 */
.bili-embed {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  background: #000;
}
.bili-embed-frame {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
}
/* 外链兜底：iframe 内部的「打开APP」在桌面端常常点不动，给一个明确出口 */
.bili-embed-link {
  position: absolute;
  top: 10px;
  right: 10px;
  z-index: 3;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 1.4;
  color: #fff;
  background: rgba(0, 0, 0, 0.55);
  text-decoration: none;
}
.bili-embed-link:hover {
  background: rgba(0, 0, 0, 0.75);
}
.hero-empty {
  position: absolute;
  inset: 0;
  z-index: 2;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--sa-text-tertiary);
  font-size: 14px;
  background: #000;
}
.hero-seeking {
  position: absolute;
  inset: 0;
  z-index: 3;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--sa-text-tertiary);
  font-size: 14px;
  background: rgba(0, 0, 0, 0.55);
}
.hero-status {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: 24px;
  background: rgba(0, 0, 0, 0.55);
}
.hero-status-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  min-width: 280px;
  max-width: 420px;
  text-align: center;
}
.hero-status-title {
  font-size: 14px;
  font-weight: 500;
  color: rgba(255, 255, 255, 0.92);
}
.hero-status-sub {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.6);
  word-break: break-all;
}
.hero-status-actions {
  display: flex;
  gap: 10px;
}
.hero-system-btn {
  padding: 7px 14px;
  border-radius: 9999px;
  font-size: 13px;
  font-weight: 500;
  color: #fff;
  background: rgba(255, 255, 255, 0.16);
  border: 1px solid rgba(255, 255, 255, 0.22);
  cursor: pointer;
  transition: all 0.2s;
}
.hero-system-btn:hover {
  background: rgba(255, 255, 255, 0.26);
  border-color: rgba(255, 255, 255, 0.35);
}

.hero-error {
  margin: 12px 0 0;
  padding: 8px 14px;
  border-radius: 8px;
  font-size: 13px;
  color: #f87171;
  background: rgba(248, 113, 113, 0.1);
  border: 1px solid rgba(248, 113, 113, 0.25);
}
.transcode-prompt-lead {
  margin: 0 0 8px;
  font-size: 14px;
  color: var(--sa-text-primary);
}
.transcode-prompt-detail {
  margin: 0 0 16px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--sa-text-tertiary);
  word-break: break-word;
}


/* ===== Description（简介抽屉内） ===== */
.desc-cn--clickable {
  cursor: pointer;
}
.desc-cn--clickable:hover {
  color: var(--sa-accent);
}
.desc-cn-arrow {
  margin-left: 6px;
  font-size: 12px;
  color: var(--sa-text-tertiary);
  display: inline-block;
  transition: transform 0.2s ease;
}
.desc-cn-arrow--open {
  transform: rotate(180deg);
}
.desc-original {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--sa-border-subtle);
  font-size: 13px;
  color: var(--sa-text-tertiary);
}

/* ===== Section（简介抽屉内的描述文字） ===== */

/* ===== Sidebar：双 Tab 控制舱 ===== */
/* ===== 右栏推荐：相关作品 / 相关艺人 =====
   按 `pro-card` 语言拆成两张独立卡片（原先挤在一个 26px 大圆角外框里，
   两个折叠标题叠在同一个白块上，层次是糊的）。外框退化成纯布局容器。 */
.rail-extra {
  display: flex;
  flex-direction: column;
  gap: 18px;
  margin-top: 18px;
}
.rail-extra .side-fold {
  margin: 0;
  padding: 18px 20px 16px;
  border: 1px solid var(--sa-hairline);
  border-radius: 16px;
  background: var(--sa-elevated);
  box-shadow: var(--sa-shadow-premium);
}
.rail-extra .side-fold:first-child {
  padding-top: 18px;
}
.rail-extra .side-fold + .side-fold {
  margin-top: 0;
}
.rail-extra .side-fold > summary {
  font-size: 15px;
  font-weight: 800;
  letter-spacing: -0.01em;
  text-transform: none;
  color: var(--sa-text-primary);
}
.rail-extra .side-fold[open] > summary {
  margin-bottom: 12px;
}
.rail-extra .related-thumb {
  width: 106px;
  height: 62px;
  border-radius: 8px;
}
.rail-extra .related-thumb img {
  width: auto;
  height: auto;
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}
.rail-extra .artist-avatar {
  width: 44px;
  height: 44px;
  font-size: 17px;
}
/* 作品标题最多两行，长标题按词换行，不再被压成一行 */
.rail-extra .related-title {
  font-size: 12.5px;
  font-weight: 700;
  line-height: 1.35;
  white-space: normal;
  word-break: break-word;
  max-height: 2.8em;
}
.rail-extra .related-meta {
  font-size: 11px;
}
.side-fold {
  border-top: 1px solid var(--sa-border-subtle);
  padding-top: 12px;
}
.side-fold summary {
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  color: var(--sa-text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  list-style: none;
  outline: none;
}
.side-fold summary::-webkit-details-marker {
  display: none;
}
.side-fold[open] summary {
  margin-bottom: 10px;
}

/* ===== B 站播放源绑定（P0 在线版） ===== */
.bili-bind {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.bili-bind-current {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.bili-bind-bv {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  color: var(--sa-accent);
  text-decoration: none;
}
.bili-bind-bv:hover {
  text-decoration: underline;
}
.bili-bind-origin {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 999px;
  color: var(--sa-text-tertiary);
  background: var(--sa-subtle);
}
.bili-bind-unlink {
  margin-left: auto;
  font-size: 12px;
}
.bili-bind-input {
  display: flex;
  gap: 8px;
}
.bili-bind-input :deep(.n-input) {
  flex: 1;
}
.bili-bind-hint {
  margin: 0;
  font-size: 11px;
  line-height: 1.6;
  color: var(--sa-text-tertiary);
}

/* 主列艺人双联胶囊见 .cast-pill（折叠区内沿用纵向 .artist-list） */
.related-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.related-item {
  display: flex;
  gap: 12px;
  padding: 8px;
  border-radius: 10px;
  text-align: left;
  background: transparent;
  border: none;
  cursor: pointer;
  /* 卡片内的行悬停：3.5% 主色 ≈ 纸上一道极浅的灰，比纯 --sa-subtle 更轻 */
  transition: background-color 0.2s ease;
}
.related-item:hover {
  background: color-mix(in oklab, var(--sa-text-primary) 3.5%, transparent);
}
.related-thumb {
  width: 80px;
  height: 50px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  overflow: hidden;
  flex-shrink: 0;
  background: var(--sa-subtle);
}
/* 封面有 16:9（官方缩略图）也有 9:16（竖版直拍截帧）：
   固定槽位 + contain 等比内缩，保证整张封面露出来，不再被 cover 裁成一条 */
.related-thumb img {
  width: auto;
  height: auto;
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  display: block;
}
.thumb-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--sa-text-tertiary);
}
.related-info {
  min-width: 0;
}
.related-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--sa-text-primary);
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.related-meta {
  font-size: 12px;
  color: var(--sa-text-tertiary);
  margin-top: 2px;
}

/* ===== Artist capsule ===== */
.artist-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.artist-capsule {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px;
  border: none;
  background: none;
  border-radius: 10px;
  text-align: left;
  width: 100%;
  font: inherit;
}
.artist-capsule:disabled {
  cursor: default;
}
.artist-capsule--link {
  cursor: pointer;
  transition: background-color 0.2s ease;
}
.artist-capsule--link:hover {
  background: color-mix(in oklab, var(--sa-text-primary) 3.5%, transparent);
}
.artist-avatar {
  width: 40px;
  height: 40px;
  border-radius: 9999px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.9);
  overflow: hidden;
}
.artist-avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.artist-info {
  min-width: 0;
}
.artist-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--sa-text-primary);
}
.artist-sub {
  font-size: 12px;
  color: var(--sa-text-tertiary);
  margin-top: 1px;
}

/* ===== 编辑弹窗 ===== */
.edit-form {
  max-height: 70vh;
  overflow-y: auto;
  padding-right: 4px;
}
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 16px;
}
.col-2 {
  grid-column: 1 / -1;
}
@media (max-width: 900px) {
  .form-grid {
    grid-template-columns: 1fr;
  }
}

/* ===== 更改存储路径 ===== */
.relocate-block {
  grid-column: 1 / -1;
  margin-top: 4px;
  padding: 12px 14px;
  border: 1px dashed var(--sa-border-subtle);
  border-radius: 10px;
  background: var(--sa-subtle);
}
.relocate-block--muted {
  color: var(--sa-text-tertiary);
  font-size: 13px;
}
.relocate-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.relocate-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0;
  border: none;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
  text-align: left;
}
.relocate-danger-tag {
  flex-shrink: 0;
  font-size: 11px;
  line-height: 1;
  padding: 3px 6px;
  border-radius: 4px;
  color: #f87171;
  background: rgba(248, 113, 113, 0.12);
  border: 1px solid rgba(248, 113, 113, 0.35);
}
.relocate-arrow {
  font-size: 11px;
  display: inline-block;
  transition: transform 0.2s ease;
}
.relocate-arrow--open {
  transform: rotate(180deg);
}
.relocate-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--sa-text-primary);
}
.cover-state {
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.relocate-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  min-width: 0;
}
.relocate-line code {
  font-size: 12px;
  color: var(--sa-text-secondary);
  overflow-wrap: anywhere;
}
.relocate-label,
.relocate-badge {
  flex-shrink: 0;
  font-size: 11px;
  line-height: 1;
  padding: 3px 6px;
  border-radius: 4px;
}
.relocate-label {
  background: var(--sa-subtle);
  border: 1px solid var(--sa-border-subtle);
  color: var(--sa-text-tertiary);
}
.relocate-badge {
  background: var(--sa-accent-subtle);
  border: 1px solid var(--sa-accent-border);
  color: var(--sa-accent);
}
.relocate-norule {
  font-size: 12px;
  color: var(--sa-text-tertiary);
  margin-bottom: 8px;
}
.relocate-row {
  display: flex;
  gap: 8px;
  align-items: center;
}
.relocate-row .n-input {
  flex: 1;
}
.score-ring-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border: none;
  border-radius: 999px;
  background: transparent;
  cursor: pointer;
}
.score-ring-btn:disabled {
  opacity: 0.5;
}
.score-ring-btn .ring-track {
  fill: none;
  stroke: var(--sa-border);
  stroke-width: 3.2;
}
.score-ring-btn .ring-arc {
  fill: none;
  stroke: var(--sa-accent);
  stroke-width: 3.2;
  stroke-linecap: round;
  transition: stroke-dasharray 0.5s ease;
}
.score-ring-btn.good .ring-arc {
  stroke: #34c77b;
}
.score-ring-btn.bad .ring-arc {
  stroke: #f05252;
}
.score-ring-btn .ring-text {
  font-size: 12px;
  font-weight: 800;
  fill: var(--sa-text-primary);
  text-anchor: middle;
  font-variant-numeric: tabular-nums;
}
.score-head {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 12px;
}
.score-video-name {
  font-size: 14px;
  font-weight: 650;
  color: var(--sa-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.score-meta {
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.score-error {
  margin-bottom: 12px;
  padding: 10px 14px;
  border-radius: 10px;
  background: rgba(240, 82, 82, 0.1);
  color: #f05252;
  font-size: 13px;
  line-height: 1.5;
}
.score-dims {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.score-dim {
  display: grid;
  grid-template-columns: 76px 34px minmax(0, 1fr);
  gap: 12px;
  align-items: baseline;
  font-size: 13px;
}
.score-dim-name {
  color: var(--sa-text-secondary);
}
.score-dim-score {
  font-size: 18px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  text-align: center;
  color: var(--sa-text-primary);
}
.score-dim-score.good {
  color: #34c77b;
}
.score-dim-score.mid {
  color: var(--sa-text-primary);
}
.score-dim-score.bad {
  color: #f05252;
}
.score-dim-reason {
  color: var(--sa-text-tertiary);
  font-size: 12px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.score-comment {
  margin: 10px 0 0;
  padding-top: 10px;
  border-top: 1px solid var(--sa-border-subtle);
  font-size: 12px;
  line-height: 1.7;
  color: var(--sa-text-secondary);
}
.score-empty {
  padding: 24px 0;
  font-size: 13px;
  color: var(--sa-text-tertiary);
}
.score-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 14px;
  font-size: 13px;
  color: var(--sa-text-secondary);
}

.relocate-hint {
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--sa-text-tertiary);
}


/* ================= 移动端设计稿（2026-11-13）：标题卡 + 双分段 Pager ================= */
.v-m-header {
  display: none;
}

@media (max-width: 768px) {
  .mv-page {
    min-height: 100vh;
    background: var(--sa-bg);
    color: var(--sa-text-primary);
  }
  .mv-container {
    padding: 0 0 16px;
    background: var(--sa-bg);
  }
  .main-grid {
    display: block;
    gap: 0;
  }
  .ambient {
    display: none;
  }
  .main-col,
  .side-col {
    width: 100%;
  }
  /* 收藏浮层在 .main-col 里，右栏同为 z-index:1 且排在后面会盖住它 —— 把左栏提一层 */
  .main-col {
    z-index: 3;
  }
  .hero-cover {
    margin: 0;
    border-radius: 0;
  }
  .hero-meta-bar {
    display: none;
  }

  /* --- 标题信息卡 --- */
  .v-m-header {
    display: block;
    padding: 16px 16px 14px;
    background: var(--sa-elevated);
    border-bottom: 1px solid var(--sa-border-subtle);
  }
  .v-m-title {
    margin: 0 0 12px;
    font-size: 16px;
    font-weight: 700;
    line-height: 1.35;
    color: var(--sa-text-primary);
  }
  .v-m-actions {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
  }
  .v-m-actor {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    padding: 0;
    border: 0;
    background: transparent;
    color: inherit;
    text-align: left;
    cursor: pointer;
  }
  .v-m-actor-avatar {
    width: 28px;
    height: 28px;
    flex-shrink: 0;
    border-radius: 50%;
    object-fit: cover;
    background: #1c1f26;
  }
  .v-m-actor-avatar--empty {
    display: inline-block;
  }
  .v-m-actor-text {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .v-m-actor-text b {
    font-size: 12px;
    font-weight: 600;
    color: var(--sa-text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .v-m-actor-text i {
    font-style: normal;
    font-size: 10px;
    color: var(--sa-text-tertiary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  /* 右侧工具组：分段开关 + 图标按钮 */
  .v-m-tools {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: none;
  }
  .v-m-switch {
    display: inline-flex;
    align-items: center;
    flex: none;
    height: 28px;
    padding: 0 11px;
    border: 1px solid var(--sa-border-subtle);
    border-radius: 999px;
    background: var(--sa-subtle);
    color: var(--sa-text-secondary);
    font-family: inherit;
    font-size: 11.5px;
    font-weight: 600;
    white-space: nowrap;
    cursor: pointer;
    transition: color 0.18s ease, background-color 0.18s ease;
  }
  .v-m-switch:hover {
    color: var(--sa-text-primary);
  }
  .v-m-quick {
    position: relative;
    display: flex;
    align-items: center;
    gap: 4px;
    flex: none;
  }
  .v-m-quick .btn-ghost--icon {
    width: 30px;
    height: 30px;
  }
  .v-m-quick .collect-pop {
    top: 38px;
    right: 0;
  }

  /* --- 两页内容切换：场次机位内部逻辑完全不动 --- */
  .mobile-seg-cams .info-below,
  .mobile-seg-cams .rail-extra {
    display: none !important;
  }
  .mobile-seg-archive .dock-shell {
    display: none !important;
  }
  .mobile-seg-archive .side-col {
    height: auto;
  }
  .dock-shell {
    height: auto;
    padding: 0;
    background: transparent;
    border: none;
    border-radius: 0;
    box-shadow: none;
  }

  /* --- 场次机位：和档案页同一块白色版面（无卡片、无灰底） ---
     内容只有两三条时，白色必须铺满整屏，否则下方会剩一大块灰底。 */
  .mv-page.mobile-seg-cams,
  .mobile-seg-cams .mv-container {
    background: var(--sa-elevated);
  }
  .mobile-seg-cams .side-col {
    padding: 0 16px 12px;
    background: var(--sa-elevated);
  }
  /* 留白原来叠了三层：shell 16 + body 22 + sheet 12 = 50px，全部归零 */
  .mobile-seg-cams .dock-body {
    padding-top: 0;
  }
  .mobile-seg-cams :deep(.stage-sheet) {
    gap: 0;
    padding-top: 0;
  }
  /* 分栏标签行（打歌舞台 / 官方影像 / 粉丝直拍）——只给外层 .block-head 一次内边距 */
  .mobile-seg-cams :deep(.block-head) {
    padding: 12px 0 8px;
  }
  /* 机位行由卡片改为细线分隔的行 */
  .mobile-seg-cams :deep(.cam-list) {
    gap: 0;
  }
  .mobile-seg-cams :deep(.cam-item) {
    position: relative;
    padding: 11px 0;
    border: 0;
    border-top: 1px solid var(--sa-border-subtle);
    border-radius: 0;
    background: transparent;
  }
  .mobile-seg-cams :deep(.cam-item:first-child) {
    border-top: 0;
  }
  .mobile-seg-cams :deep(.cam-item:hover),
  .mobile-seg-cams :deep(.cam-item.is-active) {
    background: transparent;
    border-color: var(--sa-border-subtle);
  }
  /* 正在播放：左侧一道短竖线 + 原有的声波动画 */
  .mobile-seg-cams :deep(.cam-item.is-active)::before {
    content: '';
    position: absolute;
    left: -9px;
    top: 50%;
    width: 2px;
    height: 18px;
    margin-top: -9px;
    border-radius: 2px;
    background: var(--sa-text-primary);
  }
  /* 场次选择器改成一行字段，不再是一张卡 */
  .mobile-seg-cams :deep(.sp-trigger) {
    padding: 12px 0;
    border: 0;
    border-top: 1px solid var(--sa-border-subtle);
    border-bottom: 1px solid var(--sa-border-subtle);
    border-radius: 0;
    background: transparent;
    box-shadow: none;
  }
  .mobile-seg-cams :deep(.sp-trigger:hover),
  .mobile-seg-cams :deep(.sp-trigger.is-sheet) {
    border-color: var(--sa-border-subtle);
  }
  .dock-tabs {
    border: 0;
    padding: 0 0 12px;
  }
  .dock-tab-label--head {
    font-size: 13px;
    font-weight: 700;
    color: var(--sa-text-primary);
    letter-spacing: 0;
  }
  .dock-empty {
    padding: 16px;
    border: 1px solid var(--sa-border-subtle);
    border-radius: 10px;
    background: var(--sa-subtle);
    color: var(--sa-text-secondary);
    font-size: 12px;
  }

  /* --- 档案页：与标题栏、分段栏连成一片 ---
     业主 2026-11-13：上方分段是方角、下面卡片是圆角，交界处会露角。
     解法是**不做卡片**——标题栏 / 分段栏 / 档案区同属一块白色版面，
     内部只用 1px 细线分节，没有任何外框与圆角。 */
  .info-below {
    margin: 0;
    padding: 0 16px 6px;
    gap: 0;
    border: 0;
    border-radius: 0;
    background: var(--sa-elevated);
  }
  .info-below :deep(.dossier) {
    gap: 0;
    color: var(--sa-text-primary);
  }
  .info-below :deep(.dossier-title-row) {
    display: none;
  }
  .info-below :deep(.dossier-head) {
    margin: 0;
    padding: 14px 0 12px;
    border: 0;
    border-radius: 0;
    background: transparent;
  }
  .info-below :deep(.ds-ledger) {
    margin: 0;
    padding: 2px 0 6px;
    border: 0;
    border-top: 1px solid var(--sa-border-subtle);
    background: transparent;
  }
  .info-below :deep(.ds-file) {
    margin: 0;
    padding: 13px 0 14px;
    border: 0;
    border-top: 1px solid var(--sa-border-subtle);
    border-radius: 0;
    background: transparent;
  }
  /* 文件名不再单套内框，跟账本同一条竖线排下去 */
  .info-below :deep(.ds-file-name) {
    padding: 0;
    border: 0;
    background: transparent;
    font-size: 12px;
    line-height: 1.6;
  }
  /* 播放源 / 关联标签同属这块版面，只留分隔线 */
  .info-below .side-fold {
    margin: 0;
    padding: 13px 0 14px;
    border: 0;
    border-top: 1px solid var(--sa-border-subtle);
  }
  .info-below :deep(.dossier-desc),
  .info-below :deep(.ds-value),
  .info-below :deep(.dossier-title) {
    color: var(--sa-text-primary);
  }
  .info-below :deep(.ds-label) {
    color: var(--sa-text-secondary);
  }
  .info-below :deep(.ds-row) {
    border-color: var(--sa-border-subtle);
    /* 手机窄：标签列从 112 收到 92，值列多出 24px，长场次/母带规格不再折行 */
    gap: 12px;
  }
  .info-below :deep(.ds-label) {
    flex-basis: 92px;
  }
  .side-fold {
    border-color: var(--sa-border-subtle);
  }
  .side-fold > summary {
    color: var(--sa-text-primary);
  }
  /* 推荐区接在档案白版面后面，不再是一张张白卡。
     PC 端把每个 side-fold 做成了独立卡片，手机上全部退回「无框 + 细线分节」。 */
  .rail-extra {
    display: block;
    margin-top: 0;
    padding: 4px 16px 14px;
    border: 0;
    border-radius: 0;
    background: var(--sa-elevated);
    box-shadow: none;
  }
  .rail-extra .side-fold {
    margin: 0;
    padding: 13px 0 14px;
    border: 0;
    border-top: 1px solid var(--sa-border-subtle);
    border-radius: 0;
    background: transparent;
    box-shadow: none;
  }
  .rail-extra .side-fold:first-child {
    padding-top: 4px;
    border-top: 0;
  }
  .rail-extra .side-fold > summary {
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0;
  }
  .rail-extra .side-fold[open] > summary {
    margin-bottom: 8px;
  }
  .rail-extra .related-list,
  .rail-extra .artist-list {
    gap: 0;
  }
  .rail-extra .related-item,
  .rail-extra .artist-capsule {
    padding: 10px 0;
    border: 0;
    border-radius: 0;
    background: transparent;
    color: var(--sa-text-primary);
  }
  .rail-extra .related-list > .related-item:first-child {
    border-top: 0;
  }
  .rail-extra .related-item + .related-item,
  .rail-extra .artist-capsule + .artist-capsule {
    border-top: 1px solid var(--sa-border-subtle);
  }
  .rail-extra .related-item:hover,
  .rail-extra .artist-capsule--link:hover {
    background: transparent;
  }
  .rail-extra .related-title,
  .rail-extra .artist-name {
    color: var(--sa-text-primary);
  }
  .rail-extra .related-meta,
  .rail-extra .artist-sub {
    color: var(--sa-text-secondary);
  }
}



/* 移动端播放器占位与右缘对齐修正（覆盖更早的负 margin 规则） */
@media (max-width: 768px) {
  .mv-container .hero-cover {
    margin: 0;
    border-radius: 0;
  }
  .hero-placeholder {
    background: #000;
  }
  .hero-placeholder-play {
    background: rgba(255, 255, 255, 0.25);
    color: #fff;
    border: 1px solid rgba(255, 255, 255, 0.4);
  }
  .dock-tabs {
    display: none;
  }
}



@media (max-width: 768px) {
  .rail-extra .related-thumb {
    background: var(--sa-subtle);
  }
  .rail-extra .thumb-placeholder {
    background: var(--sa-subtle);
    color: var(--sa-text-tertiary);
  }
  .rail-extra .artist-avatar {
    border: 1px solid var(--sa-border-subtle);
  }
  .bili-bind-current,
  .bili-bind-hint {
    color: var(--sa-text-secondary);
  }
}

</style>
