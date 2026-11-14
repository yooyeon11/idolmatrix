<script setup lang="ts">
/**
 * 舞台标签栏（Stage Rail）——吸顶胶囊导航，白色滑块随选中项滑动。
 *
 * 与 GroupDetailView 的 `.gp-rail` 同一套实现（白胶囊 + 弹簧位移），
 * 抽出来供艺人详情页复用；两页以后要改滑动手感只需动这里。
 */
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{
  items: { key: string; label: string }[]
  modelValue: string
  /** 左侧焦点说明（可选） */
  focusLabel?: string
  focusValue?: string
}>()

const emit = defineEmits<{ (e: 'update:modelValue', v: string): void }>()

const trackEl = ref<HTMLElement | null>(null)
const pillStyle = ref<Record<string, string>>({ opacity: '0' })

function measure() {
  const track = trackEl.value
  if (!track) return
  const idx = props.items.findIndex((i) => i.key === props.modelValue)
  const btn = track.querySelectorAll<HTMLElement>('.stage-rail-btn')[idx]
  if (!btn) {
    pillStyle.value = { opacity: '0' }
    return
  }
  pillStyle.value = {
    opacity: '1',
    transform: `translateX(${btn.offsetLeft - 4}px)`,
    width: `${btn.offsetWidth}px`,
  }
}

watch(
  () => props.modelValue,
  () => void nextTick(measure),
)
watch(
  () => props.items.length,
  () => void nextTick(measure),
)

let ro: ResizeObserver | null = null
onMounted(() => {
  void nextTick(measure)
  if (typeof ResizeObserver !== 'undefined' && trackEl.value) {
    ro = new ResizeObserver(() => measure())
    ro.observe(trackEl.value)
  }
  window.addEventListener('resize', measure)
})
onBeforeUnmount(() => {
  ro?.disconnect()
  window.removeEventListener('resize', measure)
})
</script>

<template>
  <div class="stage-rail">
    <div v-if="focusLabel || focusValue" class="stage-rail-focus">
      <span class="stage-dot" />
      <strong>{{ focusValue }}</strong>
      <span>{{ focusLabel }}</span>
    </div>

    <div class="stage-rail-scroll">
      <div ref="trackEl" class="stage-rail-track">
        <span class="stage-rail-pill" :style="pillStyle" />
        <button
          v-for="it in items"
          :key="it.key"
          type="button"
          class="stage-rail-btn"
          :class="{ 'is-active': it.key === modelValue }"
          @click="emit('update:modelValue', it.key)"
        >
          {{ it.label }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stage-rail {
  position: sticky;
  top: var(--sa-header-height, 77px);
  z-index: 20;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 10px var(--sa-gutter, 36px);
  border-top: 1px solid var(--sa-border-subtle);
  border-bottom: 1px solid var(--sa-border-subtle);
  /* 不用 backdrop-filter：吸顶栏每帧对滚动内容重采样，实测会把滚动拖到 50fps */
  background: color-mix(in oklab, var(--sa-bg) 97%, transparent);
}
@media (min-width: 1100px) {
  .stage-rail {
    flex-direction: row;
    align-items: center;
    justify-content: space-between;
  }
}
.stage-rail-focus {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--sa-text-tertiary);
  white-space: nowrap;
}
.stage-rail-focus strong {
  color: var(--sa-text-primary);
}
.stage-dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--sa-accent);
}
.stage-rail-scroll {
  min-width: 0;
  overflow-x: auto;
  scrollbar-width: none;
}
.stage-rail-scroll::-webkit-scrollbar {
  display: none;
}
.stage-rail-track {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 4px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 999px;
  background: var(--sa-subtle);
}
.stage-rail-pill {
  position: absolute;
  top: 4px;
  bottom: 4px;
  left: 4px;
  border-radius: 999px;
  background: var(--sa-text-primary);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.18);
  transition:
    transform 0.5s var(--spring, cubic-bezier(0.16, 1, 0.3, 1)),
    width 0.5s var(--spring, cubic-bezier(0.16, 1, 0.3, 1)),
    opacity 0.2s ease;
  pointer-events: none;
  z-index: 0;
}
.stage-rail-btn {
  position: relative;
  z-index: 1;
  display: inline-flex;
  align-items: center;
  padding: 7px 14px;
  border: none;
  border-radius: 999px;
  background: none;
  color: var(--sa-text-secondary);
  font-family: inherit;
  font-size: 12px;
  white-space: nowrap;
  cursor: pointer;
  transition: color 0.3s ease;
}
.stage-rail-btn:hover {
  color: var(--sa-text-primary);
}
.stage-rail-btn.is-active {
  color: var(--sa-bg);
  font-weight: 600;
}
</style>
