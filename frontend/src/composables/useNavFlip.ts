/**
 * 顶栏导航标签的跨页滑动动画（FLIP）。
 *
 * 背景：首页的导航行在右列（`.feed-nav-links`，x = 封面列宽 + 44），
 * 其他页面的导航行在顶栏里水平居中（`.sa-nav-group`）。两者是不同组件、不同坐标，
 * 直接切换会「瞬移」。这里在路由切换时把新导航行先摆到旧导航行的位置，
 * 再用 transform 过渡回自己的位置 —— 视觉上就是标签从首页位置滑到顶栏居中位。
 *
 * 只动 `[data-sa-nav]`（导航链接组），品牌烙印与顶栏容器一律不参与动画：
 * 两处的品牌/导航行 y 坐标已按像素对齐（见 SaHeader.vue 与 HomeEditorialFeed.vue 的注释）。
 */
import { nextTick } from 'vue'
import type { Router } from 'vue-router'

const NAV_SELECTOR = '[data-sa-nav]'
const DURATION = 460
const EASING = 'cubic-bezier(0.22, 1, 0.36, 1)'
/** 位移小于该值视为「没动」（例如两个非首页页面之间切换），不跑动画 */
const MIN_DELTA = 1.5

let fromRect: DOMRect | null = null
let runId = 0

function navRect(): DOMRect | null {
  const el = document.querySelector<HTMLElement>(NAV_SELECTOR)
  if (!el) return null
  const rect = el.getBoundingClientRect()
  return rect.width > 0 && rect.height > 0 ? rect : null
}

/** 等新页面的导航行真正挂载并完成布局（路由 chunk 异步加载，afterEach 时可能还没渲染） */
function waitForNav(timeout = 800): Promise<HTMLElement | null> {
  return new Promise((resolve) => {
    const started = performance.now()
    const tick = () => {
      const el = document.querySelector<HTMLElement>(NAV_SELECTOR)
      if (el && el.getBoundingClientRect().width > 0) return resolve(el)
      if (performance.now() - started > timeout) return resolve(null)
      requestAnimationFrame(tick)
    }
    tick()
  })
}

function reducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** 把新导航行从旧位置滑回自身位置 */
function flip(el: HTMLElement, from: DOMRect) {
  const to = el.getBoundingClientRect()
  const dx = from.left - to.left
  const dy = from.top - to.top
  if (Math.abs(dx) < MIN_DELTA && Math.abs(dy) < MIN_DELTA) return

  el.style.transition = 'none'
  el.style.transform = `translate(${dx}px, ${dy}px)`
  // 强制一次布局，让上面的初始位移先落地，否则过渡会被合并掉
  void el.offsetWidth

  requestAnimationFrame(() => {
    el.style.transition = `transform ${DURATION}ms ${EASING}`
    el.style.transform = ''
  })

  const clear = () => {
    el.style.transition = ''
    el.style.transform = ''
    el.removeEventListener('transitionend', clear)
  }
  el.addEventListener('transitionend', clear)
  // transitionend 在标签页隐藏/被打断时可能不触发，兜底清理
  window.setTimeout(clear, DURATION + 160)
}

export function installNavFlip(router: Router) {
  router.beforeEach(() => {
    // 离开前先记住当前导航行的位置（此时旧组件还在 DOM 里）
    fromRect = navRect()
  })

  router.afterEach(() => {
    const from = fromRect
    fromRect = null
    if (!from || reducedMotion()) return

    const id = ++runId
    void (async () => {
      await nextTick()
      const el = await waitForNav()
      // 期间又发生了一次导航，让最新那次接管
      if (!el || id !== runId) return
      flip(el, from)
    })()
  })
}
