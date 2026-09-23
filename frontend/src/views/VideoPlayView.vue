<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
import SaHeader from '@/components/SaHeader.vue'
import SaSelect from '@/components/SaSelect.vue'
import type { SaOption } from '@/components/SaSelect.vue'
import VideoTrackList from '@/components/VideoTrackList.vue'
import type { TrackRow } from '@/components/VideoTrackList.vue'
import { albumsApi } from '@/api/albums'
import { artistsApi } from '@/api/artists'
import { groupsApi } from '@/api/groups'
import { libraryApi } from '@/api/library'
import { musicVideosApi } from '@/api/musicVideos'
import { playbackApi, type CreatePlaybackSessionPayload } from '@/api/playback'
import { dashboardApi } from '@/api/dashboard'
import { systemApi } from '@/api/system'
import type {
  AlbumBrief,
  ArtistBrief,
  GroupBrief,
  MusicVideo,
  MusicVideoBrief,
  PlaybackSession,
  SongBrief,
  VideoPathSuggestion,
  VideoType,
} from '@/types/models'
import { QUALITY_LABEL, VIDEO_TYPE_OPTIONS, VIDEO_TYPE_LABEL } from '@/types/models'
import {
  PlayArrowOutlined,
  EditOutlined,
  MovieOutlined,
  BookmarkFilled,
  BookmarkOutlined,
  FolderOutlined,
  RefreshOutlined,
} from '@/components/icons'
import VideoCollectPicker from '@/components/VideoCollectPicker.vue'
import CoverFramePicker from '@/components/CoverFramePicker.vue'
import { videoCollectionsApi } from '@/api/videoCollections'
import { formatDuration, formatFileSize, formatResolution, formatDate, formatBitrate } from '@/utils/format'
import { artistPath, fetchByRouteParam, groupPath, songPath, videoPath } from '@/utils/routes'
import { IMG_W_FULL, IMG_W_THUMB } from '@/utils/imageSizes'
import { RESOLUTION_TIERS } from '@/utils/resolution'
import { useSettingsStore } from '@/stores/settings'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const settings = useSettingsStore()

const video = ref<MusicVideo | null>(null)
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
// 是否曾经开始过播放：一旦为 true，「待播放」大遮罩（hero-overlay）永不再出现，
// seek 跳转中只显示「正在跳转…」缓冲提示，不会掉回待播放再从头播
const hasEverPlayed = ref(false)
const brokenThumbs = ref<Set<number>>(new Set())
const brokenRelatedThumbs = ref<Set<number>>(new Set())
const related = ref<MusicVideoBrief[]>([])
const showOriginal = ref(false)
const collectOpen = ref(false)
const savedCollectionIds = ref<number[]>([])

const isCollected = computed(() => savedCollectionIds.value.length > 0)

function applyVideoMembership(_id: number, ids: number[]) {
  savedCollectionIds.value = ids
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

// 手机窄屏：竖屏片点播放后用页面内竖屏播放器（不是全屏），暂停再回到 16:9
const phoneViewport = ref(
  typeof window !== 'undefined' && window.matchMedia('(max-width: 768px)').matches,
)
const mediaPaused = ref(true)
const isPortraitSource = computed(() => {
  const w = video.value?.width
  const h = video.value?.height
  return !!w && !!h && h > w
})
// 手机窄屏 + 竖屏片：用「限高黑框」承载（B 站式，画面 contain 居中、左右留黑边）。
// ⚠ 刻意**不联动播放状态**。旧实现是 `playing && !mediaPaused.value`，结果：
//   点播放 / 暂停 / 切画质 / 拖进度（转码换源会先 pause 再 play）时，盒子在 16:9 与片源比例
//   之间来回切换 —— 实测 390×844 待播 219px → 播放 636px，单次跳变 417px，整页高度跟着变
//   216px，下方内容被整体推走，就是「竖屏页不稳定」的根因。盒子尺寸只该由「是不是竖屏片」决定。
//   盒子高度是固定值（58svh，见样式），不再需要把片源比例传进 CSS。
const portraitBox = computed(
  () => isMobile && phoneViewport.value && isPortraitSource.value,
)

function onPlayerPlay() {
  mediaPaused.value = false
}
function onPlayerPause() {
  mediaPaused.value = true
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
// v3.4.9 键升版：v1 条目全部写于「AV1/VP9 直出被 xgplayer-hls 兜底 avc1 打挂」的年代，
// 直出修复后那些「失败」不再是真实能力边界 —— 整体作废重来，避免同类片被旧记忆
// 无谓地压到转码档。
// v3.5.0：HLS 内核换 hls.js，AV1/VP9 可直出，真实失败大幅减少；记忆 TTL 收紧到 7 天，
// 只对「原画直出 + 非转码」情形下的解码失败标记（见 onPlayerState），避免陈旧误判
// 长时间把同类片压到转码档。原画始终可手动选（弹窗提供「仍尝试原画」）。
const ORIG_FAIL_KEY = 'kpml_origfail_classes_v2'
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
  message.warning(detail.replace(/^Transcode[：:]\s*/, '') || '原画无法直出，已自动转码播放。')
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
  // 新会话 = 新的「已可播范围」，重新开始轮询（内部会先归零）
  startWindowPoll()
}

const hasSourceFile = computed(() => !!(video.value && video.value.file_path))

const sources = computed(() => {
  const s = session.value
  if (!s) return []
  const label = qualityLabel(activeQuality.value)
  if (s.manifest_url) {
    return [{ src: s.manifest_url, type: 'application/x-mpegURL', label }]
  }
  if (s.stream_url) {
    const ext = (s.container || '').toLowerCase()
    const type =
      ext === 'webm' || ext === 'mkv' ? 'video/webm'
      : ext === 'mp4' || ext === 'm4v' || ext === 'mov' ? 'video/mp4'
      : 'video/mp4'
    return [{ src: s.stream_url, type, label }]
  }
  return []
})

const activeLabel = computed(() => qualityLabel(activeQuality.value))

function qualityLabel(key: string) {
  return QUALITY_LABEL[key] || key.toUpperCase()
}

function onPlayerState(payload: { buffering: boolean; error: string | null }) {
  playerBuffering.value = payload.buffering
  playerError.value = payload.error || ''
  if (payload.error && /401|未登录|unauthorized/i.test(payload.error)) {
    router.replace({ name: 'login', query: { redirect: route.fullPath } })
    return
  }
  if (
    payload.error &&
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
  playing.value = true
  hasEverPlayed.value = true
  if (!s) {
    await startPlayAt(target)
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
        await startPlayAt(target)
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

function formatDesc(text?: string | null) {
  return (text || '').replace(/\{BR\}/g, '\n')
}

const hasOriginalDesc = computed(() => {
  const v = video.value
  return !!(v && v.chinese_description && v.description && v.description !== v.chinese_description)
})

function onDescClick() {
  if (hasOriginalDesc.value) showOriginal.value = !showOriginal.value
}

async function load(opts: { keepPlayback?: boolean } = {}) {
  const seq = ++loadSeq
  const param = routeKey.value.trim()
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
    detailsExpanded.value = false
    if (mv.uid && mv.uid !== param) {
      router.replace(videoPath(mv.uid))
    }
    showOriginal.value = false
    if (!opts.keepPlayback) {
      playing.value = false
      hasEverPlayed.value = false
      mediaPaused.value = true
      playerBuffering.value = false
      playerError.value = ''
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
}

async function loadRelated() {
  const v = video.value
  if (!v) return
  const params: { song_id?: number; subject_artist_id?: number; page_size: number } = { page_size: 6 }
  if (v.song_id) params.song_id = v.song_id
  else if (v.subject_artist_id) params.subject_artist_id = v.subject_artist_id
  else {
    related.value = []
    return
  }
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), 8000)
  try {
    const res = await musicVideosApi.list(
      { ...params, ingestion_status: 'library' },
      { signal: controller.signal },
    )
    related.value = res.items.filter((x) => x.id !== v.id).slice(0, 5)
  } catch {
    related.value = []
  } finally {
    window.clearTimeout(timer)
  }
}

// ===== 预播态交互 =====
// 预播 = 纯封面（不挂播放器组件）。唤出（armed）后显示播放器但不出画：
// PC 悬停视频区域唤出、点击即播；移动端单击唤出、双击（350ms 内两次点击）即播。
const playerArmed = ref(false)
const playerVisible = computed(() => playing.value || playerArmed.value)
// 悬停语义只在真指针设备启用（触屏设备 matchMedia('hover:hover') 为 false，走单击/双击）
const canHover =
  typeof window !== 'undefined' &&
  window.matchMedia('(hover: hover) and (pointer: fine)').matches
// 移动端双击判定：两次点击间隔阈值（ms）
const DOUBLE_TAP_MS = 350
let lastHeroTapAt = 0

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
  // 移动端：单击封面唤出播放器；时间戳同时供覆盖层做双击判定
  if (canHover || !hasSourceFile.value || playing.value || hasEverPlayed.value) return
  lastHeroTapAt = Date.now()
  playerArmed.value = true
}

function onHeroOverlayTap() {
  // PC：单击画面即播；移动端：350ms 内第二次点击（双击）即播
  if (playing.value || hasEverPlayed.value) return
  if (canHover) {
    startPlay()
    return
  }
  const now = Date.now()
  if (now - lastHeroTapAt < DOUBLE_TAP_MS) {
    lastHeroTapAt = 0
    startPlay()
  } else {
    lastHeroTapAt = now
  }
}

async function startPlay() {
  if (!video.value) return
  playing.value = true
  hasEverPlayed.value = true
  mediaPaused.value = false
  // 已有会话且源就绪：只把 pending 关掉，播放器 watch 会接着 play()
  if (session.value && sources.value.length) return
  await startPlayAt(lastKnownTime.value || 0)
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

async function startPlayAt(t: number) {
  if (!video.value) return
  const target = Math.max(0, t)
  const requested = activeQuality.value
  const wantOriginal = requested === 'original'
  playing.value = true
  hasEverPlayed.value = true
  mediaPaused.value = false
  sessionLoading.value = true
  playerError.value = ''
  const seq = ++seekSeq
  try {
    const next = await createSessionWithRetry(requested, target)
    if (seq !== seekSeq) {
      await stopSession(next.session_id)
      return
    }
    if (wantOriginal && next.play_mode === 'transcode') {
      // 服务端判定「original 根本无法直接播」（如 Safari 解不了 HEVC）：
      // 同样记入失败记忆，下次同类片默认直接转码
      markOrigFailClass(video.value)
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
    playerError.value = text
    message.error(text)
    if (!session.value) {
      playing.value = false
      hasEverPlayed.value = false
    }
  } finally {
    if (seq === seekSeq) sessionLoading.value = false
  }
}

// ===== Playback Session（统一播放链路，播放策略由服务端决定） =====
function playModeLabel(mode: string) {
  if (mode === 'direct_play') return '原画直连'
  if (mode === 'direct_stream') return '原画直出'
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
function probeVideoConfig(width?: number, height?: number): VideoConfiguration {
  const h = Math.max(480, Math.min(Math.round(height || 1080), 2160))
  const tier = [480, 720, 1080, 1440, 2160].find((t) => h <= t) ?? 2160
  const aspect = width && height ? width / height : 16 / 9
  const bitrate =
    tier >= 2160 ? 15_000_000
      : tier >= 1440 ? 9_000_000
        : tier >= 1080 ? 6_000_000
          : tier >= 720 ? 3_500_000
            : 1_800_000
  return {
    contentType: 'video/mp4',
    width: Math.max(320, Math.round(tier * aspect)),
    height: tier,
    bitrate,
    framerate: 30,
  }
}

async function clientCapabilities(): Promise<Record<string, unknown>> {
  if (typeof document === 'undefined') return {}
  const ua = typeof navigator !== 'undefined' ? navigator.userAgent || '' : ''
  const isIOS = /iPhone|iPad|iPod/i.test(ua)
  const isAppleSafari =
    isIOS || (/Safari/i.test(ua) && !/Chrome|CriOS|Chromium/i.test(ua))

  // 无 MediaCapabilities API 的旧浏览器：回退 canPlayType 粗判（旧口径整体保留）
  if (typeof navigator.mediaCapabilities?.decodingInfo !== 'function') {
    return legacyClientCapabilities(ua, isIOS, isAppleSafari)
  }

  const dims = probeVideoConfig(video.value?.width ?? undefined, video.value?.height ?? undefined)

  const [
    h264Anchor, h264, hevc, av1File, av1Ms, vp9File, vp9Ms,
    aac, opus, mp3, flac, vorbis, ac3, eac3, mpegts, fmp4Ms,
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
    probeDecoding('file', { ...dims, contentType: 'video/webm; codecs="av01.0.05M.08"' }),
    probeDecoding('media-source', { ...dims, contentType: 'video/mp4; codecs="av01.0.13M.08"' }),
    probeDecoding('file', { ...dims, contentType: 'video/webm; codecs="vp09.00.51.08"' }),
    probeDecoding('media-source', { ...dims, contentType: 'video/mp4; codecs="vp09.00.51.08"' }),
    // 音频编码
    probeDecoding('file', { contentType: 'audio/mp4; codecs="mp4a.40.2"', bitrate: 128_000 }),
    probeDecoding('file', { contentType: 'audio/webm; codecs="opus"', bitrate: 128_000 }),
    probeDecoding('file', { contentType: 'audio/mpeg', bitrate: 192_000 }),
    probeDecoding('file', { contentType: 'audio/flac', bitrate: 900_000 }),
    probeDecoding('file', { contentType: 'audio/webm; codecs="vorbis"', bitrate: 128_000 }),
    probeDecoding('file', { contentType: 'audio/mp4; codecs="ac-3"', bitrate: 384_000 }),
    probeDecoding('file', { contentType: 'audio/mp4; codecs="ec-3"', bitrate: 384_000 }),
    // 交付形态：MPEG-TS（H.264 转码）/ fMP4（AV1/VP9 remux 直出）
    probeDecoding('file', { ...dims, contentType: 'video/mp2t; codecs="avc1.42E01E, mp4a.40.2"' }),
    probeDecoding('media-source', { ...dims, contentType: 'video/mp4; codecs="avc1.42E01E, mp4a.40.2"' }),
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
    (vp9File.supported || vp9Ms.supported || canProbablySafe('video/webm; codecs="vp09.00.51.08"')) &&
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
  if (av1File.supported || canProbablySafe('video/webm; codecs="av01.0.05M.08"')) progressiveVideo.push('av1')
  if (vp9File.supported || canProbablySafe('video/webm; codecs="vp9"')) progressiveVideo.push('vp9')
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
  const canMaybeType = (mime: string) => {
    const r = probeEl.canPlayType?.(mime) || ''
    return r === 'probably' || r === 'maybe'
  }
  const containers = isAppleSafari
    ? ['mp4', 'm4v', 'mov']
    : ['mp4', 'm4v', 'mov', 'webm']
  if (
    !isAppleSafari &&
    progressiveVideo.length > 0 &&
    (canMaybeType('video/x-matroska; codecs="vp9"') || canMaybeType('video/x-matroska; codecs="avc1.42E01E"'))
  ) {
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
    // 本播放器：Apple 走 <video> 原生 HLS，其它浏览器走 hls.js（MSE），
    // 两者都能播服务端交付的 HLS，一律报 true。
    supports_hls: true,
    supports_mpegts: isAppleSafari ? true : mpegts.supported,
    // fMP4 交付可行性以 MSE 实测为准（decodingInfo 在个别环境全 false，但 MSE 真实可用）
    supports_fmp4: isAppleSafari ? true : (mseOk('video/mp4; codecs="avc1.42E01E"') || fmp4Ms.supported),
    supports_h264: videoCodecs.includes('h264'),
    supports_hevc: videoCodecs.includes('hevc'),
    supports_av1: videoCodecs.includes('av1'),
    supports_vp9: videoCodecs.includes('vp9'),
    supports_aac: audioCodecs.includes('aac'),
    supports_ac3: ac3.supported,
    supports_eac3: eac3.supported,
    max_width: 0,
    max_height: 0,
    user_agent: ua,
  }
}

/** 无 MediaCapabilities API 的旧浏览器回退：canPlayType / isTypeSupported 粗判（旧口径）。 */
function legacyClientCapabilities(ua: string, isIOS: boolean, isAppleSafari: boolean): Record<string, unknown> {
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
  if (
    !isAppleSafari &&
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
    supports_hls: true,
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
    max_width: 0,
    max_height: 0,
    user_agent: ua,
  }
}

async function createSession(quality: string, startSeconds: number): Promise<PlaybackSession> {
  const payload: CreatePlaybackSessionPayload = {
    music_video_id: id.value,
    requested_quality: quality,
    start_seconds: startSeconds,
    client: await clientCapabilities(),
  }
  return playbackApi.create(payload)
}

// 切换画质：播放中新建对应规格的 PlaybackSession，旧会话交由服务端引用计数关闭；待播放时仅更新预选画质
async function selectQuality(key: string) {
  if (!video.value) return
  if (key === activeQuality.value) {
    if (!playing.value) return
    if (key !== 'original' || session.value?.play_mode !== 'transcode') return
  }
  userPickedQuality.value = true
  activeQuality.value = key
  if (!playing.value) return
  await startPlayAt(lastKnownTime.value || 0)
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

let phoneMq: MediaQueryList | null = null
function onPhoneViewportChange() {
  phoneViewport.value = phoneMq?.matches ?? phoneViewport.value
}

onMounted(() => {
  load()
  refreshHwAccel()
  phoneMq = window.matchMedia('(max-width: 768px)')
  onPhoneViewportChange()
  phoneMq.addEventListener('change', onPhoneViewportChange)
})

onBeforeUnmount(() => {
  phoneMq?.removeEventListener('change', onPhoneViewportChange)
  phoneMq = null
  if (seekDebounceTimer !== null) {
    window.clearTimeout(seekDebounceTimer)
    seekDebounceTimer = null
  }
  seekSeq += 1
  seekUiGen += 1
  pingWatch({ playing: false })
  stopWatchPings()
  stopHeartbeat()
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
    const s = session.value
    if (s) {
      pingWatch({ playing: false })
      stopWatchPings()
      stopHeartbeat()
      void stopSession(s.session_id)
    }
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
  subject_artist_id: number | null
  event_name: string
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
  subject_artist_id: null,
  event_name: '',
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
const libraryDirs = ref<string[]>([])
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

async function loadLibraryDirs() {
  try {
    libraryDirs.value = await libraryApi.directories()
  } catch {
    libraryDirs.value = []
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
    subject_artist_id: v.subject_artist_id ?? null,
    event_name: v.event_name ?? '',
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
  void loadLibraryDirs()
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
      subject_artist_id: f.subject_artist_id || null,
      event_name: strOrNull(f.event_name),
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
type MetaPart = { text: string; to?: string }
type MetaRow = {
  label: string
  value?: string
  mono?: boolean
  parts?: MetaPart[]
  // 移动端默认折叠的次要信息（视频格式/码率/文件名）
  fold?: boolean
}

const metaFields = computed<MetaRow[]>(() => {
  const v = video.value
  if (!v) return []
  const rows: MetaRow[] = []
  const cast: MetaPart[] = []
  for (const a of v.artists || []) {
    cast.push({ text: a.chinese_name || a.name, to: a.uid ? artistPath(a.uid) : undefined })
  }
  for (const g of v.groups || []) {
    cast.push({ text: g.chinese_name || g.name, to: g.uid ? groupPath(g.uid) : undefined })
  }
  if (cast.length) rows.push({ label: '艺人/组合', parts: cast })
  const types = v.video_types && v.video_types.length ? v.video_types : v.video_type ? [v.video_type] : []
  rows.push({ label: '视频类型', value: types.map((t) => VIDEO_TYPE_LABEL[t] || t).join(' / ') || '--' })
  if (types.includes('CoverStage') && v.cover_from && v.cover_from.length) {
    rows.push({ label: '翻唱自', value: v.cover_from.join(' / ') })
  }
  const songParts: MetaPart[] = (v.songs || [])
    .map((s) => ({ text: s.chinese_name || s.name, to: s.uid ? songPath(s.uid) : undefined }))
    .filter((p) => p.text)
  if (songParts.length) {
    rows.push({ label: '歌曲', parts: songParts })
  } else {
    const song = v.song_chinese_name || v.song_name
    if (song) rows.push({ label: '歌曲', value: song })
  }
  if (v.performance_date) rows.push({ label: '演出日期', value: formatDate(v.performance_date) })
  if (v.release_date) rows.push({ label: '发布日期', value: formatDate(v.release_date) })
  const up: MetaPart[] = []
  if (v.original_uploader) {
    up.push({ text: v.original_uploader, to: `/uploaders/${encodeURIComponent(v.original_uploader)}` })
  }
  if (v.published_date) up.push({ text: formatDate(v.published_date) })
  if (up.length) rows.push({ label: '博主/日期', parts: up })
  if (v.event_name) rows.push({ label: '活动名称', value: v.event_name })
  const fmt: string[] = []
  const res = formatResolution(v.width, v.height)
  if (res !== '--') fmt.push(res)
  if (v.video_codec) fmt.push(v.video_codec)
  if (v.audio_codec) fmt.push(v.audio_codec)
  if (v.file_size) fmt.push(formatFileSize(v.file_size))
  if (fmt.length) rows.push({ label: '视频格式', value: fmt.join(' · '), fold: true })
  if (v.bitrate) rows.push({ label: '码率', value: formatBitrate(v.bitrate), mono: true, fold: true })
  if (v.file_name) rows.push({ label: '文件名', value: v.file_name, mono: true, fold: true })
  return rows
})

const tags = computed(() => {
  const v = video.value
  if (!v) return []
  const list: string[] = []
  if (v.video_type) list.push(VIDEO_TYPE_LABEL[v.video_type] || v.video_type)
  for (const s of v.songs || []) list.push(s.chinese_name || s.name)
  for (const a of v.artists || []) list.push(a.chinese_name || a.name)
  for (const g of v.groups || []) list.push(g.chinese_name || g.name)
  for (const al of v.albums || []) list.push(al.chinese_name || al.name)
  for (const alias of v.aliases || []) list.push(alias)
  return Array.from(new Set(list)).slice(0, 12)
})

// 移动端折叠：视频格式/码率/文件名与关联标签默认收起，点「展开详细信息」显示
const detailsExpanded = ref(false)
const visibleMetaFields = computed(() =>
  phoneViewport.value && !detailsExpanded.value
    ? metaFields.value.filter((r) => !r.fold)
    : metaFields.value,
)
const hasFoldableMeta = computed(
  () => metaFields.value.some((r) => r.fold) || tags.value.length > 0,
)

const metaDate = computed(() => {
  const v = video.value
  if (!v) return ''
  const d = primaryDateRaw.value
  return d ? formatDate(d) : ''
})

const primaryDateRaw = computed(() => {
  const v = video.value
  if (!v) return ''
  return v.release_date || v.published_date || v.performance_date || ''
})

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
  return list
})

function relatedAvatarUrl(item: { id: number | null; kind: 'artist' | 'group' }) {
  if (item.id === null) return ''
  return item.kind === 'group' ? `/api/groups/${item.id}/avatar` : `/api/artists/${item.id}/avatar`
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
  <div class="mv-page">
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
      <div v-else-if="video" class="main-grid">
        <div class="main-col">
          <!-- Hero Cover -->
          <div
            class="hero-cover"
            :class="{ 'hero-cover--portrait': portraitBox }"
            @mouseenter="onHeroEnter"
            @mouseleave="onHeroLeave"
            @click="onHeroTap"
          >
            <template v-if="hasSourceFile">
              <!-- 预播态：纯封面（不挂播放器）；唤出（armed）或播放后由播放器盖在封面之上 -->
              <img
                v-if="poster"
                v-show="!playerVisible"
                :src="poster"
                :alt="video.name"
                class="hero-img"
                @error="onThumbError"
              />
              <div v-if="!poster && !playerVisible" class="hero-placeholder">
                <span class="hero-placeholder-play"><PlayArrowOutlined :size="26" /></span>
              </div>
              <div v-if="playerVisible" class="hero-player">
                <VideoPlayer
                  :sources="sources"
                  :poster="poster"
                  :active-label="playing ? activeLabel : ''"
                  :qualities="playing ? qualities : preQualities"
                  :active-quality="activeQuality"
                  :play-mode-label="playing && session ? playModeLabel(session.play_mode) : ''"
                  :play-mode-reason="playing && session ? session.decision_reason ?? '' : ''"
                  :restore-time="playing && session ? session.play_mode === 'direct_play' : false"
                  :buffering="playerBuffering"
                  :pending="!playing"
                  :duration="session?.source_duration || video.duration || 0"
                  :start-offset="session?.start_offset || 0"
                  :native-seek="session?.play_mode === 'direct_play'"
                  :auto-play="true"
                  :seek-into="seekInto"
                  :available-until="availableUntil"
                  @state="onPlayerState"
                  @timeupdate="onTimeUpdate"
                  @seeked="onSeeked"
                  @quality-select="selectQuality"
                  @play-request="startPlay"
                  @play="onPlayerPlay"
                  @pause="onPlayerPause"
                  @ended="onPlayerEnded"
                />
                <!-- 预播热区：唤出（armed）后出现，底部 58px 留给控制栏（画质/PlayToggle 仍可点）。
                     PC 单击即播；移动端双击即播。一旦播放过不再出现，seek 跳转只显示「正在跳转…」 -->
                <button
                  v-if="playerArmed && !playing && !hasEverPlayed"
                  class="hero-overlay"
                  aria-label="播放"
                  @click.stop="onHeroOverlayTap"
                ></button>
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
              </div>
            </template>
            <template v-else>
              <img
                v-if="poster"
                :src="poster"
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
          </div>

          <!-- 转码错误提示（画质选择已移入播放器控制栏齿轮菜单） -->
          <div v-if="playing && playerError" class="hero-error">{{ playerError }}</div>

          <!-- Title -->
          <h1 class="title">{{ video.name }}</h1>

          <!-- Meta Row -->
          <div class="meta-row">
            <span v-if="metaDate" class="meta-sep" />
            <span v-if="metaDate" class="meta-text">{{ metaDate }}</span>
            <span v-if="video.duration" class="meta-sep" />
            <span v-if="video.duration" class="meta-text">{{ formatDuration(video.duration) }}</span>
            <template v-if="video.source_platform">
              <span class="meta-sep" />
              <a
                v-if="video.source_url"
                class="meta-text meta-link"
                :href="video.source_url"
                target="_blank"
                rel="noopener noreferrer"
              >
                {{ video.source_platform }}
              </a>
              <span v-else class="meta-text">{{ video.source_platform }}</span>
            </template>
            <span v-if="video.published_date && video.published_date !== primaryDateRaw" class="meta-sep" />
            <span v-if="video.published_date && video.published_date !== primaryDateRaw" class="meta-text mono">
              {{ formatDate(video.published_date) }}
            </span>
            <div class="meta-actions">
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
              <button class="btn-ghost btn-ghost--icon" :disabled="!video" title="编辑" @click="openEdit">
                <EditOutlined />
              </button>
            </div>
          </div>

          <!-- Description -->
          <div v-if="video.chinese_description || video.description" class="desc">
            <div
              class="desc-cn"
              :class="{ 'desc-cn--clickable': hasOriginalDesc }"
              @click="onDescClick"
            >
              {{ formatDesc(video.chinese_description || video.description) }}
              <span
                v-if="hasOriginalDesc"
                class="desc-cn-arrow"
                :class="{ 'desc-cn-arrow--open': showOriginal }"
              >
                ▾
              </span>
            </div>
            <div v-if="showOriginal && hasOriginalDesc" class="desc-original">
              {{ formatDesc(video.description) }}
            </div>
          </div>

          <!-- Meta fields -->
          <section class="section">
            <div class="meta-grid">
              <template v-for="row in visibleMetaFields" :key="row.label">
                <div class="meta-label">{{ row.label }}</div>
                <div v-if="row.parts" class="meta-value meta-value--parts">
                  <template v-for="(part, pi) in row.parts" :key="pi">
                    <router-link v-if="part.to" :to="part.to" class="meta-value--link">
                      {{ part.text }}
                    </router-link>
                    <span v-else>{{ part.text }}</span>
                    <span v-if="pi < row.parts.length - 1" class="meta-part-sep">·</span>
                  </template>
                </div>
                <div v-else class="meta-value" :class="{ mono: row.mono }">{{ row.value }}</div>
              </template>
            </div>
            <button
              v-if="phoneViewport && hasFoldableMeta"
              type="button"
              class="meta-toggle"
              @click="detailsExpanded = !detailsExpanded"
            >
              {{ detailsExpanded ? '收起详细信息' : '展开详细信息' }}
              <span class="meta-toggle-arrow" :class="{ 'meta-toggle-arrow--open': detailsExpanded }">▾</span>
            </button>
          </section>

          <!-- Tags -->
          <section v-if="tags.length && (detailsExpanded || !phoneViewport)" class="section">
            <div class="section-title">关联标签</div>
            <div class="tags">
              <span
                v-for="tag in tags"
                :key="tag"
                class="badge badge--neutral"
              >
                {{ tag }}
              </span>
            </div>
          </section>
        </div>

        <!-- Sidebar -->
        <aside class="side-col">
          <div v-if="related.length" class="side-block">
            <div class="side-title">相关作品</div>
            <div class="related-list">
              <button
                v-for="item in related"
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
          </div>

          <div v-if="relatedArtists.length" class="side-block">
            <div class="side-title">相关艺人</div>
            <div class="artist-list">
              <button
                v-for="a in relatedArtists"
                :key="a.key"
                class="artist-capsule"
                :class="{ 'artist-capsule--link': !!a.uid }"
                :disabled="!a.uid"
                @click="a.uid && goRelatedEntity(a)"
              >
                <div class="artist-avatar" :style="avatarStyle(a.index)">
                  <img
                    v-if="a.avatar"
                    class="artist-avatar-img"
                    :src="relatedAvatarUrl(a)"
                    alt=""
                    loading="lazy"
                  />
                  <template v-else>{{ initialOf(a.name) }}</template>
                </div>
                <div class="artist-info">
                  <div class="artist-name">{{ a.name }}</div>
                  <div class="artist-sub">{{ a.sub }}</div>
                </div>
              </button>
            </div>
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

    <!-- 选择已有入库目录 -->
    <n-modal
      v-model:show="destDirOpen"
      preset="card"
      title="选择已有入库目录"
      :style="{ width: 'min(480px, calc(100vw - 24px))' }"
    >
      <div class="dest-dir-list">
        <button v-for="d in libraryDirs" :key="d" class="dest-dir-item" type="button" @click="applyDestDir(d)">
          <FolderOutlined :size="14" />
          <span>{{ d }}</span>
        </button>
        <div v-if="!libraryDirs.length" class="dest-dir-empty">
          正式库中暂无子目录，可关闭弹窗后手动输入相对路径
        </div>
      </div>
      <template #footer>
        <n-space justify="end">
          <n-button @click="destDirOpen = false">关闭</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.mv-page {
  min-height: 100vh;
  background: var(--sa-bg);
  color: var(--sa-text-primary);
  -webkit-font-smoothing: antialiased;
}
.mv-container {
  max-width: 1280px;
  margin: 0 auto;
  padding: 24px 32px 48px;
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
  margin-left: auto;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  position: relative;
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
    grid-template-columns: 1fr 320px;
  }
}

/* ===== Hero ===== */
.hero-cover {
  position: relative;
  aspect-ratio: 16 / 9;
  border-radius: 12px;
  overflow: hidden;
  background: var(--sa-subtle);
  margin-bottom: 24px;
}
/* 手机竖屏片（B 站式）：盒子是一个「铺满全宽 + 视口高度 58%」的黑色画框，
 * 竖屏画面在里面 contain 居中 → 左右自然留出黑边（9:16 在 390×844 上实测各约 57px），
 * 页面占用从 ~82%（636px）降到 58%（490px）。
 * - 尺寸只由「是不是竖屏片」决定，**不随播放/暂停/换源变化** → 不再整页跳变；
 * - 用**确定高度**而不是 aspect-ratio：基础规则是 16/9，竖屏时若继续用 aspect-ratio，
 *   一旦 max-height 生效，Chrome 会把高度上限「反向传递」压窄宽度 —— 实测盒子只剩
 *   275px 宽、右侧漏出页面背景，黑边就跑到播放器外面去了。必须 aspect-ratio: auto
 *   让宽度回到块级布局（auto 宽 + 移动端 .hero-cover 的负外边距 = 铺满全宽 390px）；
 * - 高度用 svh（小视口高度）而非 dvh：滚动时地址栏收缩不改变 svh，播放器高度不抖动。 */
.hero-cover--portrait {
  aspect-ratio: auto;
  height: 58vh;   /* 老浏览器兜底（不认识 svh 时用这条） */
  height: 58svh;  /* 动态视口安全单位：不随地址栏伸缩变化 */
  background: #000;
}
/* 封面同样 contain：竖屏缩略图不被裁切，黑边从封面阶段就在，避免开播瞬间画面"变胖" */
.hero-cover--portrait .hero-img {
  object-fit: contain;
}
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
.hero-overlay {
  position: absolute;
  /* 底部留出自定义进度条 + 原生控制栏区域，避免遮挡 */
  inset: 0 0 58px 0;
  background: transparent;
  border: none;
  cursor: pointer;
  /* 透明全屏点击热区：待播时点画面任意处即开始播放。
   * 播放锚点回归控制栏 PlayToggle，不再叠加页面级浮动按钮，避免视觉语言冲突 */
}

.hero-player {
  position: relative;
  width: 100%;
  height: 100%;
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
  margin: -12px 0 16px;
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

/* ===== Title ===== */
.title {
  font-size: 26px;
  font-weight: 600;
  letter-spacing: -0.02em;
  line-height: 1.3;
  color: var(--sa-text-primary);
  margin: 0 0 12px;
}
@media (max-width: 768px) {
  .title {
    font-size: 20px;
  }
}

/* ===== Meta Row ===== */
.meta-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 20px;
}
.meta-sep {
  width: 1px;
  height: 14px;
  background: var(--sa-border);
}
.meta-text {
  font-size: 14px;
  color: var(--sa-text-secondary);
}
.meta-link {
  color: var(--sa-accent);
  text-decoration: none;
  transition: color 0.15s;
}
.meta-link:hover {
  color: var(--sa-accent-hover);
  text-decoration: underline;
}
.meta-text.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
}

/* ===== Badge ===== */
.badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 10px;
  font-size: 12px;
  font-weight: 500;
  border-radius: 9999px;
  line-height: 1.5;
}
.badge--neutral {
  color: var(--sa-text-secondary);
  background: var(--sa-subtle);
}

/* ===== Description ===== */
.desc {
  font-size: 15px;
  line-height: 1.7;
  color: var(--sa-text-secondary);
  margin-bottom: 32px;
  white-space: pre-wrap;
}
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

/* ===== Section ===== */
.section {
  margin-bottom: 40px;
}
.section-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--sa-text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 16px;
}

/* ===== Meta grid ===== */
.meta-grid {
  display: grid;
  grid-template-columns: 110px 1fr;
  gap: 12px 16px;
  font-size: 14px;
}
.meta-label {
  color: var(--sa-text-tertiary);
  font-size: 12px;
  padding-top: 1px;
}
.meta-value {
  color: var(--sa-text-primary);
}
.meta-value.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
}
.meta-value--link {
  color: var(--sa-accent);
  cursor: pointer;
  text-decoration: none;
  transition: opacity 0.15s;
}
.meta-value--link:hover {
  opacity: 0.8;
  text-decoration: underline;
}
.meta-value--parts {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  row-gap: 2px;
}
.meta-part-sep {
  margin: 0 6px;
  color: var(--sa-text-tertiary);
}
.meta-toggle {
  margin-top: 16px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  font-size: 13px;
  color: var(--sa-text-secondary);
  background: var(--sa-subtle);
  border: none;
  border-radius: 9999px;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}
.meta-toggle:active {
  opacity: 0.75;
}
.meta-toggle-arrow {
  font-size: 11px;
  display: inline-block;
  transition: transform 0.2s ease;
}
.meta-toggle-arrow--open {
  transform: rotate(180deg);
}

/* ===== Tags ===== */
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

/* ===== Sidebar ===== */
.side-col {
  display: flex;
  flex-direction: column;
  gap: 24px;
}
.side-block {
  min-width: 0;
}
.side-title {
  font-size: 12px;
  font-weight: 500;
  color: var(--sa-text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 12px;
}
.related-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.related-item {
  display: flex;
  gap: 12px;
  padding: 8px;
  border-radius: 8px;
  text-align: left;
  background: transparent;
  border: none;
  cursor: pointer;
  transition: background 0.2s;
}
.related-item:hover {
  background: var(--sa-subtle);
}
.related-thumb {
  width: 80px;
  height: 50px;
  border-radius: 6px;
  overflow: hidden;
  flex-shrink: 0;
  background: var(--sa-subtle);
}
.related-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
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
  border-radius: 8px;
  text-align: left;
  width: 100%;
  font: inherit;
}
.artist-capsule:disabled {
  cursor: default;
}
.artist-capsule--link {
  cursor: pointer;
  transition: background 0.2s;
}
.artist-capsule--link:hover {
  background: var(--sa-hover);
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
.relocate-hint {
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--sa-text-tertiary);
}
.dest-dir-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 320px;
  overflow-y: auto;
}
.dest-dir-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--sa-subtle);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 8px;
  color: var(--sa-text-primary);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.dest-dir-item:hover {
  border-color: var(--sa-accent-border);
  color: var(--sa-accent);
}
.dest-dir-empty {
  padding: 16px;
  text-align: center;
  font-size: 13px;
  color: var(--sa-text-tertiary);
}
</style>
