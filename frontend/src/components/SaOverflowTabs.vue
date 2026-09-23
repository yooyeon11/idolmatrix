<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch, type Component } from 'vue'

/**
 * 横向标签条 —— HeroUI Tabs（primary 变体 +「溢出」行为）
 *
 * 类名与数值直接对齐 heroui v3 的 `tabs.css` / `tabs.tsx` / `scroll-shadow.css`：
 * - `.tabs__list-container`：relative + bg-default（这里映射 --sa-elevated）+ 圆角 `calc(--radius × 2.5)` = 20px
 * - `.tabs__list-container__scroller`：真正的滚动元素，ScrollShadow 用 `data-left-scroll` / `data-right-scroll`
 *   驱动两端 40px 渐隐边缘（mask-image）；滚动条隐藏
 * - `.tabs__list`：inline-flex、p-1(4px)、w-max、min-w-full —— 首个标签被 4px 内边距撑开与容器对齐
 * - `.tabs__tab`：h-8(32px)、px-4(16px)、rounded-3xl(24px)、text-sm(14px) font-medium、text-muted；
 *   选中 = `text-segment-foreground` + `.tabs__indicator`（absolute inset-0、bg-segment、z-index:-1、250ms 过渡）
 * - 溢出箭头 `.tabs__list-container__scroll-prev/-next`：size-4(16px)、start-1/end-1(4px)、bg-transparent、
 *   仅对应方向可滚动时显示（ScrollShadow 的 data 属性驱动，这里等价成 v-if）；hover opacity-70
 */
export interface SaOverflowTabItem {
  key: string
  label: string
  icon?: Component
}

const props = withDefaults(
  defineProps<{
    items: SaOverflowTabItem[]
    modelValue: string
    ariaLabel?: string
  }>(),
  { ariaLabel: '标签页' },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const scroller = ref<HTMLElement | null>(null)
const canLeft = ref(false)
const canRight = ref(false)

function update() {
  const el = scroller.value
  if (!el) return
  canLeft.value = el.scrollLeft > 1
  canRight.value = el.scrollLeft + el.clientWidth < el.scrollWidth - 1
}

function scrollByDir(dir: -1 | 1) {
  const el = scroller.value
  if (!el) return
  el.scrollBy({ left: dir * el.clientWidth * 0.85, behavior: 'smooth' })
}

function scrollToSelected() {
  const el = scroller.value
  if (!el) return
  const tab = el.querySelector<HTMLElement>('[data-selected="true"]')
  if (!tab) return
  const left = tab.offsetLeft
  const right = left + tab.offsetWidth
  if (left < el.scrollLeft) el.scrollTo({ left: Math.max(0, left - 8), behavior: 'smooth' })
  else if (right > el.scrollLeft + el.clientWidth) el.scrollTo({ left: right - el.clientWidth + 8, behavior: 'smooth' })
}

function select(key: string, ev?: KeyboardEvent) {
  emit('update:modelValue', key)
  if (ev) {
    // 键盘导航：焦点跟着选择走（React Aria Tabs 的行为）
    const el = scroller.value
    const tab = el?.querySelector<HTMLElement>(`[data-key="${key}"]`)
    tab?.focus()
  }
}

function onKeydown(ev: KeyboardEvent) {
  const keys = props.items.map((i) => i.key)
  const cur = keys.indexOf(props.modelValue)
  let next = -1
  if (ev.key === 'ArrowRight') next = Math.min(keys.length - 1, cur + 1)
  else if (ev.key === 'ArrowLeft') next = Math.max(0, cur - 1)
  else if (ev.key === 'Home') next = 0
  else if (ev.key === 'End') next = keys.length - 1
  if (next >= 0 && next !== cur) {
    ev.preventDefault()
    select(keys[next], ev)
  }
}

let ro: ResizeObserver | null = null
onMounted(() => {
  update()
  window.addEventListener('resize', update, { passive: true })
  if (typeof ResizeObserver !== 'undefined' && scroller.value) {
    ro = new ResizeObserver(update)
    ro.observe(scroller.value)
  }
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', update)
  ro?.disconnect()
})

watch(() => props.modelValue, () => nextTick(scrollToSelected))
watch(() => props.items, () => nextTick(() => { update(); scrollToSelected() }), { deep: true })
</script>

<template>
  <div class="tabs__list-container">
    <button
      v-if="canLeft"
      class="tabs__list-container__scroll-prev"
      type="button"
      :aria-label="`向前滚动${ariaLabel}`"
      @click="scrollByDir(-1)"
    >
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M15 18 9 12l6-6" />
      </svg>
    </button>

    <div
      ref="scroller"
      class="tabs__list-container__scroller"
      :data-left-scroll="canLeft ? 'true' : 'false'"
      :data-right-scroll="canRight ? 'true' : 'false'"
      @scroll.passive="update"
    >
      <div class="tabs__list" role="tablist" :aria-label="ariaLabel" @keydown="onKeydown">
        <button
          v-for="it in items"
          :key="it.key"
          class="tabs__tab"
          :class="{ 'tabs__tab--selected': modelValue === it.key }"
          type="button"
          role="tab"
          :aria-selected="modelValue === it.key"
          :data-selected="modelValue === it.key ? 'true' : 'false'"
          :data-key="it.key"
          :tabindex="modelValue === it.key ? 0 : -1"
          @click="select(it.key)"
        >
          <component :is="it.icon" v-if="it.icon" :size="16" />
          <span>{{ it.label }}</span>
          <span v-if="modelValue === it.key" class="tabs__indicator" />
        </button>
      </div>
    </div>

    <button
      v-if="canRight"
      class="tabs__list-container__scroll-next"
      type="button"
      :aria-label="`向后滚动${ariaLabel}`"
      @click="scrollByDir(1)"
    >
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <path d="m9 6 6 6-6 6" />
      </svg>
    </button>
  </div>
</template>

<style scoped>
/* ===== .tabs__list-container（HeroUI：relative bg-default rounded-[calc(--radius*2.5)]） ===== */
.tabs__list-container {
  position: relative;
  min-width: 0;
  max-width: 100%;
  background: var(--sa-elevated);
  border-radius: 20px; /* --radius(.5rem=8px) × 2.5 */
}

/* ===== 滚动元素（HeroUI ScrollShadow：40px 渐隐边缘，data-* 驱动） ===== */
.tabs__list-container__scroller {
  --scroll-shadow-size: 40px;
  overflow-x: auto;
  scrollbar-width: none;
  -webkit-overflow-scrolling: touch;
  border-radius: inherit;
}
.tabs__list-container__scroller::-webkit-scrollbar {
  display: none;
}
.tabs__list-container__scroller[data-left-scroll='true'] {
  -webkit-mask-image: linear-gradient(270deg, #000 calc(100% - var(--scroll-shadow-size)), transparent);
  mask-image: linear-gradient(270deg, #000 calc(100% - var(--scroll-shadow-size)), transparent);
}
.tabs__list-container__scroller[data-right-scroll='true'] {
  -webkit-mask-image: linear-gradient(90deg, #000 calc(100% - var(--scroll-shadow-size)), transparent);
  mask-image: linear-gradient(90deg, #000 calc(100% - var(--scroll-shadow-size)), transparent);
}
.tabs__list-container__scroller[data-left-scroll='true'][data-right-scroll='true'] {
  -webkit-mask-image: linear-gradient(90deg, transparent 0, #000 var(--scroll-shadow-size), #000 calc(100% - var(--scroll-shadow-size)), transparent 100%);
  mask-image: linear-gradient(90deg, transparent 0, #000 var(--scroll-shadow-size), #000 calc(100% - var(--scroll-shadow-size)), transparent 100%);
}

/* ===== .tabs__list（inline-flex p-1 w-max min-w-full） ===== */
.tabs__list {
  display: inline-flex;
  width: max-content;
  min-width: 100%;
  padding: 4px;
}

/* ===== .tabs__tab（h-8 px-4 rounded-3xl text-sm font-medium text-muted） ===== */
.tabs__tab {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 32px;
  padding: 0 16px;
  border: none;
  border-radius: 24px;
  background: transparent;
  color: var(--sa-text-secondary); /* text-muted */
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
  cursor: pointer;
  outline: none;
  transition: color 150ms, opacity 150ms;
}
.tabs__tab:hover {
  opacity: 0.7; /* HeroUI：hover 只降不透明度，不改底色 */
}
.tabs__tab:focus-visible {
  outline: 2px solid var(--sa-accent);
  outline-offset: 2px;
}
.tabs__tab--selected {
  color: var(--sa-text-primary); /* text-segment-foreground */
}
/* 选中标签里的 Tabs.Indicator：absolute inset-0、bg-segment、z-index:-1、250ms */
.tabs__indicator {
  position: absolute;
  inset: 0;
  z-index: -1;
  border-radius: 24px;
  background: var(--sa-subtle); /* bg-segment */
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1); /* shadow-sm */
  animation: tabs-indicator-in 250ms cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes tabs-indicator-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}
@media (prefers-reduced-motion: reduce) {
  .tabs__indicator {
    animation: none;
  }
}

/* ===== 溢出箭头（size-4、start-1/end-1、bg-transparent、仅可滚方向显示） ===== */
.tabs__list-container__scroll-prev,
.tabs__list-container__scroll-next {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  z-index: 2;
  width: 16px;
  height: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: none;
  border-radius: 9999px;
  background: transparent;
  color: var(--sa-text-primary);
  cursor: pointer;
  transition: opacity 150ms;
}
.tabs__list-container__scroll-prev:hover,
.tabs__list-container__scroll-next:hover {
  opacity: 0.7;
}
.tabs__list-container__scroll-prev {
  left: 4px; /* start-1 */
}
.tabs__list-container__scroll-next {
  right: 4px; /* end-1 */
}
</style>
