<script setup lang="ts">
/**
 * 按 HeroUI v3 DatePicker 外观复刻（非 React）：
 * - date-input-group：h-9、field-radius 12px、field-shadow、分段年/月/日
 * - date-picker__popover + calendar：圆角 overlay、年月切换、日格
 * 数值对齐 heroui packages/styles/components/{date-input-group,date-picker,calendar}.css
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue?: string | null
    disabled?: boolean
    clearable?: boolean
    placeholder?: string
    /** primary ≈ HeroUI default（投影字段）；secondary ≈ 灰底无投影；underline 仅资料行轻量用 */
    variant?: 'primary' | 'secondary' | 'bordered' | 'underline'
  }>(),
  {
    modelValue: null,
    disabled: false,
    clearable: true,
    placeholder: '年 / 月 / 日',
    variant: 'primary',
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: string | null]
}>()

const rootRef = ref<HTMLElement | null>(null)
const open = ref(false)
const yearMode = ref(false)
const viewYear = ref(new Date().getFullYear())
const viewMonth = ref(new Date().getMonth())

const yStr = ref('')
const mStr = ref('')
const dStr = ref('')

const yInput = ref<HTMLInputElement | null>(null)
const mInput = ref<HTMLInputElement | null>(null)
const dInput = ref<HTMLInputElement | null>(null)

const WEEK = ['日', '一', '二', '三', '四', '五', '六']

function pad2(n: number) {
  return String(n).padStart(2, '0')
}

function parseYmd(v: string | null | undefined): { y: number; m: number; d: number } | null {
  if (!v) return null
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v.trim())
  if (!m) return null
  const y = Number(m[1])
  const mo = Number(m[2])
  const d = Number(m[3])
  if (!y || mo < 1 || mo > 12 || d < 1 || d > 31) return null
  const dt = new Date(y, mo - 1, d)
  if (dt.getFullYear() !== y || dt.getMonth() !== mo - 1 || dt.getDate() !== d) return null
  return { y, m: mo, d }
}

function toYmd(y: number, m: number, d: number): string | null {
  if (!y || m < 1 || m > 12 || d < 1 || d > 31) return null
  const dt = new Date(y, m - 1, d)
  if (dt.getFullYear() !== y || dt.getMonth() !== m - 1 || dt.getDate() !== d) return null
  return `${y}-${pad2(m)}-${pad2(d)}`
}

function syncFromModel(v: string | null | undefined) {
  const p = parseYmd(v)
  if (!p) {
    yStr.value = ''
    mStr.value = ''
    dStr.value = ''
    return
  }
  yStr.value = String(p.y)
  mStr.value = pad2(p.m)
  dStr.value = pad2(p.d)
  viewYear.value = p.y
  viewMonth.value = p.m - 1
}

watch(
  () => props.modelValue,
  (v) => syncFromModel(v),
  { immediate: true },
)

const hasValue = computed(() => !!(yStr.value || mStr.value || dStr.value))

const visualVariant = computed(() => {
  // bordered 映射到 HeroUI primary 字段外观（文档里常见圆角输入组）
  if (props.variant === 'bordered') return 'primary'
  return props.variant
})

function commitSegments() {
  const y = Number(yStr.value)
  const m = Number(mStr.value)
  const d = Number(dStr.value)
  if (!yStr.value && !mStr.value && !dStr.value) {
    emit('update:modelValue', null)
    return
  }
  if (yStr.value.length === 4 && mStr.value.length >= 1 && dStr.value.length >= 1) {
    const next = toYmd(y, m, d)
    if (next) {
      mStr.value = pad2(m)
      dStr.value = pad2(d)
      emit('update:modelValue', next)
      viewYear.value = y
      viewMonth.value = m - 1
    }
  }
}

function onYInput(ev: Event) {
  const el = ev.target as HTMLInputElement
  yStr.value = el.value.replace(/\D/g, '').slice(0, 4)
  el.value = yStr.value
  if (yStr.value.length === 4) {
    mInput.value?.focus()
    mInput.value?.select()
  }
  commitSegments()
}

function onMInput(ev: Event) {
  const el = ev.target as HTMLInputElement
  mStr.value = el.value.replace(/\D/g, '').slice(0, 2)
  el.value = mStr.value
  if (mStr.value.length === 2 || (mStr.value.length === 1 && Number(mStr.value) > 1)) {
    dInput.value?.focus()
    dInput.value?.select()
  }
  commitSegments()
}

function onDInput(ev: Event) {
  const el = ev.target as HTMLInputElement
  dStr.value = el.value.replace(/\D/g, '').slice(0, 2)
  el.value = dStr.value
  commitSegments()
}

function onSegKeydown(which: 'y' | 'm' | 'd', ev: KeyboardEvent) {
  if (ev.key === 'ArrowLeft') {
    ev.preventDefault()
    if (which === 'm') yInput.value?.focus()
    if (which === 'd') mInput.value?.focus()
  } else if (ev.key === 'ArrowRight') {
    ev.preventDefault()
    if (which === 'y') mInput.value?.focus()
    if (which === 'm') dInput.value?.focus()
  } else if (ev.key === 'Backspace') {
    const el = ev.target as HTMLInputElement
    if (!el.value) {
      ev.preventDefault()
      if (which === 'm') {
        yInput.value?.focus()
        yInput.value?.select()
      }
      if (which === 'd') {
        mInput.value?.focus()
        mInput.value?.select()
      }
    }
  } else if (ev.key === 'Enter') {
    toggleOpen()
  }
}

function onSegFocus(ev: Event) {
  ;(ev.target as HTMLInputElement).select()
}

const selected = computed(() => parseYmd(props.modelValue))

const headerLabel = computed(() => `${viewYear.value}年 ${viewMonth.value + 1}月`)

function daysInMonth(y: number, m0: number) {
  return new Date(y, m0 + 1, 0).getDate()
}

type DayCell = { key: string; y: number; m: number; d: number; inMonth: boolean; day: number }

const dayCells = computed(() => {
  const y = viewYear.value
  const m0 = viewMonth.value
  const first = new Date(y, m0, 1)
  const startPad = first.getDay()
  const dim = daysInMonth(y, m0)
  const prevDim = daysInMonth(y, m0 - 1)
  const cells: DayCell[] = []
  for (let i = 0; i < startPad; i++) {
    const day = prevDim - startPad + 1 + i
    const dt = new Date(y, m0 - 1, day)
    cells.push({
      key: `p-${day}`,
      y: dt.getFullYear(),
      m: dt.getMonth() + 1,
      d: day,
      inMonth: false,
      day,
    })
  }
  for (let d = 1; d <= dim; d++) {
    cells.push({ key: `c-${d}`, y, m: m0 + 1, d, inMonth: true, day: d })
  }
  let n = 1
  while (cells.length % 7 !== 0 || cells.length < 42) {
    const dt = new Date(y, m0 + 1, n)
    cells.push({
      key: `n-${n}`,
      y: dt.getFullYear(),
      m: dt.getMonth() + 1,
      d: n,
      inMonth: false,
      day: n,
    })
    n++
    if (cells.length >= 42) break
  }
  return cells
})

function isSelected(c: DayCell) {
  const s = selected.value
  return !!(s && s.y === c.y && s.m === c.m && s.d === c.d)
}

function isToday(c: DayCell) {
  const t = new Date()
  return c.y === t.getFullYear() && c.m === t.getMonth() + 1 && c.d === t.getDate()
}

function prevMonth() {
  if (viewMonth.value === 0) {
    viewMonth.value = 11
    viewYear.value -= 1
  } else viewMonth.value -= 1
}

function nextMonth() {
  if (viewMonth.value === 11) {
    viewMonth.value = 0
    viewYear.value += 1
  } else viewMonth.value += 1
}

function pickDay(c: DayCell) {
  if (props.disabled) return
  const next = toYmd(c.y, c.m, c.d)
  if (!next) return
  emit('update:modelValue', next)
  syncFromModel(next)
  close()
}

const yearStart = computed(() => Math.floor(viewYear.value / 12) * 12)
const yearCells = computed(() => {
  const start = yearStart.value
  return Array.from({ length: 12 }, (_, i) => start + i)
})

function pickYear(y: number) {
  viewYear.value = y
  yearMode.value = false
}

function close() {
  open.value = false
  yearMode.value = false
}

function toggleOpen() {
  if (props.disabled) return
  open.value = !open.value
  if (open.value) {
    const p = parseYmd(props.modelValue)
    if (p) {
      viewYear.value = p.y
      viewMonth.value = p.m - 1
    }
    yearMode.value = false
  }
}

function clear() {
  if (props.disabled) return
  emit('update:modelValue', null)
  syncFromModel(null)
  close()
  nextTick(() => yInput.value?.focus())
}

function focusField() {
  if (props.disabled) return
  yInput.value?.focus()
}

function onDocPointer(ev: PointerEvent) {
  if (!open.value) return
  const el = rootRef.value
  if (el && !el.contains(ev.target as Node)) close()
}

onMounted(() => document.addEventListener('pointerdown', onDocPointer, true))
onBeforeUnmount(() => document.removeEventListener('pointerdown', onDocPointer, true))
</script>

<template>
  <div
    ref="rootRef"
    class="sa-dp"
    :class="[`is-${visualVariant}`, { 'is-open': open, 'is-disabled': disabled, 'has-value': hasValue }]"
  >
    <div
      class="sa-dp-field"
      role="group"
      :aria-disabled="disabled || undefined"
      :aria-label="placeholder"
      @click="focusField"
    >
      <div class="sa-dp-input">
        <input
          ref="yInput"
          class="sa-dp-seg sa-dp-y"
          inputmode="numeric"
          maxlength="4"
          :disabled="disabled"
          :value="yStr"
          aria-label="年"
          placeholder="年"
          @input="onYInput"
          @keydown="onSegKeydown('y', $event)"
          @focus="onSegFocus"
        />
        <span class="sa-dp-lit" aria-hidden="true">/</span>
        <input
          ref="mInput"
          class="sa-dp-seg sa-dp-m"
          inputmode="numeric"
          maxlength="2"
          :disabled="disabled"
          :value="mStr"
          aria-label="月"
          placeholder="月"
          @input="onMInput"
          @keydown="onSegKeydown('m', $event)"
          @focus="onSegFocus"
        />
        <span class="sa-dp-lit" aria-hidden="true">/</span>
        <input
          ref="dInput"
          class="sa-dp-seg sa-dp-d"
          inputmode="numeric"
          maxlength="2"
          :disabled="disabled"
          :value="dStr"
          aria-label="日"
          placeholder="日"
          @input="onDInput"
          @keydown="onSegKeydown('d', $event)"
          @focus="onSegFocus"
        />
      </div>

      <div class="sa-dp-suffix">
        <button
          v-if="clearable && hasValue && !disabled"
          type="button"
          class="sa-dp-clear"
          aria-label="清除"
          @click.stop="clear"
        >
          ×
        </button>
        <button
          type="button"
          class="sa-dp-trigger"
          data-slot="date-picker-trigger"
          :disabled="disabled"
          aria-label="打开日历"
          :aria-expanded="open"
          @click.stop="toggleOpen"
        >
          <svg class="sa-dp-trigger-ico" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <rect x="3.5" y="5.5" width="17" height="15" rx="2.5" stroke="currentColor" stroke-width="1.5" />
            <path d="M3.5 9.5h17" stroke="currentColor" stroke-width="1.5" />
            <path d="M8 3.5v3M16 3.5v3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
          </svg>
        </button>
      </div>
    </div>

    <div v-if="open" class="sa-dp-popover" role="dialog" aria-label="选择日期">
      <div class="sa-cal">
        <div class="sa-cal-header">
          <button type="button" class="sa-cal-year-trigger" @click="yearMode = !yearMode">
            <span>{{ yearMode ? `${yearStart} – ${yearStart + 11}` : headerLabel }}</span>
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
              <path
                d="M3 4.5L6 7.5L9 4.5"
                stroke="currentColor"
                stroke-width="1.5"
                fill="none"
                stroke-linecap="round"
              />
            </svg>
          </button>
          <div class="sa-cal-nav" :class="{ hidden: yearMode }">
            <button type="button" class="sa-cal-nav-btn" aria-label="上一个月" @click="prevMonth()">
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                <path d="M10 3L5 8l5 5" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" />
              </svg>
            </button>
            <button type="button" class="sa-cal-nav-btn" aria-label="下一个月" @click="nextMonth()">
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                <path d="M6 3l5 5-5 5" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" />
              </svg>
            </button>
          </div>
          <div v-if="yearMode" class="sa-cal-nav">
            <button type="button" class="sa-cal-nav-btn" aria-label="上一组年份" @click="viewYear -= 12">
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                <path d="M10 3L5 8l5 5" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" />
              </svg>
            </button>
            <button type="button" class="sa-cal-nav-btn" aria-label="下一组年份" @click="viewYear += 12">
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                <path d="M6 3l5 5-5 5" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" />
              </svg>
            </button>
          </div>
        </div>

        <div v-if="yearMode" class="sa-year-grid">
          <button
            v-for="y in yearCells"
            :key="y"
            type="button"
            class="sa-year-cell"
            :class="{ on: y === viewYear, sel: selected?.y === y }"
            @click="pickYear(y)"
          >
            {{ y }}
          </button>
        </div>

        <template v-else>
          <div class="sa-week">
            <span v-for="w in WEEK" :key="w">{{ w }}</span>
          </div>
          <div class="sa-day-grid">
            <button
              v-for="c in dayCells"
              :key="c.key"
              type="button"
              class="sa-day"
              :class="{ mute: !c.inMonth, on: isSelected(c), today: isToday(c) }"
              @click="pickDay(c)"
            >
              {{ c.day }}
            </button>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* —— date-input-group —— */
.sa-dp {
  position: relative;
  width: 100%;
  font-family: inherit;
}

.sa-dp-field {
  display: inline-flex;
  align-items: center;
  width: 100%;
  height: 36px; /* h-9 */
  box-sizing: border-box;
  overflow: hidden;
  background: var(--sa-elevated, #fff);
  color: var(--sa-text-primary, #1f2329);
  border: 1px solid var(--sa-border, rgba(15, 23, 42, 0.12));
  border-radius: 12px; /* --field-radius */
  box-shadow:
    0 2px 4px rgba(0, 0, 0, 0.04),
    0 1px 2px rgba(0, 0, 0, 0.06),
    0 0 1px rgba(0, 0, 0, 0.06); /* --field-shadow */
  outline: none;
  cursor: text;
  transition:
    background-color 150ms ease,
    border-color 150ms ease,
    box-shadow 150ms ease;
}

.sa-dp.is-secondary .sa-dp-field {
  box-shadow: none;
  background: var(--sa-subtle, #eef0f4);
  border-color: transparent;
}

.sa-dp.is-secondary .sa-dp-field:hover {
  background: var(--sa-hover, #e6e9f0);
}

.sa-dp-field:hover {
  background: color-mix(in srgb, var(--sa-elevated, #fff) 94%, var(--sa-text-primary, #1f2329) 6%);
}

.sa-dp.is-open .sa-dp-field,
.sa-dp-field:focus-within {
  /* 中性聚焦，对齐项目里 Select / AI 设置字段（非紫色光晕） */
  border-color: color-mix(in srgb, var(--sa-text-primary, #1f2329) 30%, transparent);
  box-shadow:
    0 2px 4px rgba(0, 0, 0, 0.04),
    0 1px 2px rgba(0, 0, 0, 0.06),
    0 0 1px rgba(0, 0, 0, 0.06);
}

.sa-dp.is-disabled .sa-dp-field {
  opacity: 0.5;
  cursor: not-allowed;
  pointer-events: none;
}

html[data-theme='dark'] .sa-dp-field {
  box-shadow: none;
}

/* underline：资料行轻量 */
.sa-dp.is-underline .sa-dp-field {
  height: 32px;
  background: transparent;
  border: none;
  border-bottom: 1px dashed var(--sa-border, rgba(15, 23, 42, 0.12));
  border-radius: 0;
  box-shadow: none;
}
.sa-dp.is-underline .sa-dp-field:hover {
  background: transparent;
}
.sa-dp.is-underline.is-open .sa-dp-field,
.sa-dp.is-underline .sa-dp-field:focus-within {
  border-bottom-color: var(--sa-accent, #0485f7);
  box-shadow: none;
}

.sa-dp-input {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 1px;
  min-width: 0;
  padding: 8px 8px 8px 12px; /* px-3 py-2 近似 */
  font-variant-numeric: tabular-nums;
}

.sa-dp-seg {
  display: inline-block;
  border: none;
  outline: none;
  background: transparent;
  color: var(--sa-text-primary, #1f2329);
  font: inherit;
  font-size: 13px; /* 与资料库字段口径一致（.sa-input / .sa-select = 13px） */
  font-weight: 400;
  line-height: 1.2;
  padding: 0 2px;
  text-align: end;
  border-radius: 6px; /* rounded-md */
  box-sizing: content-box;
}
.sa-dp-seg::placeholder {
  color: var(--sa-text-tertiary, #77818f); /* field-placeholder */
}
.sa-dp-seg:focus {
  background: var(--sa-accent-subtle, rgba(109, 92, 224, 0.1)); /* accent-soft */
  color: color-mix(in srgb, var(--sa-accent, #0485f7) 70%, var(--sa-text-primary, #1f2329) 30%);
}
.sa-dp-y {
  width: 4.2ch;
}
.sa-dp-m,
.sa-dp-d {
  width: 2.2ch;
}

.sa-dp-lit {
  color: var(--sa-text-tertiary, #77818f);
  font-size: 13px;
  user-select: none;
  padding: 0;
}

.sa-dp-suffix {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  gap: 2px;
  padding-right: 8px; /* me-2~3 */
  color: var(--sa-text-tertiary, #77818f);
}

.sa-dp-trigger,
.sa-dp-clear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 4px; /* p-1 */
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--sa-text-tertiary, #77818f);
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
}
.sa-dp-trigger-ico {
  width: 16px; /* size-4 */
  height: 16px;
}
.sa-dp-trigger:hover,
.sa-dp-clear:hover {
  background: var(--sa-hover, #e6e9f0);
  color: var(--sa-text-primary, #1f2329);
}
.sa-dp-trigger:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

/* —— date-picker__popover —— */
.sa-dp-popover {
  position: absolute;
  z-index: 80;
  top: calc(100% + 8px);
  left: 0;
  width: fit-content;
  padding: 12px; /* p-3 */
  box-sizing: border-box;
  background: var(--sa-elevated, #fff);
  border-radius: 20px; /* min(32px, radius*2.5) with radius=8 */
  box-shadow:
    0 2px 8px rgba(0, 0, 0, 0.06),
    0 -6px 12px rgba(0, 0, 0, 0.03),
    0 14px 28px rgba(0, 0, 0, 0.08); /* --overlay-shadow */
}

.sa-cal {
  width: 252px; /* w-63 */
  max-width: 252px;
}

.sa-cal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 2px 16px; /* px-0.5 pb-4 */
}

.sa-cal-year-trigger {
  display: inline-flex;
  flex: 1;
  align-items: center;
  gap: 6px;
  border: none;
  background: transparent;
  color: var(--sa-text-primary, #1f2329);
  font: inherit;
  font-size: 14px; /* text-sm */
  font-weight: 500;
  cursor: pointer;
  border-radius: 8px;
  padding: 2px 4px;
  text-align: left;
}
.sa-cal-year-trigger:hover {
  background: var(--sa-hover, #e6e9f0);
}

.sa-cal-nav {
  display: flex;
  gap: 2px;
}
.sa-cal-nav.hidden {
  visibility: hidden;
  pointer-events: none;
}
.sa-cal-nav-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px; /* size-6 */
  height: 24px;
  border: none;
  border-radius: 16px; /* rounded-2xl */
  background: transparent;
  color: color-mix(in srgb, var(--sa-accent, #0485f7) 70%, var(--sa-text-primary, #1f2329) 30%);
  cursor: pointer;
  padding: 0;
}
.sa-cal-nav-btn:hover {
  background: var(--sa-subtle, #eef0f4);
}

.sa-week {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  width: 100%;
}
.sa-week span {
  display: flex;
  align-items: center;
  justify-content: center;
  padding-bottom: 8px;
  font-size: 12px;
  font-weight: 500;
  color: var(--sa-text-tertiary, #77818f);
}

.sa-day-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  width: 100%;
}
.sa-year-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
  padding: 4px 0 2px;
}

.sa-day {
  position: relative;
  display: flex;
  aspect-ratio: 1;
  width: 100%;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: var(--sa-text-primary, #1f2329);
  font: inherit;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  border-radius: 24px; /* rounded-3xl */
  outline: none;
  transition: transform 150ms ease, background-color 100ms ease;
}
.sa-day:hover:not(.on) {
  background: var(--sa-subtle, #eef0f4);
}
.sa-day.mute {
  color: var(--sa-text-tertiary, #77818f);
  opacity: 0.5;
}
.sa-day.today:not(.on) {
  background: var(--sa-accent-subtle, rgba(109, 92, 224, 0.1));
  color: color-mix(in srgb, var(--sa-accent, #0485f7) 70%, var(--sa-text-primary, #1f2329) 30%);
}
.sa-day.on {
  background: var(--sa-accent, #0485f7);
  color: #fff;
}
.sa-day.on:hover {
  background: var(--sa-accent-hover, #3592f9);
  color: #fff;
}

.sa-year-cell {
  border: none;
  background: transparent;
  color: var(--sa-text-primary, #1f2329);
  font: inherit;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  min-height: 40px;
  border-radius: 12px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.sa-year-cell:hover {
  background: var(--sa-subtle, #eef0f4);
}
.sa-year-cell.on,
.sa-year-cell.sel {
  background: var(--sa-accent, #0485f7);
  color: #fff;
}
</style>
