/**
 * 平板客户端（idolMatrix Pad）桥接层。
 *
 * 只在原生容器里存在 `window.IdolAndroid`；在普通浏览器里所有函数都退化成 no-op，
 * 因此 Web 端行为与之前完全一致 —— 不需要为「有没有客户端」分叉页面逻辑。
 *
 * 职责边界（很重要）：
 * - 窗口态播放、页面滚动、列表、详情、设置：**全部由网页自己负责**，
 *   原生不参与任何布局，所以 UI 才能「完全复用」；
 * - 点全屏：把播放交给原生 ExoPlayer（系统硬解 / 多音轨 / 字幕轨 / 倍速），
 *   退出后原生回传进度，网页在原位置续播。
 */

export interface IdolAndroidBridge {
  isAvailable(): boolean
  version(): string
  serverBase(): string
  /** 平板真实解码能力（MediaCodecList 探测结果，JSON） */
  capabilities(): string
  /** 打开全屏原生播放器；返回 true 表示原生已接管 */
  playFullscreen(payload: string): boolean
  /** 设备/系统是否支持系统画中画（旧版 App 没有这个方法） */
  pipSupported?(): boolean
  toast(text: string): void
  syncCookie(): boolean
}

declare global {
  interface Window {
    IdolAndroid?: IdolAndroidBridge
    /** 原生播放器关闭时回调（片源绝对秒） */
    __idolNativePlayerClosed?: (position: number, ended: boolean) => void
  }
}

export function androidBridge(): IdolAndroidBridge | null {
  if (typeof window === 'undefined') return null
  const bridge = window.IdolAndroid
  if (!bridge) return null
  try {
    return bridge.isAvailable() ? bridge : null
  } catch {
    return null
  }
}

export function isNativeApp(): boolean {
  return androidBridge() !== null
}

/**
 * 平板的真实解码能力（snake_case，字段与后端 `ClientCapabilities.parse` 对齐）。
 *
 * 用它替换 Web 端的 `clientCapabilities()` 探测结果，服务端就会按「这台平板能直连」
 * 决策，而不是按 WebView 的移动端保守档（只 h264 / 1080p / 无 mkv）去转码。
 */
export function nativeCapabilities(): Record<string, unknown> | null {
  const bridge = androidBridge()
  if (!bridge) return null
  try {
    const raw = bridge.capabilities()
    if (!raw) return null
    const parsed = JSON.parse(raw) as Record<string, unknown>
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch {
    return null
  }
}

export interface NativeFullscreenPayload {
  videoId: number
  quality: string
  /** 起播位置：片源绝对秒 */
  startSeconds: number
  title?: string
  subtitleText?: string
  poster?: string
  /** true = 缩成系统小窗（画中画）；原生起来后立刻进 PiP */
  pip?: boolean
}

/**
 * 把播放交给全屏原生播放器。
 *
 * @returns true = 原生已接管（调用方应暂停网页播放器）；false = 没接管，调用方按原逻辑走
 */
export function playNativeFullscreen(payload: NativeFullscreenPayload): boolean {
  const bridge = androidBridge()
  if (!bridge) return false
  if (!payload.videoId) return false
  try {
    // 先交播放，再同步 Cookie：syncCookie 是可选的加分项，
    // 它抛异常不该把「打开播放器」一起拖死（这条以前是静默失败的来源之一）
    const ok = bridge.playFullscreen(JSON.stringify(payload))
    try {
      bridge.syncCookie()
    } catch {
      /* 忽略：原生拉流仍会用上一张票据 */
    }
    return ok
  } catch (e) {
    console.warn('[IdolAndroid] playFullscreen 调用失败', e)
    return false
  }
}

export function nativeToast(text: string) {
  const bridge = androidBridge()
  if (!bridge) return
  try {
    bridge.toast(text)
  } catch {
    /* 忽略 */
  }
}

/**
 * 原生侧能不能开画中画。
 *
 * WebView 里没有 `requestPictureInPicture`（`document.pictureInPictureEnabled` 也没有），
 * 所以网页自己的 `canPip` 在平板客户端里恒为 false —— 画中画只能问原生。
 * 旧版 App 没有 `pipSupported()`，这里返回 false，按钮自然不渲染。
 */
export function nativePipSupported(): boolean {
  const bridge = androidBridge()
  if (!bridge) return false
  try {
    return typeof bridge.pipSupported === 'function' && bridge.pipSupported()
  } catch {
    return false
  }
}

/**
 * 把播放交给原生并立刻缩成系统小窗（画中画）。
 *
 * 和全屏同一条握手流程：原生自己建会话（带平板真实能力 + 起播秒），
 * 网页侧要停掉窗口态会话，否则服务端会同时跑两路。
 */
export function playNativePip(payload: NativeFullscreenPayload): boolean {
  return playNativeFullscreen({ ...payload, pip: true })
}
