<script setup lang="ts">
/**
 * 视频播放器 —— xgplayer 内核（字节跳动）
 *
 * 对外契约（props / emits）与原 video.js 实现完全一致，调用方 VideoPlayView 零改动。
 *
 * 设计要点：
 * 1. 控制栏、进度条、时间码、倍速、音量、全屏、画中画全部使用 xgplayer 原生插件，
 *    颜色通过 commonStyle 对齐站内主色（#0485f7）。
 * 2. 转码窗口的「绝对时间轴」用 xgplayer 原生配置实现，不再自研进度条：
 *      - customDuration = 片源完整时长（进度条总长）
 *      - timeOffset     = 本路转码 window_start（当前时间码 = 流内时间 + offset）
 *    两者是 xgplayer 为「时间轴偏移」设计的官方配置（player.offsetDuration /
 *    progress.timeOffset / time.timeOffset 都读它），自研进度条可以整段删除。
 * 3. 拖动结束后的「窗口内 seek / 窗口外重建会话」判定：包装公开方法 player.seek。
 *    xgplayer 的进度条把拖动结果直接交给 player.seek，因此这是唯一需要拦截的点。
 * 4. 待播预览（pending、无源）：不加载任何源，用 urlNull 事件把「点了播放」转成
 *    play-request，交给父组件创建会话。
 * 5. iOS / iPadOS 无 MSE 的原生 HLS 分支：hls.js（xgplayer-hls.js 插件内核）
 *    的 isSupported() 为 false 时**不注册插件**，m3u8 直接交给 <video> 原生 HLS，
 *    与旧 xgplayer-hls 的 MSE.isMMSOnly() 自动放行行为对齐。
 * 6. 触屏端（手机/平板）交互语义：单击画面 = 显示/隐藏控制栏，双击 = 播放/暂停
 *    （xgplayer mobile 插件原生行为，用 closeVideoClick 关掉 pc 插件的「单击切播放」，
 *    见 spawnPlayer 注释）；倍速控件只在桌面端显示（ignores）。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Player, { Plugin } from 'xgplayer'
import HlsJsPlugin from 'xgplayer-hls.js'
import 'xgplayer/dist/index.min.css'

interface Source {
  src: string
  type?: string
  label?: string
}

const emit = defineEmits<{
  /** 播放器状态变化：buffering=是否正在缓冲（转码等待中），error=错误信息 */
  (e: 'state', payload: { buffering: boolean; error: string | null }): void
  (e: 'timeupdate', currentTime: number): void
  /** 用户拖动进度条完成后触发：方便父组件按目标时间重建转码会话 */
  (e: 'seeked', targetTime: number): void
  /** 用户在控制栏画质菜单中选择了某个画质 */
  (e: 'quality-select', quality: string): void
  /** 待播放预览模式下用户点击了控制栏播放按钮，请求父组件创建播放会话 */
  (e: 'play-request'): void
  /** 真正开始出画（不含换源过程中的中间态） */
  (e: 'play'): void
  /** 用户暂停或播完；换源 / seek 引起的 pause 不发 */
  (e: 'pause'): void
  /** 播到片尾 */
  (e: 'ended'): void
}>()

const props = withDefaults(
  defineProps<{
    sources: Source[]
    poster?: string
    /** 选中源 label（与 sources[].label 匹配） */
    activeLabel?: string
    /** 可选画质列表（如 ['original', '1080p', '720p']） */
    qualities?: string[]
    /** 当前选中的画质 key */
    activeQuality?: string
    /** 播放模式标签（直接播放/转码 等），显示在画质菜单标题区 */
    playModeLabel?: string
    /** 播放模式决策原因（tooltip，悬停查看为何直连/转码） */
    playModeReason?: string
    /** 是否正在缓冲转码 */
    buffering?: boolean
    /** 待播放预览模式：仅展示 poster 与原生控制栏，尚未建立播放会话 */
    pending?: boolean
    /** 换源后是否恢复到原播放位置。
     * Direct Play 换源没有 `-ss`，可以 restoreTime=true；
     * Transcode / Direct Stream（后端已 `-ss window_start`）必须 false，
     * 否则 currentTime 会叠在 `-ss` 之上，时间轴跳到大约两倍位置。 */
    restoreTime?: boolean
    /** 片源完整时长（秒）。转码时进度条总长度用此值。 */
    duration?: number
    /** 本路转码 window_start（秒）。转码进度条绝对时间 = startOffset + 流内时间 */
    startOffset?: number
    /** true=直连用原生时间轴/原生 seek；false=转码，进度条走偏移时间轴并拦截 seek */
    nativeSeek?: boolean
    /** 换源 canplay 后是否自动 play */
    autoPlay?: boolean
    /** 换源后在流内 seek 到此位置（秒），用于精确定位目标帧 */
    seekInto?: number
    /** 本会话「已转出上界」（**片源绝对秒**，0=未知）。
     * 转码进行中 HLS 清单无 ENDLIST → MSE 的 duration 恒为 Infinity，
     * 窗口判定必须靠它；详见 canSeekInPlace()。由父组件轮询 /window 维护。 */
    availableUntil?: number
  }>(),
  {
    qualities: () => [],
    activeQuality: 'original',
    playModeLabel: '',
    playModeReason: '',
    buffering: false,
    pending: false,
    restoreTime: false,
    startOffset: 0,
    nativeSeek: true,
    autoPlay: false,
    seekInto: 0,
    availableUntil: 0,
  },
)

const containerRef = ref<HTMLDivElement | null>(null)
let player: any = null

/** 换源进行中：期间压制 xgplayer 内置 error UI（详见模板下方 CSS 注释）。
 * pending→false 瞬间（点播放建会话）error UI 会先显形，新源 canplay 后才被
 * xgplayer 收起 —— 中间裸露几百 ms，用户看到「不支持的音频/视频格式」一闪。 */
const switching = ref(false)
let switchingTimer: number | null = null

function markSwitching() {
  switching.value = true
  if (switchingTimer) window.clearTimeout(switchingTimer)
  // 兜底超时：换源 8s 后无论如何放行（真错误也必须能显示出来）
  switchingTimer = window.setTimeout(() => {
    switching.value = false
    switchingTimer = null
  }, 8000)
}

function clearSwitching() {
  if (switchingTimer) {
    window.clearTimeout(switchingTimer)
    switchingTimer = null
  }
  switching.value = false
}

// ===== 换源 / 事件抑制状态 =====
let srcGen = 0
/** 用户已点过播放/拖过进度：换源后必须继续 play，不能掉回暂停 */
let wantPlay = false
/** 是否收到过真实的用户手势（容器内 pointerdown，捕获）。
 * ⚠ v3.4.5 修「PC 悬停即播 / 移动端单击即播」：xgplayer 以空 url 挂载时
 * _startInit('') 会同步 emit urlNull（见 xgplayer player.js:381），旧实现把它
 * 当成「用户点了播放按钮」→ play-request → 预播挂载即自动开播 —— 悬停/单击
 * 唤出立刻变成自动播放。urlNull 只在见过真实手势后才允许转成 play-request。 */
let userGestureSeen = false
/** 换源 / 窗口内 seek 时播放器会先 pause 再 play，不能当成用户暂停 */
let ignorePause = false
let hushGen = 0
/** 转码换源期间禁止 seeked 把会话打回开头 */
let suppressSeeked = false
/** 内部程序化 seek（canplay 后的定位），不经过窗口判定 */
let internalSeek = false
/** 换源后待执行的 canplay 回调（按下标 gen 比对，过期即丢弃） */
let pendingCanplay: (() => void) | null = null
/** 最近一次换源时刻：用于忽略「旧源失效」引发的瞬时 error */
let lastSwitchAt = 0

function hushPause(ms = 800) {
  const gen = ++hushGen
  ignorePause = true
  window.setTimeout(() => {
    if (gen === hushGen) ignorePause = false
  }, ms)
}

function tryPlay() {
  if (!player) return
  try {
    const ret = player.play()
    if (ret && typeof ret.catch === 'function') ret.catch(() => {})
  } catch {
    /* 自动播放被拦截时等下一次用户手势 */
  }
}

function currentSource(): Source | undefined {
  if (props.activeLabel) {
    const found = props.sources.find((s) => s.label === props.activeLabel)
    if (found) return found
  }
  return props.sources[0]
}

// ===== 画质标签（与 models.ts QUALITY_LABEL 保持一致）=====
const QUALITY_TEXT: Record<string, string> = {
  original: '原画',
  '2160p': '2160P',
  '1440p': '1440P',
  '1080p': '1080P',
  '720p': '720P',
  '480p': '480P',
  '360p': '360P',
}
function qualityText(key: string): string {
  return QUALITY_TEXT[key] || key
}

/** 画质菜单选中回调（由 setup 注入，插件类内部不持有 emit） */
let onQualityPick: ((quality: string) => void) | null = null

/**
 * 画质选择控件 —— 挂在 xgplayer 原生控制栏右栏的自定义控件。
 *
 * 为什么不用内置 definition 插件：内置插件的语义是「同一路流切换清晰度」，
 * 选中后会自行 switchURL；而本项目的语义是「选画质 = 重建转码会话」，
 * 必须回调父组件，不能让它自己换源。外观沿用 xgplayer 原生图标样式。
 */
const QUALITY_ICON_SVG =
  '<svg viewBox="0 0 24 24" width="100%" height="100%" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round">' +
  '<path d="M4 6h16M4 12h16M4 18h9"/>' +
  '<circle cx="17.5" cy="18" r="2.4" fill="currentColor" stroke="none"/>' +
  '</svg>'

const PluginBase: any = Plugin

class QualitySelector extends PluginBase {
  listEl: HTMLElement | null = null
  docClick: ((e: Event) => void) | null = null

  static get pluginName() {
    return 'qualitySelector'
  }

  static get defaultConfig() {
    return {
      position: 'controlsRight',
      index: 3,
      list: [] as string[],
      active: 'original',
      tip: '',
    }
  }

  render() {
    return (
      '<xg-icon class="xgplayer-quality xg-icon" tabindex="0">' +
      '<span class="xgplayer-icon">' +
      QUALITY_ICON_SVG +
      '</span>' +
      '<xg-quality-list class="xgplayer-quality-list"></xg-quality-list>' +
      '</xg-icon>'
    )
  }

  afterCreate() {
    this.listEl = this.find('.xgplayer-quality-list')

    // 点击控件外任意处收起菜单
    this.docClick = (e: Event) => {
      if (this.root && !this.root.contains(e.target as Node)) this.close()
    }
    document.addEventListener('click', this.docClick)

    this.bind('click', (e: Event) => {
      e.stopPropagation()
      const picked = (e.target as HTMLElement)?.getAttribute?.('data-q')
      if (picked) {
        this.close()
        onQualityPick?.(picked)
        return
      }
      if (this.isOpen()) this.close()
      else this.open()
    })

    this.refresh()
  }

  destroy() {
    if (this.docClick) document.removeEventListener('click', this.docClick)
    this.docClick = null
    this.listEl = null
  }

  isOpen() {
    return !!this.root?.classList.contains('is-open')
  }

  open() {
    this.root?.classList.add('is-open')
    this.player?.focus?.({ autoHide: false })
  }

  close() {
    this.root?.classList.remove('is-open')
  }

  /** 由外部（props 变化）调用，刷新列表与选中态 */
  refresh() {
    const list: string[] = this.config?.list || []
    const active: string = this.config?.active || 'original'

    if (this.root) {
      this.root.style.display = list.length ? '' : 'none'
      this.root.setAttribute('title', this.config?.tip || '')
      if (!list.length) this.close()
    }

    const el = this.listEl
    if (!el) return

    const item = (k: string) =>
      `<xg-quality-item class="xgplayer-quality-item${
        k === active ? ' is-active' : ''
      }" data-q="${k}">${qualityText(k)}</xg-quality-item>`

    const parts: string[] = []
    if (list.includes('original')) parts.push(item('original'))
    const rest = list.filter((k) => k !== 'original')
    if (rest.length) {
      if (parts.length) parts.push('<xg-quality-title>转码</xg-quality-title>')
      for (const k of rest) parts.push(item(k))
    }
    el.innerHTML = parts.join('')
  }
}

function qualityPlugin(): any {
  if (!player) return null
  try {
    return player.getPlugin?.('qualitySelector') || player.plugins?.qualitySelector || null
  } catch {
    return null
  }
}

/** 把最新的画质列表 / 选中态 / 播放模式提示同步给控制栏控件 */
function syncQualityState() {
  const qp = qualityPlugin()
  if (!qp?.refresh) return
  qp.config.list = [...(props.qualities || [])]
  qp.config.active = props.activeQuality || 'original'
  qp.config.tip = [props.playModeLabel, props.playModeReason].filter(Boolean).join(' · ')
  qp.refresh()
}

// ===== 时间轴（转码窗口偏移）=====
/**
 * 直连：原生时间轴（customDuration/timeOffset 归零 → 退回 player.duration）
 * 转码：进度条总长 = 片源完整时长，当前时间 = 流内时间 + startOffset
 */
function syncTimeline() {
  if (!player) return
  const cfg = player.config || {}
  if (props.nativeSeek) {
    cfg.customDuration = 0
    cfg.timeOffset = 0
  } else {
    cfg.customDuration = props.duration || 0
    cfg.timeOffset = props.startOffset || 0
  }
}

/** 原地 seek 允许的前探容差（秒）：目标略超已转出上界时，后续分片通常几秒内
 * 就产出，不值得为它重建一次会话（重建=新一轮 -ss + 转码启动）。 */
const SEEK_AHEAD_TOLERANCE = 3

/** 播放器当前已缓冲到的流内末端（秒）；拿不到就返回 0。 */
function bufferedEnd(p: any): number {
  try {
    const media = p.media
    const b = media && media.buffered
    if (!b || !b.length) return 0
    const cur = Number(media.currentTime) || 0
    for (let i = 0; i < b.length; i += 1) {
      if (b.start(i) - 1 <= cur && cur <= b.end(i) + 1) return b.end(i)
    }
    return b.end(b.length - 1)
  } catch {
    return 0
  }
}

/**
 * 目标时间能否「原地 seek」（true=流内跳，false=交给父组件重建会话）。
 *
 * ⚠ 这里是 v3.4.3 修的坑：**不能只看 duration**。转码进行中 HLS 清单是
 * EVENT（无 ENDLIST，为的是让 iOS 原生 HLS 持续增补），此时 MSE 的
 * `duration` 恒为 **Infinity**（浏览器实测）→ `time <= offset + duration`
 * 恒为真 → 永远判「在窗口内」→ 永远原地 seek，父组件那条「按目标时间重建
 * 转码会话（-ss）」的路径**永不触发**；而目标分片还没转出来，播放器只能卡在
 * 已转出区间 —— 用户看到的就是「跳到后面跳不过去」。
 *
 * 按可信度依次降级：
 *   1) duration 有限（清单已收尾 VOD / 直连）→ 用它（分片全在盘，原地跳即可）
 *   2) 后端 availableUntil（已转出上界，绝对秒）→ 目标在其内才原地跳
 *   3) 两者都没有（首片还没出）→ 退回「已缓冲末端」再给前探容差
 */
function canSeekInPlace(p: any, time: number): boolean {
  const offset = props.startOffset || 0
  if (!(time >= offset - 0.5)) return false
  const mediaDur = Number(p.duration) || 0
  if (Number.isFinite(mediaDur) && mediaDur > 1) {
    return time <= offset + mediaDur - 0.5
  }
  const avail = Number(props.availableUntil) || 0
  if (avail > 0) return time <= avail + SEEK_AHEAD_TOLERANCE
  return time <= offset + bufferedEnd(p) + SEEK_AHEAD_TOLERANCE
}

/**
 * 包装 player.seek（xgplayer 公开 API）：进度条拖动结束后 xgplayer 会直接
 * 调用它并把「偏移时间轴上的时间」传进来，这里是唯一的拦截点。
 *
 * - 直连：原样透传
 * - 转码：目标可原地跳（见 canSeekInPlace）→ 换算成流内时间 seek
 *         否则 → 交给父组件按目标时间重建转码会话
 */
function installSeekGuard(p: any) {
  const rawSeek = p.seek.bind(p)
  p.seek = (time: number, status?: 'play' | 'pause' | 'auto') => {
    if (props.nativeSeek || internalSeek) return rawSeek(time, status)

    const offset = props.startOffset || 0
    if (canSeekInPlace(p, time)) {
      hushPause(1200)
      wantPlay = true
      return rawSeek(Math.max(0, time - offset), status)
    }

    wantPlay = true
    hushPause(1200)
    tryPlay()
    emit('seeked', time)
    return undefined
  }
}

/** 程序化定位（不经过窗口判定） */
function seekInternal(time: number) {
  if (!player || !Number.isFinite(time) || time < 0) return
  internalSeek = true
  try {
    player.currentTime = time
  } catch {
    /* 忽略 */
  }
  internalSeek = false
}

// ===== 换源 =====
function applySource() {
  if (!player) return
  const src = currentSource()
  const gen = ++srcGen

  syncTimeline()
  syncQualityState()

  if (!src) {
    // 无源：待播预览态，保留 poster 与控制栏
    stopPendingEvents()
    if (props.pending && !wantPlay) {
      suppressSeeked = false
      emit('state', { buffering: false, error: null })
    }
    return
  }

  emit('state', { buffering: true, error: null })
  markSwitching()
  hushPause(1200)

  const shouldPlay = wantPlay || props.autoPlay || !props.pending
  const prevTime = Number(player.currentTime) || 0
  if (!props.nativeSeek) suppressSeeked = true
  if (shouldPlay) wantPlay = true
  try {
    player.autoplay = shouldPlay
  } catch {
    /* 忽略 */
  }

  // 换源后的定位策略（canplay 时执行）：
  //   直连 + restoreTime → 回到原播放位置（Direct Play 换源不换时间轴）
  //   转码 / Direct Stream（后端已 -ss）→ 落到 seekInto（精确定位目标帧）
  const restoreTo = props.nativeSeek && props.restoreTime ? prevTime : 0
  const seekTo = !props.nativeSeek && props.seekInto > 0.35 ? props.seekInto : 0

  pendingCanplay = () => {
    if (gen !== srcGen) return
    if (restoreTo > 0) {
      seekInternal(Math.min(restoreTo, Number(player.duration) || restoreTo))
    } else if (seekTo > 0) {
      seekInternal(seekTo)
    }
    if (!props.nativeSeek) suppressSeeked = false
    if (shouldPlay || wantPlay || !props.pending) tryPlay()
  }

  try {
    lastSwitchAt = Date.now()
    // HLS 插件是否已挂钩：HlsJsPlugin 通过 URL_CHANGE 事件接管换源（switchURL →
    // src setter → emit URL_CHANGE → 插件 register() 重建 hls 实例），不在实例上
    // 覆写 switchURL，因此用「插件已注册且内核可用」判断钩子是否装上。
    const hlsHooked = HLS_MSE_OK && !!player.getPlugin?.('HlsJsPlugin')
    const inErrorState = !!player.root?.classList?.contains('xgplayer-is-error')
    // ⚠ xgplayer-hls.js 的 beforePlayerInit/URL_CHANGE 对**任何** url 都无条件
    // hls.js 接管（attachMedia 抢占 <video>，src 变 MSE blob）—— Direct Play 的
    // 渐进流（/stream，MKV/mp4 原文件）会被它当 HLS 清单解析而死在起播。
    // 因此实例的插件形态必须与源类型匹配：m3u8 ↔ 挂 HlsJsPlugin；
    // 渐进流 ↔ 裸 <video>。形态不匹配（wantHls ≠ hlsHooked）时重建实例
    // （spawnPlayer 按源类型配插件）；匹配则走 switchURL（含 iOS 原生 HLS 的
    // m3u8→m3u8：HLS_MSE_OK=false 时两者恒 false，不重建，行为不变）。
    const wantHls = HLS_MSE_OK && HLS_URL_RE.test(src.src)
    const needRespawn = inErrorState || wantHls !== hlsHooked
    if (needRespawn) {
      // 按「创建即带 url」的官方用法重建实例，让插件（或裸 video）接管。
      spawnPlayer(src.src)
    } else {
      player.switchURL(src.src)
    }
  } catch (err) {
    console.warn('[VideoPlayer] 换源失败：', err)
    emit('state', { buffering: false, error: '播放出错，请尝试切换画质或稍后重试' })
    return
  }

  /**
   * 兜底：xgplayer 的 switchURL 会记下切换瞬间的暂停态，canplay 后原样恢复；
   * 而画质切换时旧会话刚被停掉，播放器恰好处于暂停 —— 结果换了源却不续播。
   * 这里在换源后补两次「该播就播」，跨过 canplay 的时序。
   *
   * ⚠ 不要改成「短间隔连续重试」（v3.2.59 试过 300ms 起、每 600ms 一次、共 15s）：
   *   媒体就绪前的连续 play() 会把它打进 `not-allow-autoplay` 状态并**永久卡住**，
   *   起播直接失败（实测交互回归从 10/10 掉到 0/1，回退后恢复 10/10）。
   *   两次固定延时 + canplay 回调是实测可靠的口径。
   */
  if (shouldPlay) {
    const ensure = () => {
      if (gen !== srcGen || !player) return
      if (player.paused) tryPlay()
    }
    window.setTimeout(ensure, 400)
    window.setTimeout(ensure, 1500)
  }
}

function stopPendingEvents() {
  pendingCanplay = null
}

function bindEvents(p: any) {
  p.on('waiting', () => {
    emit('state', { buffering: true, error: null })
  })

  p.on('playing', () => {
    wantPlay = true
    ignorePause = false
    clearSwitching()
    emit('state', { buffering: false, error: null })
    emit('play')
  })

  p.on('pause', () => {
    if (ignorePause || suppressSeeked) return
    // 换源 / seek 会先 pause：等一帧确认仍是暂停且不在 seeking，才当成用户暂停
    window.setTimeout(() => {
      if (ignorePause || suppressSeeked || !p) return
      if (p.paused === false || p.isSeeking) return
      emit('pause')
    }, 80)
  })

  p.on('ended', () => {
    if (ignorePause) return
    emit('ended')
    emit('pause')
  })

  p.on('error', () => {
    // 待播预览态还没有源，xgplayer 对空 url 也会抛一次 error，按「无源」静默
    const src = currentSource()
    if (!src) return
    /**
     * 换源期间旧会话被后端停掉，旧 URL 会立刻 404（或 404 后重试耗尽）并在
     * media 上抛 error；紧接着新会话来接上。这种噪声不该把错误条闪给用户，
     * 用「当前媒体源已不是本组件要播的源」+「刚换过源」两道判据过滤。
     */
    const cur = String(player?.media?.currentSrc || '')
    if (cur && src.src && !cur.endsWith(src.src)) return
    if (Date.now() - lastSwitchAt < 5000) return
    clearSwitching()
    emit('state', { buffering: false, error: '播放出错，请尝试切换画质或稍后重试' })
  })

  p.on('timeupdate', () => {
    const ct = Number(p.currentTime) || 0
    // 转码时发绝对时间（startOffset + 流内时间）；直连发流内时间（=绝对时间）
    emit('timeupdate', props.nativeSeek ? ct : (props.startOffset || 0) + ct)
  })

  // 仅直连允许原生 seek 上报；转码由 seek guard 负责
  p.on('seeked', () => {
    if (props.nativeSeek && !suppressSeeked) {
      emit('seeked', Number(p.currentTime) || 0)
    }
  })

  p.on('seeking', () => hushPause(500))

  p.on('canplay', () => {
    // 新源就绪：xgplayer 会自行收起 error UI，压制窗口到此为止
    clearSwitching()
    const fn = pendingCanplay
    pendingCanplay = null
    fn?.()
  })

  // 无源时点击播放按钮 → 请求父组件创建会话。
  // ⚠ 必须有 userGestureSeen 门卫：挂载本身（空 url）也会触发一次 urlNull，
  // 不挡的话预播态挂载即自动开播（正是「悬停/单击就播」的根因）。
  p.on('urlNull', () => {
    if (!userGestureSeen) return
    wantPlay = true
    emit('play-request')
  })
}

/**
 * 创建（或带 URL 重建）xgplayer 实例。
 *
 * ⚠ 必须在创建时给出 url（可以为空串，但不能等到创建后再用 switchURL 首次喂
 * m3u8）：HlsJsPlugin 的接管发生在 `beforePlayerInit`，而它只在 `_startInit`
 * 里跑；url 为空时 `_startInit` 提前 return（见下方 nullUrlStart 注释），
 * 插件永远不挂钩 —— 之后 switchURL(m3u8) 只会裸设 video.src，Chrome 原生
 * 解不了 m3u8（DEMUXER_ERROR_COULD_NOT_PARSE）。实测钩子装上后，增长中的
 * EVENT 清单（转码窗口）是可以正常边转边播的。
 */
/**
 * 触屏设备判定：主输入为粗指针（手机/平板）或移动 UA。
 * 与 VideoPlayView 的 canHover（hover:hover + pointer:fine）互补：
 * 触屏端走「单击控制栏 / 双击播放暂停」语义，桌面端保持原生单击行为。
 */
const IS_TOUCH_DEVICE =
  typeof window !== 'undefined' &&
  (window.matchMedia?.('(hover: none) and (pointer: coarse)').matches ||
    /Android|iPhone|iPad|iPod|IEMobile|Opera Mini|HarmonyOS/i.test(navigator.userAgent || ''))

/**
 * hls.js 内核可用性（MSE 存在）：iOS Safari 等无 MSE 的环境返回 false，
 * 此时**不注册 HlsJsPlugin**，m3u8 交给 <video> 原生 HLS（Safari 内建支持）。
 * 静态 getter isSupported 返回的就是 hls.js 的 Hls.isSupported 函数本身。
 */
const HLS_MSE_OK = (() => {
  try {
    return !!(HlsJsPlugin as unknown as { isSupported?: () => boolean }).isSupported?.()
  } catch {
    return false
  }
})()

/** HLS 清单 URL 判定（.m3u8 结尾，忽略 query/hash）。
 * Direct Play 渐进直连源（/stream 返回的 MKV/mp4 原文件）不是 m3u8，
 * 绝不能让 HlsJsPlugin 接管 —— 插件的 beforePlayerInit 对任何 URL 都无条件
 * hls.js attachMedia 抢占 <video>，渐进流会被当 HLS 清单解析而死在起播。 */
const HLS_URL_RE = /\.m3u8([?#]|$)/i

function spawnPlayer(url: string) {
  if (!containerRef.value) return
  if (player) {
    try {
      player.destroy(true)
    } catch {
      /* 旧实例可能已残废，忽略 */
    }
    player = null
  }

  const cfg: Record<string, unknown> = {
    el: containerRef.value,
    url,
    poster: props.poster || '',
    /**
     * xgplayer 的 defaultConfig 带 width:600 / height:337.5，_initDOM 会把它们
     * 写成 root 的内联样式，直接盖掉外层 CSS 的 100%。显式声明 100% 才能让
     * 播放器跟着 hero 画框走（字符串会原样写进 style，数字会补 px）。 */
    width: '100%',
    height: '100%',
    /**
     * ⚠ 不要设 nullUrlStart=true：xgplayer 的 _startInit 在 url 为空时会提前
     * return，而「把 <video> 插入 DOM」正排在 return 之后 —— 待播预览态会因此
     * 连媒体元素都没有，整个播放器不可见。保持默认 false，空 src 引发的 error
     * 由 error 处理按「无源」静默掉。
     */
    fluid: false,
    playsinline: true,
    autoplay: false,
    volume: 1,
    playbackRate: [0.5, 1, 1.25, 1.5, 2],
    /** 页面已有圆形播放遮罩，隐藏 xgplayer 的中央大播放键；
     *  definition 由自定义控件接管（项目语义是「选画质 = 重建转码会话」，
     *  内置 definition 插件选中后会自行 switchURL，与项目语义冲突）；
     *  触屏端再去掉倍速控件（v3.4.5 用户要求，桌面端保留） */
    ignores: IS_TOUCH_DEVICE
      ? ['start', 'definition', 'download', 'playbackRate']
      : ['start', 'definition', 'download'],
    /**
     * 画中画。xgplayer 的 pip 插件默认就在 preset 的 controlsIcons 里注册好了，
     * 但 defaultConfig.showIcon 是 false；插件的 beforeCreate 只在
     * `typeof player.config.pip === 'boolean'` 时把它当开关，故这里直接给对象形式
     * （对象形式不会被 beforeCreate 覆盖，且能顺带指定 index）。
     * index 语义：右栏是 flex-direction:row-reverse，**index 越大越靠左** ——
     * 全屏 0 / 网页全屏 1 / 音量 1 / 画质 3 / 倍速 4，故取 2 落在「音量↔画质」之间。
     * ⚠ 画中画需要安全上下文（https 或 localhost）：NAS 走明文 http 时
     * `document.pictureInPictureEnabled` 为 false，插件 render() 直接返回空 →
     * 按钮自动不出现，不会留一个点了没用的图标。
     */
    pip: { showIcon: true, index: 2 },
    /** 画面适配：竖屏片在 16:9 画框内左右留黑边、居中显示 */
    videoFillMode: 'contain',
    // 点击/触摸语义分两端：
    // - 桌面（false）：单击画面 = 播放/暂停，双击 = 全屏（pc 插件原生行为）。
    // - 触屏（true）：必须关掉 pc 插件的「单击切播放」——移动端触摸会合成
    //   click 事件，不关它的话单击画面约 300ms 后就被切播放，且预播态
    //   （state<ready）mobile 插件自己也会 play()，用户感知就是「点一下
    //   就开播」。交给 mobile 插件原生语义接管：单击 = 显示/隐藏控制栏
    //   （focus/blur），双击 = 播放/暂停（closedbClick 默认 false）。
    //   ⚠ closeVideoDblclick 绝不能设 true：mobile 插件会把它转写成
    //   closedbClick=true，连「双击切播放」一起关掉（见插件 afterCreate）。
    closeVideoClick: IS_TOUCH_DEVICE,
    // 站内主色
    commonStyle: {
      playedColor: '#0485f7',
      progressColor: 'rgba(255, 255, 255, 0.28)',
      cachedColor: 'rgba(255, 255, 255, 0.5)',
      volumeColor: '#0485f7',
    },
    // 插件形态与源类型匹配（根因修复）：仅 m3u8 且 MSE 可用时挂 HlsJsPlugin；
    // 渐进直连流（/stream）必须裸 <video> —— HlsJsPlugin 的 beforePlayerInit
    // 会无条件 hls.js 接管任何 URL，渐进流被当 HLS 清单解析 → 起播死（转圈）。
    // MSE 不可用（iOS Safari）时 m3u8 也交给 <video> 原生 HLS。
    plugins: HLS_MSE_OK && HLS_URL_RE.test(url) ? [QualitySelector, HlsJsPlugin] : [QualitySelector],
  }

  try {
    player = new Player(cfg)
  } catch (err) {
    console.error('[VideoPlayer] xgplayer 初始化失败：', err)
    return
  }

  onQualityPick = (q: string) => emit('quality-select', q)

  bindEvents(player)
  installSeekGuard(player)
  syncQualityState()
  syncTimeline()

  // 待播预览：控制栏常驻（上层遮罩挡住 hover，等不到 autoHide 的唤出）
  if (props.pending) {
    try {
      player.focus({ autoHide: false })
    } catch {
      /* 忽略 */
    }
  }
}

onMounted(() => {
  // 用户手势门卫：容器内任何按下都算（捕获阶段，控制栏/画面都覆盖）。
  // 绑一次即可，spawnPlayer 重建实例不影响。
  containerRef.value?.addEventListener('pointerdown', () => { userGestureSeen = true }, true)
  // 预播态播放锚点接线：hero-overlay 故意留出底部 58px 给控制栏
  // （「播放锚点回归控制栏 PlayToggle」），但空 url 挂载下任何 xgplayer 内部
  // 播放入口都会无声失败 —— urlNull 只在挂载时发过一次（已被手势门卫挡掉），
  // 且 `start` 插件被 ignores，控制栏没有 PlayToggle，画面中央只剩
  // xgplayer-enter（加载/重试按钮）。这里在捕获阶段把预播态下这些入口的
  // 点击统一转成 play-request，由父组件建会话开播；播放开始后（有真实源）
  // 不再拦截，全部恢复原生行为。
  containerRef.value?.addEventListener('click', (e) => {
    if (currentSource()?.src) return
    const t = e.target as HTMLElement | null
    const PLAY_ENTRY = '.xgplayer-enter, .xgplayer-error-refresh, .xgplayer-play, .xgplayer-icon-play, .xgplayer-play-icon'
    if (t?.closest?.(PLAY_ENTRY)) {
      e.preventDefault()
      e.stopPropagation()
      userGestureSeen = true
      emit('play-request')
    }
  }, true)
  spawnPlayer(currentSource()?.src || '')
  applySource()
})

watch(
  () => [props.sources, props.activeLabel],
  () => applySource(),
  { deep: true },
)

watch(
  () => props.poster,
  (p) => {
    if (player) player.poster = p || ''
  },
)

watch(
  () => [props.qualities, props.activeQuality, props.playModeLabel, props.playModeReason],
  () => syncQualityState(),
  { deep: true },
)

watch(
  () => [props.duration, props.startOffset, props.nativeSeek],
  () => syncTimeline(),
)

watch(
  () => props.pending,
  (pending, wasPending) => {
    if (!player) return
    if (pending) {
      try {
        player.focus({ autoHide: false })
      } catch {
        /* 忽略 */
      }
    }
    if (wasPending && !pending) {
      wantPlay = true
      tryPlay()
    }
  },
)

onBeforeUnmount(() => {
  onQualityPick = null
  stopPendingEvents()
  if (switchingTimer) {
    window.clearTimeout(switchingTimer)
    switchingTimer = null
  }
  try {
    player?.destroy()
  } catch {
    /* 忽略 */
  }
  player = null
})
</script>

<template>
  <!-- 外层承载 Vue 的 class 绑定；内层是 xgplayer 的挂载点。
       两者必须分开：Vue 的 :class 会在更新时重写整个 class 属性，
       若把 xgplayer 直接挂在外层，它自己加的 xgplayer-* 状态类会被冲掉。 -->
  <div class="video-player" :class="{ 'pending-mode': props.pending, 'switching-mode': switching }">
    <div ref="containerRef" class="video-stage" />
  </div>
</template>

<style scoped>
.video-player {
  width: 100%;
  height: 100%;
  background: #000;
  border-radius: 8px;
  overflow: hidden;
  position: relative;
}

.video-stage {
  width: 100%;
  height: 100%;
}
</style>

<style>
/* ===== 待播预览：控制栏常驻 =====
 * 上层 hero-overlay 挡住鼠标 hover，等不到 xgplayer 的 autoHide 唤出逻辑。 */
.video-player.pending-mode .xgplayer-controls {
  opacity: 1 !important;
  visibility: visible !important;
  pointer-events: auto !important;
}

/* ===== 待播预览 / 换源期间：隐藏全屏错误层 =====
 * ⚠ v3.4.5 实测踩坑：空 url 挂载必然抛一次 media error，xgplayer-error 插件
 * 会渲染全屏错误层（xgplayer-error，z-index 高于预播热区按钮），把后续所有
 * 点击/触摸全部吃掉 —— 移动端「单击显隐控制栏 / 双击播放」根本到不了覆盖层
 * （CDP elementFromPoint 实测命中 XG-ERROR）。这层只在「有真实播放源却出错」
 * 时才有意义，待播预览态（pending）直接隐藏。
 * ⚠ 换源期间（switching）同样隐藏：pending→false 瞬间这层会先显形，新源
 * canplay 后才被 xgplayer 收起，中间裸露几百 ms —— 用户点播放后会看到
 * 「不支持的音频/视频格式」一闪再开播，正是空 url 遗留的 error 层。 */
.video-player.pending-mode .xgplayer-error,
.video-player.switching-mode .xgplayer-error {
  display: none !important;
}

/* ===== 控制栏画质控件（自定义，挂在原生右栏）=====
 * 布局刻意「零额外声明」以对齐原生图标：xg-icon 的 height/margin/position 全部
 * 沿用 xgplayer 自己的 `.xgplayer xg-icon{height:40px;position:relative;...}`。 */
.video-player .xgplayer-quality {
  /* 显式钉住：下拉是 position:absolute，需要这层做定位参照
   * （xgplayer 原生也给同值，但这里是真实依赖，不借它的） */
  position: relative;
  cursor: pointer;
  color: rgba(255, 255, 255, 0.9);
}
.video-player .xgplayer-quality .xgplayer-icon {
  /* ⚠ 两条都不能改（v3.2.59 用户报「画质按钮和其他按钮不在一条水平线」的根因）：
   * ① width 与音量/全屏同为 28px —— 宽度只是图标盒，用于保持间距节奏；
   * ② height 必须是 100%（即 40px），**绝不能按图形尺寸写成 18px**：
   *    xgplayer 原生图标盒的居中是 `top:50% + translateY(-50%)`
   *    （见 `.xgplayer xg-icon .xgplayer-icon`），这个技巧只有当
   *    「图标盒高度 == 容器高度」时才是净零偏移；高度一旦变小，就退化成
   *    向下偏移 (40-h)/2 —— h=18 时正好低 11px，实测 svg top 540 vs 原生 518。
   * ③ 图形本身由 svg 的 viewBox + preserveAspectRatio 在盒内自动居中，
   *    与盒高解耦，所以把盒撑满不会让图标变大。 */
  width: 28px;
  height: 100%;
  display: block;
}
.video-player .xgplayer-quality:hover {
  color: #fff;
}

.video-player .xgplayer-quality-list {
  display: none;
  position: absolute;
  right: 0;
  bottom: 100%;
  margin-bottom: 8px;
  min-width: 104px;
  padding: 6px 0;
  border-radius: 8px;
  background: rgba(20, 20, 22, 0.96);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.45);
  z-index: 10;
}

.video-player .xgplayer-quality.is-open .xgplayer-quality-list {
  display: block;
}

.video-player .xgplayer-quality-item,
.video-player .xgplayer-quality-title {
  display: block;
  padding: 6px 14px;
  font-size: 12px;
  line-height: 1.4;
  white-space: nowrap;
  color: rgba(255, 255, 255, 0.85);
}

.video-player .xgplayer-quality-item:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
}

.video-player .xgplayer-quality-item.is-active {
  color: #0485f7;
}
.video-player .xgplayer-quality-item.is-active::before {
  content: '✓ ';
}

.video-player .xgplayer-quality-title {
  padding: 8px 14px 4px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  color: rgba(255, 255, 255, 0.45);
  cursor: default;
}
</style>
