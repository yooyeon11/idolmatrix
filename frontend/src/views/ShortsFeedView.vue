<script setup lang="ts">
/**
 * 随机刷短视频（抖音式沉浸 feed）。
 *
 * 结构：全屏黑底 + 垂直 scroll-snap 容器，一条视频一屏。移动端走原生触摸
 * 滚动（跟手 + 惯性 + scroll-snap-stop 防一次甩多条）；桌面端滚轮 / 方向键
 * 逐条切换，空格暂停，Esc 退出。
 *
 * 播放复用 playback session：active 条建会话，下一条仅当 direct_play
 * （渐进直连）时预挂 src 预加载 —— HLS 会话挂 src 即惰性启动 FFmpeg，
 * 提前挂会占转码并发额度，推迟到激活时再挂。播放出错按「重建 720p 转码
 * 会话」兜底一次。单条循环播放，划走 / 循环看完都照常上报 /stats/watch。
 * 进 feed 的一次点击即用户手势，默认有声起播；被浏览器拦截则降级静音并提示。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import Hls from 'hls.js'
import { musicVideosApi } from '@/api/musicVideos'
import { playbackApi } from '@/api/playback'
import { dashboardApi } from '@/api/dashboard'
import type { MusicVideo, PlaybackSession } from '@/types/models'
import { formatDuration } from '@/utils/format'
import { videoPath } from '@/utils/routes'
import { IMG_W_FULL } from '@/utils/imageSizes'
import {
  ChevronLeftOutlined,
  LoadingOutlined,
  OpenInNewOutlined,
  PlayArrowOutlined,
  ShuffleOutlined,
} from '@/components/icons'

const router = useRouter()

const BATCH_SIZE = 10
// 距队尾不足该条数时预取下一批
const PREFETCH_AHEAD = 3
const WHEEL_LOCK_MS = 450
const WATCH_PING_MS = 15_000
// 换源挂 src 后这段时间内的 pause 不当用户暂停（只是换源中间态）
const ATTACH_PAUSE_GRACE_MS = 800
// 会话保活：与播放页同节奏的心跳，避免长循环播放时会话过期 404
const HEARTBEAT_MS = 25_000

const queue = ref<MusicVideo[]>([])
const idx = ref(0)
const loading = ref(true)
const loadError = ref('')
const exhausted = ref(false)
const fetching = ref(false)
const muted = ref(false)
// 自动播放被浏览器拦截、降级静音后显示「点击开启声音」
const mutedFallback = ref(false)
const paused = ref(false)
const activating = ref(false)
const progress = ref(0)
const slideError = ref('')

const scroller = ref<HTMLElement | null>(null)

const current = computed(() => queue.value[idx.value] ?? null)
// 视图停在尾卡（exhausted 时的最后一张"幻灯片"）：此时 idx 仍是最后一条视频
const atEnd = ref(false)

// ===== 每条视频的运行时状态（非响应式，key 均为 video id）=====
const videoEls = new Map<number, HTMLVideoElement>()
const sessions = new Map<number, PlaybackSession>()
const hlsInstances = new Map<number, Hls>()
const sessionCreating = new Set<number>()
const attached = new Set<number>()
const attachedAt = new Map<number, number>()
// 播放出错后已按 720p 转码兜底过的条：不再二次兜底
const fallbackTried = new Set<number>()
// 本轮已完整看过一遍的条：循环每看完一次补一条 ended 上报
const completedSent = new Set<number>()
// 真正出过画的条：划走时才需要补最终上报
const startedIds = new Set<number>()
// 本会话刷过的全部 id：feed 接口的 exclude_ids
const seenIds = new Set<number>()

// ===== 数据加载 =====
async function fetchBatch(): Promise<MusicVideo[]> {
  const items = await musicVideosApi.feed({
    count: BATCH_SIZE,
    exclude_ids: seenIds.size ? [...seenIds].join(',') : undefined,
  })
  for (const v of items) seenIds.add(v.id)
  return items
}

async function initialLoad() {
  loading.value = true
  loadError.value = ''
  slideError.value = ''
  try {
    const items = await fetchBatch()
    queue.value = items
    exhausted.value = items.length === 0
    idx.value = 0
    await nextTick()
    if (items.length) void activateCurrent()
  } catch (e) {
    loadError.value = (e as Error).message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function prefetchBatch() {
  if (fetching.value || exhausted.value) return
  if (idx.value < queue.value.length - PREFETCH_AHEAD) return
  fetching.value = true
  try {
    const items = await fetchBatch()
    if (items.length) queue.value.push(...items)
    else exhausted.value = true
  } catch {
    /* 静默：下次激活再试，不影响当前播放 */
  } finally {
    fetching.value = false
  }
}

async function reshuffle() {
  for (const [, s] of sessions) void playbackApi.stop(s.session_id, { force: true }).catch(() => {})
  sessions.clear()
  for (const [, h] of hlsInstances) {
    try {
      h.destroy()
    } catch {
      /* 忽略 */
    }
  }
  hlsInstances.clear()
  attached.clear()
  attachedAt.clear()
  fallbackTried.clear()
  completedSent.clear()
  startedIds.clear()
  seenIds.clear()
  queue.value = []
  exhausted.value = false
  atEnd.value = false
  scroller.value?.scrollTo({ top: 0 })
  await initialLoad()
}

// ===== 会话与播放 =====
/** 上报客户端能力（与 VideoPlayView.clientCapabilities 同语义的廉价版）：
 * 渐进直连（Direct Play）用 canPlayType 实测（Chrome 能原生 demux MKV/WebM，
 * 直连原文件是最快路径）；MSE 口径用 isTypeSupported 实测（VP9/AV1 走
 * fMP4 HLS remux 直出，不落转码）。Safari/iOS 原生管线保守剔除。 */
function clientCaps(): Record<string, unknown> {
  const ua = navigator.userAgent || ''
  const isIOS = /iPhone|iPad|iPod/i.test(ua)
  const isAppleSafari = isIOS || (/Safari/i.test(ua) && !/Chrome|CriOS|Chromium/i.test(ua))
  const probe = document.createElement('video')
  const canProbably = (mime: string) => (probe.canPlayType?.(mime) || '') === 'probably'
  const canMaybe = (mime: string) => {
    const r = probe.canPlayType?.(mime) || ''
    return r === 'probably' || r === 'maybe'
  }
  const mseOk = (mime: string) => {
    try {
      return typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported(mime)
    } catch {
      return false
    }
  }
  // MSE 口径（Direct Stream / Transcode 决策）
  const videoCodecs: string[] = []
  if (mseOk('video/mp4; codecs="avc1.42E01E"') || canProbably('video/mp4; codecs="avc1.42E01E"')) videoCodecs.push('h264')
  if (mseOk('video/mp4; codecs="av01.0.08M.08"') || mseOk('video/webm; codecs="av01.0.08M.08"')) videoCodecs.push('av1')
  if (mseOk('video/mp4; codecs="vp09.00.51.08"') || mseOk('video/webm; codecs="vp9"')) videoCodecs.push('vp9')
  const audioCodecs: string[] = []
  if (mseOk('audio/mp4; codecs="mp4a.40.2"')) audioCodecs.push('aac')
  if (mseOk('audio/mp4; codecs="opus"') || mseOk('audio/webm; codecs="opus"')) audioCodecs.push('opus')
  if (mseOk('audio/mp4; codecs="mp3"') || canMaybe('audio/mpeg')) audioCodecs.push('mp3')
  if (mseOk('audio/mp4; codecs="flac"')) audioCodecs.push('flac')
  if (mseOk('audio/mp4; codecs="vorbis"')) audioCodecs.push('vorbis')
  // 渐进口径（Direct Play：<video> 直连原文件）
  const progressiveVideo: string[] = []
  if (canProbably('video/mp4; codecs="avc1.42E01E"')) progressiveVideo.push('h264')
  if (!isAppleSafari && (canMaybe('video/webm; codecs="av01.0.05M.08"') || canMaybe('video/webm; codecs="av01.0.08M.08"'))) progressiveVideo.push('av1')
  if (!isAppleSafari && canMaybe('video/webm; codecs="vp9"')) progressiveVideo.push('vp9')
  const progressiveAudio: string[] = []
  if (canMaybe('audio/mp4; codecs="mp4a.40.2"')) progressiveAudio.push('aac')
  if (!isAppleSafari && canMaybe('audio/webm; codecs="opus"')) progressiveAudio.push('opus')
  if (canMaybe('audio/mpeg')) progressiveAudio.push('mp3')
  if (!isAppleSafari && canMaybe('audio/webm; codecs="vorbis"')) progressiveAudio.push('vorbis')
  // 容器：渐进 demuxer 实测报 mkv 资格（桌面 Chromium 内置 Matroska demuxer）
  const containers = isAppleSafari ? ['mp4', 'm4v', 'mov'] : ['mp4', 'm4v', 'mov', 'webm']
  if (
    !isAppleSafari &&
    progressiveVideo.length > 0 &&
    (canMaybe('video/x-matroska; codecs="vp9"') || canMaybe('video/x-matroska; codecs="avc1.42E01E"'))
  ) {
    containers.push('mkv')
  }
  if (isIOS) {
    // iOS 原生管线：VP9/AV1 等保守剔除，交服务端转码兜底
    for (const drop of ['vp9', 'av1', 'opus', 'vorbis', 'flac']) {
      let i = videoCodecs.indexOf(drop)
      if (i >= 0) videoCodecs.splice(i, 1)
      i = progressiveVideo.indexOf(drop)
      if (i >= 0) progressiveVideo.splice(i, 1)
      i = audioCodecs.indexOf(drop)
      if (i >= 0) audioCodecs.splice(i, 1)
      i = progressiveAudio.indexOf(drop)
      if (i >= 0) progressiveAudio.splice(i, 1)
    }
  }
  return {
    supported_video_codecs: videoCodecs,
    supported_audio_codecs: audioCodecs,
    progressive_video_codecs: progressiveVideo,
    progressive_audio_codecs: progressiveAudio,
    supported_containers: containers,
    // Apple 走 <video> 原生 HLS，其余走 hls.js，都能播服务端交付的 HLS
    supports_hls: true,
    supports_fmp4: true,
    user_agent: ua,
  }
}

function isBusyError(e: unknown): boolean {
  const err = e as Error & { status?: number }
  if (err?.status === 429 || err?.status === 503) return true
  return /转码并发上限|转码繁忙/.test(err?.message || '')
}

async function createSession(v: MusicVideo, quality = 'original'): Promise<PlaybackSession> {
  const payload = { music_video_id: v.id, requested_quality: quality, client: clientCaps() }
  try {
    return await playbackApi.create(payload)
  } catch (e) {
    if (!isBusyError(e)) throw e
    await new Promise((r) => window.setTimeout(r, 1500))
    return playbackApi.create(payload)
  }
}

function destroyHls(id: number) {
  const h = hlsInstances.get(id)
  if (h) {
    try {
      h.destroy()
    } catch {
      /* 忽略 */
    }
    hlsInstances.delete(id)
  }
}

function attachSource(el: HTMLVideoElement, v: MusicVideo, s: PlaybackSession) {
  destroyHls(v.id)
  attachedAt.set(v.id, Date.now())
  attached.add(v.id)
  if (s.manifest_url) {
    if (Hls.isSupported()) {
      const hls = new Hls({ enableWorker: true })
      // 致命错误（如清单 CODECS 与 MSE 不兼容）→ 走 720p 转码兜底，与 <video> error 同路
      hls.on(Hls.Events.ERROR, (_evt, data) => {
        if (!data.fatal) return
        if (v.id !== current.value?.id) return
        destroyHls(v.id)
        onSlideError(v)
      })
      hls.loadSource(s.manifest_url)
      hls.attachMedia(el)
      hlsInstances.set(v.id, hls)
      // ⚠ 不能在 attachMedia 之后 el.load()：load() 会重置媒体元素、拆掉 MSE 挂载，
      // hls.js 便永远等不到缓冲（实测 readyState 恒 0）。重置只属于原生 src 分支。
    } else {
      // 无 MSE（iOS Safari 等）：原生 HLS
      el.src = s.manifest_url
      try {
        el.load()
      } catch {
        /* 忽略 */
      }
    }
  } else if (s.stream_url) {
    el.src = s.stream_url
    try {
      el.load()
    } catch {
      /* 忽略 */
    }
  }
}

function detachVideo(id: number) {
  destroyHls(id)
  attached.delete(id)
  const el = videoEls.get(id)
  if (el) {
    try {
      el.pause()
      el.removeAttribute('src')
      el.load()
    } catch {
      /* 忽略 */
    }
  }
}

function stopSessionQuiet(id: number) {
  const s = sessions.get(id)
  if (!s) return
  sessions.delete(id)
  void playbackApi.stop(s.session_id, { force: true }).catch(() => {})
}

// active 条激活（idx 变化 / 首载 / 兜底后）：统一入口，seq 丢弃过期回调
let activeSeq = 0

async function activateCurrent() {
  const seq = ++activeSeq
  const i = idx.value
  const v = queue.value[i]
  if (!v) return
  paused.value = false
  progress.value = 0
  slideError.value = ''

  // 停掉 current 之外的全部会话并拆源（预载的下一条保留会话，下面重建）
  for (const id of [...sessions.keys()]) {
    if (id !== v.id) {
      stopSessionQuiet(id)
      detachVideo(id)
    }
  }
  for (const [id, el] of videoEls) {
    if (id !== v.id) {
      try {
        el.pause()
      } catch {
        /* 忽略 */
      }
    }
  }

  void prefetchBatch()

  if (!sessions.has(v.id) && !sessionCreating.has(v.id)) {
    sessionCreating.add(v.id)
    activating.value = true
    try {
      const s = await createSession(v)
      if (seq !== activeSeq) {
        void playbackApi.stop(s.session_id, { force: true }).catch(() => {})
        return
      }
      sessions.set(v.id, s)
    } catch (e) {
      if (seq === activeSeq) slideError.value = (e as Error).message || '会话创建失败'
      return
    } finally {
      sessionCreating.delete(v.id)
      if (seq === activeSeq) activating.value = false
    }
  }
  const s = sessions.get(v.id)
  const el = videoEls.get(v.id)
  if (!s || !el || seq !== activeSeq) return
  if (!attached.has(v.id)) attachSource(el, v, s)
  void playCurrent()
  preloadNext()
}

async function playCurrent() {
  const v = current.value
  const el = v ? videoEls.get(v.id) : null
  if (!v || !el) return
  el.muted = muted.value
  try {
    await el.play()
    startedIds.add(v.id)
  } catch {
    // 自动播放被拦：降级静音重播，提示点按开声
    if (!muted.value) {
      muted.value = true
      mutedFallback.value = true
      el.muted = true
      try {
        await el.play()
        startedIds.add(v.id)
      } catch {
        paused.value = true
      }
    } else {
      paused.value = true
    }
  }
}

/** 预载下一条：渐进直连流直接挂 src 静默缓冲；HLS 会话（转码/remux）挂 src
 * 即惰性启动 FFmpeg，提前挂会占并发额度，留给激活时。 */
function preloadNext() {
  const nv = queue.value[idx.value + 1]
  if (!nv || sessionCreating.has(nv.id)) return
  void (async () => {
    sessionCreating.add(nv.id)
    try {
      let s = sessions.get(nv.id)
      if (!s) {
        s = await createSession(nv)
        const stillNext = queue.value[idx.value + 1]?.id === nv.id
        if (!stillNext) {
          void playbackApi.stop(s.session_id, { force: true }).catch(() => {})
          return
        }
        sessions.set(nv.id, s)
      }
      if (s.stream_url && !s.manifest_url) {
        const el = videoEls.get(nv.id)
        if (el && !attached.has(nv.id)) {
          el.preload = 'auto'
          attachSource(el, nv, s)
        }
      }
    } catch {
      /* 预载失败不致命：激活时会重建 */
    } finally {
      sessionCreating.delete(nv.id)
    }
  })()
}

// ===== 滑动 / 滚轮 / 键盘 =====
let scrollTimer: number | null = null

function onScroll() {
  if (scrollTimer !== null) window.clearTimeout(scrollTimer)
  scrollTimer = window.setTimeout(() => {
    scrollTimer = null
    const el = scroller.value
    if (!el) return
    const h = el.clientHeight || 1
    const i = Math.round(el.scrollTop / h)
    if (i >= queue.value.length) {
      // 划到尾卡：当前条已出屏，暂停（回来点按继续）
      atEnd.value = true
      const v = current.value
      const vel = v ? videoEls.get(v.id) : null
      if (vel) vel.pause()
      return
    }
    atEnd.value = false
    if (i < 0 || i === idx.value) return
    goTo(i)
  }, 80)
}

function goTo(i: number) {
  if (i < 0 || i >= queue.value.length || i === idx.value) return
  atEnd.value = false
  const old = idx.value
  idx.value = i
  // 划走旧条：补最终观看上报
  const oldV = queue.value[old]
  if (oldV && startedIds.has(oldV.id)) pingWatch(oldV, { playing: false })
  stopWatchLoop()
  stopHeartbeat()
  void activateCurrent()
}

function goToIdx(i: number) {
  const el = scroller.value
  if (!el) return
  // 刷穿后允许滚到尾卡（index = queue.length），其余情况止步最后一条视频
  const max = exhausted.value ? queue.value.length : queue.value.length - 1
  if (i < 0 || i > max) return
  el.scrollTo({ top: i * el.clientHeight, behavior: 'smooth' })
}

let wheelLock = 0

/** 桌面滚轮 / 方向键统一走一步；尾卡上反向一步 = 回到最后一条视频 */
function step(dir: 1 | -1) {
  if (dir < 0 && atEnd.value) {
    atEnd.value = false
    goToIdx(queue.value.length - 1)
    return
  }
  goToIdx(idx.value + dir)
}

function onWheel(e: WheelEvent) {
  // 桌面滚轮：一次滚动一条（触摸滚动不产生 wheel，走原生 snap）
  e.preventDefault()
  const now = Date.now()
  if (now < wheelLock || Math.abs(e.deltaY) < 12) return
  wheelLock = now + WHEEL_LOCK_MS
  step(e.deltaY > 0 ? 1 : -1)
}

function onKey(e: KeyboardEvent) {
  const t = e.target as HTMLElement | null
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
  if (e.key === 'ArrowDown' || e.key === 'PageDown') {
    e.preventDefault()
    step(1)
  } else if (e.key === 'ArrowUp' || e.key === 'PageUp') {
    e.preventDefault()
    step(-1)
  } else if (e.key === ' ') {
    e.preventDefault()
    void togglePause()
  } else if (e.key === 'Escape') {
    exit()
  }
}

// ===== 播放事件（模板绑定，只认当前条）=====
function bindVideo(id: number, el: unknown) {
  const v = el as HTMLVideoElement | null
  if (v) videoEls.set(id, v)
  else videoEls.delete(id)
}

function onSlidePlay(v: MusicVideo) {
  if (v.id !== current.value?.id) return
  paused.value = false
  startWatchLoop(v)
  startHeartbeat()
}

function onSlidePause(v: MusicVideo) {
  if (v.id !== current.value?.id) return
  if (Date.now() - (attachedAt.get(v.id) || 0) < ATTACH_PAUSE_GRACE_MS) return
  paused.value = true
  if (startedIds.has(v.id)) pingWatch(v, { playing: false })
  stopWatchLoop()
}

function onSlideTime(v: MusicVideo, e: Event) {
  if (v.id !== current.value?.id) return
  const el = e.target as HTMLVideoElement
  const d = Number(el.duration) || v.duration || 0
  progress.value = d > 0 ? Math.min(1, (el.currentTime || 0) / d) : 0
  // 循环看完一遍：补一条 ended（服务端据此标 completed）
  if (d > 1 && el.currentTime >= d - 0.75) {
    if (!completedSent.has(v.id)) {
      completedSent.add(v.id)
      pingWatch(v, { playing: true, ended: true, position: d, duration: d })
    }
  } else if (el.currentTime < 1) {
    completedSent.delete(v.id)
  }
}

function onSlideError(v: MusicVideo) {
  if (v.id !== current.value?.id) return
  if (fallbackTried.has(v.id)) {
    slideError.value = '播放出错，可在播放页尝试其他画质'
    return
  }
  fallbackTried.add(v.id)
  void (async () => {
    stopSessionQuiet(v.id)
    detachVideo(v.id)
    activating.value = true
    try {
      const s = await createSession(v, '720p')
      sessions.set(v.id, s)
      const el = videoEls.get(v.id)
      if (el) {
        attachSource(el, v, s)
        void playCurrent()
      }
    } catch (e) {
      slideError.value = (e as Error).message || '播放出错'
    } finally {
      activating.value = false
    }
  })()
}

function onSlideClick(v: MusicVideo) {
  if (v.id !== current.value?.id) return
  void togglePause()
}

async function togglePause() {
  const v = current.value
  const el = v ? videoEls.get(v.id) : null
  if (!el) return
  if (el.paused) await playCurrent()
  else el.pause()
}

function onSoundToggle() {
  muted.value = !muted.value
  if (!muted.value) mutedFallback.value = false
  const v = current.value
  const el = v ? videoEls.get(v.id) : null
  if (el) el.muted = muted.value
  if (!muted.value && el?.paused) void playCurrent()
}

// ===== 观看上报 / 会话保活 =====
function pingWatch(
  v: MusicVideo,
  extra: { playing: boolean; ended?: boolean; position?: number; duration?: number },
) {
  const el = videoEls.get(v.id)
  const duration = extra.duration ?? (Number(el?.duration) || v.duration || 0)
  dashboardApi
    .watchPing({
      music_video_id: v.id,
      position: extra.position ?? (el ? el.currentTime || 0 : 0),
      duration: duration || undefined,
      playing: extra.playing,
      ended: extra.ended ?? false,
    })
    .catch(() => {})
}

let watchTimer: number | null = null

function startWatchLoop(v: MusicVideo) {
  stopWatchLoop()
  pingWatch(v, { playing: true })
  watchTimer = window.setInterval(() => {
    if (v.id === current.value?.id && !paused.value) pingWatch(v, { playing: true })
  }, WATCH_PING_MS)
}

function stopWatchLoop() {
  if (watchTimer !== null) {
    window.clearInterval(watchTimer)
    watchTimer = null
  }
}

let heartbeatTimer: number | null = null

function startHeartbeat() {
  stopHeartbeat()
  heartbeatTimer = window.setInterval(() => {
    const v = current.value
    const s = v ? sessions.get(v.id) : null
    if (s && !paused.value) void playbackApi.heartbeat(s.session_id).catch(() => {})
  }, HEARTBEAT_MS)
}

function stopHeartbeat() {
  if (heartbeatTimer !== null) {
    window.clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
}

// ===== 进出 / 生命周期 =====
function posterUrl(v: MusicVideo) {
  return musicVideosApi.thumbnailUrl(v.id, undefined, IMG_W_FULL)
}

/** 副标题：艺人 / 歌名 / 时长，缺项自动收拢，避免空占位的「·」开头 */
function infoSub(v: MusicVideo) {
  const parts: string[] = []
  if (v.song_name) parts.push(v.song_name)
  if (v.duration) parts.push(formatDuration(v.duration))
  return parts.join(' · ')
}

function exit() {
  cleanup()
  if (window.history.length > 1) router.back()
  else router.replace('/shorts')
}

function cleanup() {
  const v = current.value
  if (v && startedIds.has(v.id)) pingWatch(v, { playing: false })
  stopWatchLoop()
  stopHeartbeat()
  for (const [, s] of sessions) void playbackApi.stop(s.session_id, { force: true }).catch(() => {})
  sessions.clear()
  for (const [, h] of hlsInstances) {
    try {
      h.destroy()
    } catch {
      /* 忽略 */
    }
  }
  hlsInstances.clear()
}

function onVisibility() {
  // 切后台：暂停当前条并补上报（回来点按继续）
  if (!document.hidden) return
  const v = current.value
  const el = v ? videoEls.get(v.id) : null
  if (el && !el.paused) {
    el.pause()
    if (v && startedIds.has(v.id)) pingWatch(v, { playing: false })
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKey)
  document.addEventListener('visibilitychange', onVisibility)
  void initialLoad()
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  document.removeEventListener('visibilitychange', onVisibility)
  cleanup()
})
</script>

<template>
  <div class="feed-root">
    <header class="feed-top">
      <button class="top-btn" type="button" aria-label="退出随机刷" @click="exit">
        <ChevronLeftOutlined :size="22" />
      </button>
      <div class="top-title">随机刷 · 短视频</div>
      <button class="top-btn" type="button" :aria-label="muted ? '开启声音' : '静音'" @click="onSoundToggle">
        <!-- 音量图标：icons.ts 无现成音量图标，内联 Material 路径 -->
        <svg v-if="!muted" viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true">
          <path
            d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"
          />
        </svg>
        <svg v-else viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true">
          <path
            d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3 3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4 9.91 6.09 12 8.18V4z"
          />
        </svg>
      </button>
    </header>

    <div v-if="loading" class="feed-hint">
      <LoadingOutlined :size="28" />
      <p>正在洗牌…</p>
    </div>
    <div v-else-if="loadError" class="feed-hint">
      <p>{{ loadError }}</p>
      <button class="end-btn" type="button" @click="initialLoad">重试</button>
    </div>
    <div v-else-if="!queue.length" class="feed-hint">
      <p>库里还没有可刷的短视频</p>
    </div>

    <div v-else ref="scroller" class="feed-scroller" @scroll="onScroll" @wheel.prevent="onWheel">
      <section v-for="(v, i) in queue" :key="v.id" class="feed-slide">
        <div class="feed-media">
          <video
            :ref="(el) => bindVideo(v.id, el)"
            class="feed-video"
            :poster="posterUrl(v)"
            loop
            playsinline
            preload="auto"
            @play="onSlidePlay(v)"
            @pause="onSlidePause(v)"
            @timeupdate="onSlideTime(v, $event)"
            @error="onSlideError(v)"
            @click="onSlideClick(v)"
          />
          <!-- 暂停遮罩 / 会话建立中 -->
          <button
            v-if="i === idx && paused && !activating && !slideError"
            class="feed-resume"
            type="button"
            aria-label="继续播放"
            @click="togglePause"
          >
            <PlayArrowOutlined :size="44" />
          </button>
          <div v-if="i === idx && activating" class="feed-loading">
            <LoadingOutlined :size="26" />
          </div>
          <div v-if="i === idx && slideError" class="feed-err">{{ slideError }}</div>
        </div>

        <!-- 信息栏 / 进度条：只挂当前条 -->
        <template v-if="i === idx">
          <div class="feed-progress"><span :style="{ width: `${progress * 100}%` }" /></div>
          <div class="feed-info">
            <div class="info-main">
              <p class="info-title">{{ v.name }}</p>
              <p v-if="infoSub(v)" class="info-sub">{{ infoSub(v) }}</p>
            </div>
            <router-link v-if="v.uid" class="info-link" :to="videoPath(v.uid)">
              详情
              <OpenInNewOutlined :size="12" />
            </router-link>
          </div>
        </template>
      </section>

      <!-- 尾卡：这轮刷完 -->
      <section v-if="exhausted" class="feed-slide feed-end">
        <ShuffleOutlined :size="36" />
        <p class="end-title">这一轮刷完啦</p>
        <p class="end-sub">库里没有被排除的短视频了</p>
        <button class="end-btn" type="button" @click="reshuffle">重新洗牌，再刷一轮</button>
        <button class="end-btn ghost" type="button" @click="exit">回到短视频页</button>
      </section>
    </div>

    <button v-if="mutedFallback && !loading" class="sound-chip" type="button" @click="onSoundToggle">
      点击开启声音
    </button>
  </div>
</template>

<style scoped>
.feed-root {
  position: fixed;
  inset: 0;
  z-index: 100;
  background: #000;
  color: #fff;
  overflow: hidden;
}

/* ===== 顶栏 ===== */
.feed-top {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  /* 上下渐变压住画面，保证白色图标可读 */
  background: linear-gradient(to bottom, rgba(0, 0, 0, 0.55), transparent);
  pointer-events: none;
}
.top-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  padding: 0;
  border: none;
  border-radius: 9999px;
  background: rgba(255, 255, 255, 0.14);
  color: #fff;
  cursor: pointer;
  pointer-events: auto;
  transition: background 0.15s;
}
.top-btn:hover {
  background: rgba(255, 255, 255, 0.26);
}
.top-title {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.5px;
  text-shadow: 0 1px 4px rgba(0, 0, 0, 0.6);
}

/* ===== 加载 / 错误 / 空态 ===== */
.feed-hint {
  position: absolute;
  inset: 0;
  z-index: 10;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: rgba(255, 255, 255, 0.85);
  font-size: 14px;
}

/* ===== 滑动容器 ===== */
.feed-scroller {
  position: absolute;
  inset: 0;
  overflow-y: auto;
  scroll-snap-type: y mandatory;
  overscroll-behavior: contain;
  scrollbar-width: none;
}
.feed-scroller::-webkit-scrollbar {
  display: none;
}
.feed-slide {
  height: 100%;
  scroll-snap-align: start;
  /* 一次甩只切一条（抖音行为） */
  scroll-snap-stop: always;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
}
.feed-media {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}
/* 视频按自身宽高比渲染：竖屏满高、横屏满宽，超界等比收（不裁切） */
.feed-video {
  max-width: 100%;
  max-height: 100%;
  width: auto;
  height: auto;
  background: #000;
  cursor: pointer;
}

.feed-resume {
  position: absolute;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 76px;
  height: 76px;
  padding: 0;
  border: none;
  border-radius: 9999px;
  background: rgba(0, 0, 0, 0.45);
  color: #fff;
  cursor: pointer;
}
.feed-loading {
  position: absolute;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 60px;
  height: 60px;
  border-radius: 9999px;
  background: rgba(0, 0, 0, 0.45);
  color: #fff;
}
.feed-err {
  position: absolute;
  left: 16px;
  right: 16px;
  bottom: 96px;
  padding: 8px 14px;
  border-radius: 10px;
  background: rgba(0, 0, 0, 0.6);
  color: #ffb4b4;
  font-size: 13px;
  text-align: center;
}

/* ===== 进度条 / 信息栏 ===== */
.feed-progress {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 3px;
  background: rgba(255, 255, 255, 0.18);
}
.feed-progress span {
  display: block;
  height: 100%;
  background: #0485f7;
  transition: width 0.2s linear;
}
.feed-info {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 16px;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  padding: 0 16px;
  pointer-events: none;
  text-shadow: 0 1px 4px rgba(0, 0, 0, 0.7);
}
.info-main {
  min-width: 0;
}
.info-title {
  margin: 0 0 4px;
  font-size: 15px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.info-sub {
  margin: 0;
  font-size: 13px;
  color: rgba(255, 255, 255, 0.82);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.info-link {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  border-radius: 9999px;
  background: rgba(255, 255, 255, 0.16);
  color: #fff;
  font-size: 12px;
  text-decoration: none;
  pointer-events: auto;
  transition: background 0.15s;
}
.info-link:hover {
  background: rgba(255, 255, 255, 0.28);
}

/* ===== 尾卡 ===== */
.feed-end {
  flex-direction: column;
  gap: 12px;
  color: rgba(255, 255, 255, 0.9);
  text-align: center;
}
.end-title {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
}
.end-sub {
  margin: 0 0 8px;
  font-size: 13px;
  color: rgba(255, 255, 255, 0.6);
}
.end-btn {
  padding: 9px 22px;
  border: none;
  border-radius: 9999px;
  background: #0485f7;
  color: #fff;
  font-size: 14px;
  cursor: pointer;
  transition: filter 0.15s;
}
.end-btn:hover {
  filter: brightness(1.15);
}
.end-btn.ghost {
  background: rgba(255, 255, 255, 0.14);
}

/* ===== 静音降级提示 ===== */
.sound-chip {
  position: absolute;
  top: 62px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 30;
  padding: 8px 16px;
  border: none;
  border-radius: 9999px;
  background: rgba(255, 255, 255, 0.9);
  color: #111;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
}
</style>
