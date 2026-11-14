<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

/**
 * 流光空间玻璃下拉（体系 III / Spatial Glass）
 *
 * 全站统一的下拉组件：组合列表、艺人列表、视频详情页场次切换都用它，
 * 保证「一颗胶囊触发器 + 24px 毛玻璃浮层 + 12px 圆角选项」这一套语言一致。
 *
 * - 触发器：圆角全圆，solid（白底黑字，主操作）/ ghost（微光描边，次操作）两态
 * - 浮层：`backdrop-filter: blur(24px) saturate(1.4)` + 1px 微光描边 + 深投影
 * - 选项：默认透明，悬停浅底，选中「反相」（白底黑字 / 深色主题下同样反相）
 */
export interface GlassOption {
  label: string
  value: string | number | null
  /** 右侧次要说明（等宽小字） */
  hint?: string
}

const props = withDefaults(
  defineProps<{
    modelValue: string | number | null
    options: GlassOption[]
    /** 触发器前缀，如「排序」「范围」 */
    prefix?: string
    /** solid = 白底黑字主操作；ghost = 微光描边次操作 */
    variant?: 'solid' | 'ghost'
    /** 浮层对齐方向 */
    align?: 'left' | 'right'
    /** 浮层宽度 */
    width?: string
    /** 无障碍标签 */
    ariaLabel?: string
  }>(),
  { prefix: '', variant: 'ghost', align: 'left', width: '14rem', ariaLabel: '' },
)

const emit = defineEmits<{ (e: 'update:modelValue', v: string | number | null): void }>()

const open = ref(false)
const root = ref<HTMLElement | null>(null)
const current = computed(
  () => props.options.find((o) => o.value === props.modelValue) ?? props.options[0] ?? null,
)

/**
 * ⚠ 触发器**不能**写 @click.stop：每个实例都靠 document 的 click 判断「点到外面了」，
 * 一旦 stop 冒泡，另一实例的监听收不到事件 → 点第二个下拉时第一个不会收起（业主 2026-11-14 报障）。
 * 自身触发器的点击由 onDocClick 里的 root.contains 判断排除，不会误关。
 */
function toggle() {
  open.value = !open.value
}
function pick(o: GlassOption) {
  open.value = false
  if (o.value !== props.modelValue) emit('update:modelValue', o.value)
}
/**
 * ≤768px 走底部抽屉。
 * ⚠ 不能退回原生 <select>：vivo / OPPO / 小米等内核不认 `appearance: none`，
 *   会渲染成系统默认白底控件（业主 2026-11-13 反馈「特别丑的白色选择框」）。
 */
const isNarrow = ref(false)
let narrowMq: MediaQueryList | null = null
function syncNarrow(e: { matches: boolean }) {
  isNarrow.value = e.matches
}
/** 抽屉标题：优先 prefix，其次 ariaLabel */
const sheetTitle = computed(() => props.prefix || props.ariaLabel || '选择')

function onDocClick(e: MouseEvent) {
  if (!open.value) return
  // 窄屏是 teleport 出去的抽屉，点击它不在 root 内，交给遮罩自己处理
  if (isNarrow.value) return
  if (!root.value?.contains(e.target as Node)) open.value = false
}
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') open.value = false
}

watch([open, isNarrow], ([o, narrow]) => {
  document.body.style.overflow = o && narrow ? 'hidden' : ''
})

onMounted(() => {
  narrowMq = window.matchMedia('(max-width: 768px)')
  isNarrow.value = narrowMq.matches
  narrowMq.addEventListener('change', syncNarrow)
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  narrowMq?.removeEventListener('change', syncNarrow)
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = ''
})
</script>

<template>
  <div ref="root" class="gs" :class="{ 'is-open': open }">
    <button
      type="button"
      class="gs-trigger"
      :class="[`gs-trigger--${variant}`, { 'is-open': open }]"
      :aria-label="ariaLabel || prefix || '下拉选择'"
      :aria-expanded="open"
      aria-haspopup="listbox"
      @click="toggle"
    >
      <span v-if="prefix" class="gs-prefix">{{ prefix }}:</span>
      <strong class="gs-value">{{ current?.label ?? '未选择' }}</strong>
      <svg class="gs-chevron" width="10" height="6" viewBox="0 0 10 6" fill="none" aria-hidden="true">
        <path d="M1 1L5 5L9 1" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </button>

    <Transition name="gs-pop">
      <div
        v-if="open && !isNarrow"
        class="gs-panel"
        :class="`gs-panel--${align}`"
        :style="{ minWidth: width }"
        role="listbox"
        @click.stop
      >
        <button
          v-for="o in options"
          :key="String(o.value)"
          type="button"
          class="gs-opt"
          :class="{ 'is-active': o.value === modelValue }"
          role="option"
          :aria-selected="o.value === modelValue"
          @click="pick(o)"
        >
          <span class="gs-opt-main">
            <span class="gs-opt-label">{{ o.label }}</span>
            <span v-if="o.hint" class="gs-opt-hint">{{ o.hint }}</span>
          </span>
          <svg
            v-if="o.value === modelValue"
            class="gs-opt-check"
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            aria-hidden="true"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.6" d="M20 6L9 17l-5-5" />
          </svg>
        </button>
      </div>
    </Transition>

    <!-- 移动端：底部抽屉（不用原生 select，避免被内核画成系统白框） -->
    <Teleport to="body">
      <Transition name="gs-sheet">
        <div v-if="open && isNarrow" class="gs-scrim" @click="open = false">
          <div class="gs-sheet" role="dialog" aria-modal="true" :aria-label="sheetTitle" @click.stop>
            <div class="gs-sheet-head">
              <span class="gs-sheet-title">{{ sheetTitle }}</span>
              <button type="button" class="gs-sheet-close" aria-label="关闭" @click="open = false">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.4" d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </div>
            <div class="gs-sheet-list">
              <button
                v-for="o in options"
                :key="`s-${String(o.value)}`"
                type="button"
                class="gs-sheet-opt"
                :class="{ 'is-active': o.value === modelValue }"
                @click="pick(o)"
              >
                <span class="gs-sheet-label">{{ o.label }}</span>
                <span v-if="o.hint" class="gs-sheet-hint">{{ o.hint }}</span>
                <svg
                  v-if="o.value === modelValue"
                  class="gs-sheet-check"
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  aria-hidden="true"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.6" d="M20 6L9 17l-5-5" />
                </svg>
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.gs {
  position: relative;
  display: inline-flex;
}

/* ===== 触发器：无边框文字按钮（= 浏览页 .pop-btn） =====
   之前是「自带描边 + 灰底的独立胶囊」，与浏览页那颗大胶囊里的无框按钮并排时，
   一个像输入框、一个像链接。统一后触发器自身不画框，靠 SaFilterBar 提供容器。 */
.gs-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  /* 盒模型逐字对齐浏览页 .pop-btn（6px 13px + 12px 字 → 实测 31px 高），
     SaFilterBar 靠容器内最高的搜索框撑到 44px，两者同高 */
  padding: 6px 13px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: var(--sa-text-secondary);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
  transition: background-color 0.18s ease, color 0.18s ease;
}
.gs-trigger:hover {
  background: var(--sa-hover);
  color: var(--sa-text-primary);
}
.gs-trigger.is-open {
  background: var(--sa-hover);
  color: var(--sa-text-primary);
}
/* solid 备用：反相按钮（当前无页面使用，保留以免破坏旧调用） */
.gs-trigger--solid {
  background: var(--sa-text-primary);
  color: var(--sa-bg);
}
.gs-trigger--solid:hover {
  background: var(--sa-text-primary);
  color: var(--sa-bg);
}
.gs-prefix {
  color: var(--sa-text-secondary);
  font-size: 12px;
  font-weight: 400;
  letter-spacing: 0;
  opacity: 1;
}
.gs-value {
  font-weight: 600;
  color: var(--sa-text-primary);
  white-space: nowrap;
}
.gs-trigger--solid .gs-value,
.gs-trigger--solid .gs-prefix {
  color: var(--sa-bg);
  opacity: 0.9;
}
.gs-chevron {
  flex-shrink: 0;
  color: var(--sa-text-tertiary);
  transition: transform 0.2s ease;
}
.gs.is-open .gs-chevron {
  transform: rotate(180deg);
}

/* ===== 浮层：与浏览页 pop-menu 同规格 ===== */
.gs-panel {
  position: absolute;
  top: calc(100% + 8px);
  z-index: 60;
  padding: 10px;
  border-radius: 16px;
  border: 1px solid var(--sa-border);
  background: var(--sa-elevated);
  box-shadow:
    0 12px 32px -4px rgba(0, 0, 0, 0.35),
    0 4px 12px -2px rgba(0, 0, 0, 0.18);
  transform-origin: top center;
}
.gs-panel--left {
  left: 0;
}
.gs-panel--right {
  right: 0;
}

.gs-opt {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: 10px;
  background: none;
  color: var(--sa-text-secondary);
  font-family: inherit;
  font-size: 12.5px;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.16s ease, color 0.16s ease;
}
.gs-opt:hover {
  background: var(--sa-hover);
  color: var(--sa-text-primary);
}
/* 选中：不反相，只加粗 + 右侧对勾（浏览页口径） */
.gs-opt.is-active {
  color: var(--sa-text-primary);
  font-weight: 600;
}
.gs-opt-main {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.gs-opt-label {
  white-space: nowrap;
}
.gs-opt-hint {
  font-family: inherit;
  font-size: 11px;
  color: var(--sa-text-tertiary);
  white-space: nowrap;
}
.gs-opt-check {
  flex: none;
  color: var(--sa-text-primary);
}

/* 弹簧式展开 */
.gs-pop-enter-active,
.gs-pop-leave-active {
  transition:
    opacity 0.22s cubic-bezier(0.19, 1, 0.22, 1),
    transform 0.3s cubic-bezier(0.19, 1, 0.22, 1);
}
.gs-pop-enter-from,
.gs-pop-leave-to {
  opacity: 0;
  transform: translateY(-6px) scale(0.97);
}

/* ===== 移动端：底部抽屉 ===== */
.gs-scrim {
  position: fixed;
  inset: 0;
  z-index: 320;
  display: flex;
  align-items: flex-end;
  background: rgba(10, 12, 16, 0.44);
}
.gs-sheet {
  width: 100%;
  max-height: 72vh;
  display: flex;
  flex-direction: column;
  padding: 12px 12px calc(12px + env(safe-area-inset-bottom, 0px));
  border-radius: 20px 20px 0 0;
  background: var(--sa-elevated);
  box-shadow: 0 -20px 56px -14px rgba(0, 0, 0, 0.45);
}
.gs-sheet-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 2px 4px 10px;
}
.gs-sheet-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--sa-text-primary);
}
.gs-sheet-close {
  margin-left: auto;
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 999px;
  background: var(--sa-subtle);
  color: var(--sa-text-secondary);
  cursor: pointer;
}
.gs-sheet-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}
.gs-sheet-opt {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 12px 10px;
  border: 0;
  border-radius: 12px;
  background: none;
  color: var(--sa-text-secondary);
  font-family: inherit;
  font-size: 14px;
  text-align: left;
  cursor: pointer;
}
/* 选中态与桌面浮层同口径：不铺底色，只加粗 + 主色（右侧已有对勾） */
.gs-sheet-opt.is-active {
  color: var(--sa-text-primary);
  font-weight: 700;
}
.gs-sheet-label {
  flex: 1;
  min-width: 0;
}
.gs-sheet-hint {
  flex: none;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  opacity: 0.62;
}
.gs-sheet-check {
  flex: none;
  color: var(--sa-text-primary);
}

.gs-sheet-enter-active,
.gs-sheet-leave-active {
  transition: opacity 0.22s ease;
}
.gs-sheet-enter-active .gs-sheet,
.gs-sheet-leave-active .gs-sheet {
  transition: transform 0.3s cubic-bezier(0.19, 1, 0.22, 1);
}
.gs-sheet-enter-from,
.gs-sheet-leave-to {
  opacity: 0;
}
.gs-sheet-enter-from .gs-sheet,
.gs-sheet-leave-to .gs-sheet {
  transform: translateY(100%);
}

/* 窄屏：控件仍按内容宽排，让「范围 / 排序 / 检索」塞进同一颗胶囊。
   前缀保留（"范围: 全部" 比只写 "全部" 更容易读懂），靠隐藏箭头的做法省宽度 */
@media (max-width: 768px) {
  .gs-trigger {
    padding: 6px 9px;
    gap: 4px;
    font-size: 11.5px;
  }
  .gs-chevron {
    width: 9px;
    height: 5px;
  }
  .gs-prefix {
    font-size: 11.5px;
  }
}
</style>
