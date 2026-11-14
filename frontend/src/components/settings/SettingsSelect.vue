<script setup lang="ts">
/**
 * 空间玻璃下拉（空间磨砂样式）
 *
 * 用途：设置页里的枚举型字段（画质、编码、服务商…）。
 * 为什么不用 n-select：naive-ui 的下拉是白色实底 + 直角菜单，
 * 与设置页的「浮起卡片 + 磨砂层」语言不一致；这个组件是复刻设计稿的。
 *
 * 关键细节（别简化，简化就没质感了）：
 * 1. 触发框是 180° 渐变 + 12px 模糊，hover 才转纯白；
 * 2. 打开态给 3px 淡蓝环（不是边框变蓝就算）；
 * 3. 菜单 28px 模糊 + 四层阴影，入场 scale(.97)→1；
 * 4. 打开时把所在卡片抬到 z-index 40 —— 否则菜单会被下一行卡片盖住
 *    （卡片自身有 animation/transform，形成独立层叠上下文，
 *     菜单的 z-index 只在卡内有效）。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'

export interface SelectOption {
  value: string
  label: string
  /** 第二行说明，可省 */
  hint?: string
}

const props = withDefaults(
  defineProps<{
    modelValue: string
    options: SelectOption[]
    disabled?: boolean
    /** 菜单最小宽度，默认 240 */
    menuMinWidth?: number
  }>(),
  { disabled: false, menuMinWidth: 240 },
)

const emit = defineEmits<{ 'update:modelValue': [string] }>()

const rootEl = ref<HTMLElement | null>(null)
const open = ref(false)

const current = computed(
  () => props.options.find((o) => o.value === props.modelValue) ?? props.options[0],
)

/** 打开时抬升祖先卡片，关闭时还原 */
function raiseCard(on: boolean) {
  const card = rootEl.value?.closest('.sa-section, .panel, .im-card') as HTMLElement | null
  if (!card) return
  if (on) card.style.setProperty('z-index', '40')
  else card.style.removeProperty('z-index')
}

function toggle() {
  if (props.disabled) return
  open.value = !open.value
}

function pick(opt: SelectOption) {
  emit('update:modelValue', opt.value)
  open.value = false
}

function onDocClick(e: MouseEvent) {
  if (!open.value) return
  if (!rootEl.value?.contains(e.target as Node)) open.value = false
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') open.value = false
}

watch(open, (v) => {
  raiseCard(v)
  if (v) {
    document.addEventListener('click', onDocClick)
    document.addEventListener('keydown', onKey)
  } else {
    document.removeEventListener('click', onDocClick)
    document.removeEventListener('keydown', onKey)
  }
})

onBeforeUnmount(() => {
  raiseCard(false)
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div ref="rootEl" class="im-sel" :class="{ 'is-open': open, 'is-disabled': disabled }">
    <button
      type="button"
      class="im-sel__trigger"
      :aria-expanded="open"
      aria-haspopup="listbox"
      @click.stop="toggle"
    >
      <span class="im-sel__label">{{ current?.label ?? '未选择' }}</span>
      <svg class="im-sel__arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="m6 9 6 6 6-6" />
      </svg>
    </button>

    <div class="im-sel__menu" role="listbox" :style="{ minWidth: menuMinWidth + 'px' }">
      <div
        v-for="opt in options"
        :key="opt.value"
        class="im-sel__opt"
        :class="{ 'is-on': opt.value === modelValue }"
        role="option"
        :aria-selected="opt.value === modelValue"
        @click.stop="pick(opt)"
      >
        <div class="im-sel__opt-main">
          <span class="im-sel__opt-title">{{ opt.label }}</span>
          <span v-if="opt.hint" class="im-sel__opt-sub">{{ opt.hint }}</span>
        </div>
        <svg class="im-sel__check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4">
          <path d="m20 6-11 11-5-5" />
        </svg>
      </div>
    </div>
  </div>
</template>

<style scoped>
.im-sel {
  position: relative;
  width: 100%;
  user-select: none;
}

.im-sel__trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  height: 36px;
  padding: 0 12px 0 14px;
  border-radius: 11px;
  border: 1px solid var(--sa-border);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.95) 0%, rgba(246, 248, 251, 0.88) 100%);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03), inset 0 1px 0 rgba(255, 255, 255, 0.85);
  color: var(--sa-text-primary);
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
}
:global(html[data-theme='dark']) .im-sel__trigger {
  background: linear-gradient(180deg, rgba(40, 40, 52, 0.95) 0%, rgba(30, 30, 40, 0.9) 100%);
}
.im-sel__trigger:hover {
  background: var(--sa-elevated);
  border-color: color-mix(in oklab, var(--sa-text-primary) 16%, transparent);
  box-shadow: 0 3px 10px -2px rgba(15, 23, 42, 0.05), inset 0 1px 0 #fff;
}
.im-sel.is-open .im-sel__trigger {
  background: var(--sa-elevated);
  border-color: var(--sa-accent);
  box-shadow: 0 0 0 3px var(--sa-accent-subtle);
}
.im-sel.is-disabled .im-sel__trigger {
  opacity: 0.55;
  cursor: not-allowed;
}

.im-sel__label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.im-sel__arrow {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  color: var(--sa-text-tertiary);
  transition: transform 0.28s cubic-bezier(0.22, 1, 0.36, 1), color 0.2s ease;
}
.im-sel.is-open .im-sel__arrow {
  transform: rotate(180deg);
  color: var(--sa-accent);
}

.im-sel__menu {
  position: absolute;
  top: calc(100% + 7px);
  right: 0;
  left: 0;
  z-index: 250;
  padding: 6px;
  border-radius: 15px;
  background: var(--im-glass-bg, rgba(255, 255, 255, 0.76));
  backdrop-filter: blur(28px) saturate(190%);
  -webkit-backdrop-filter: blur(28px) saturate(190%);
  box-shadow:
    0 22px 48px -12px rgba(15, 23, 42, 0.16),
    0 4px 14px -2px rgba(15, 23, 42, 0.06),
    0 0 0 1px rgba(255, 255, 255, 0.85),
    0 0 0 1.5px rgba(15, 23, 42, 0.06);
  opacity: 0;
  visibility: hidden;
  transform: translateY(-6px) scale(0.97);
  transform-origin: top center;
  transition: opacity 0.2s ease, transform 0.26s cubic-bezier(0.22, 1, 0.36, 1), visibility 0.2s;
}
:global(html[data-theme='dark']) .im-sel__menu {
  --im-glass-bg: rgba(28, 28, 36, 0.82);
  box-shadow:
    0 22px 48px -12px rgba(0, 0, 0, 0.7),
    0 4px 14px -2px rgba(0, 0, 0, 0.4),
    0 0 0 1px rgba(255, 255, 255, 0.08),
    0 0 0 1.5px rgba(0, 0, 0, 0.5);
}
.im-sel.is-open .im-sel__menu {
  opacity: 1;
  visibility: visible;
  transform: translateY(0) scale(1);
}

.im-sel__opt {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 11px;
  border-radius: 10px;
  cursor: pointer;
  color: var(--sa-text-secondary);
  transition: background 0.15s ease;
}
.im-sel__opt + .im-sel__opt {
  margin-top: 2px;
}
.im-sel__opt:hover {
  background: color-mix(in oklab, var(--sa-text-primary) 4.5%, transparent);
}
.im-sel__opt.is-on {
  background: var(--sa-accent-subtle);
}

.im-sel__opt-main {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}
.im-sel__opt-title {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--sa-text-primary);
}
.im-sel__opt.is-on .im-sel__opt-title {
  color: var(--sa-accent);
  font-weight: 600;
}
.im-sel__opt-sub {
  font-size: 11px;
  color: var(--sa-text-tertiary);
  line-height: 1.5;
}

.im-sel__check {
  width: 15px;
  height: 15px;
  flex-shrink: 0;
  color: var(--sa-accent);
  opacity: 0;
  transform: scale(0.6);
  transition: opacity 0.2s cubic-bezier(0.22, 1, 0.36, 1), transform 0.2s cubic-bezier(0.22, 1, 0.36, 1);
}
.im-sel__opt.is-on .im-sel__check {
  opacity: 1;
  transform: scale(1);
}
</style>
