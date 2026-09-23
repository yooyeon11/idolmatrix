<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

const props = defineProps<{
  src: string
  aspect: number
  title: string
}>()

const emit = defineEmits<{
  cancel: []
  confirm: [crop: { x: number; y: number; width: number; height: number }]
}>()

/** 提示文案里的比例：整数写 1:1，小数写 1.82:1（避免再硬编码某个比例） */
const aspectLabel = computed(() => {
  const a = props.aspect
  return `${Number.isInteger(a) ? a : Number(a.toFixed(2))}:1`
})

const stage = ref<HTMLElement | null>(null)
const img = ref<HTMLImageElement | null>(null)
const natural = ref({ w: 0, h: 0 })
const scale = ref(1)
const crop = ref({ x: 0, y: 0, w: 0, h: 0 })
const dragging = ref<'move' | 'nw' | 'ne' | 'sw' | 'se' | null>(null)
const dragStart = ref({ x: 0, y: 0, crop: { x: 0, y: 0, w: 0, h: 0 } })

function fitImage() {
  const el = img.value
  const box = stage.value
  if (!el || !box || !el.naturalWidth) return
  natural.value = { w: el.naturalWidth, h: el.naturalHeight }
  const maxW = box.clientWidth
  const maxH = box.clientHeight
  scale.value = Math.min(maxW / el.naturalWidth, maxH / el.naturalHeight, 1)
  const ratio = props.aspect
  const nw = el.naturalWidth
  const nh = el.naturalHeight
  let w: number
  let h: number
  if (nw / nh >= ratio) {
    h = nh
    w = h * ratio
  } else {
    w = nw
    h = w / ratio
  }
  crop.value = { x: (nw - w) / 2, y: (nh - h) / 2, w, h }
}

function boxStyle() {
  const s = scale.value
  return {
    left: `${crop.value.x * s}px`,
    top: `${crop.value.y * s}px`,
    width: `${crop.value.w * s}px`,
    height: `${crop.value.h * s}px`,
  }
}

const imgStyle = computed(() => ({
  width: `${natural.value.w * scale.value}px`,
  height: `${natural.value.h * scale.value}px`,
}))

function clampCrop(next: { x: number; y: number; w: number; h: number }) {
  const { w: nw, h: nh } = natural.value
  const ratio = props.aspect
  let { x, y, w, h } = next
  w = Math.max(32, w)
  h = w / ratio
  if (h < 32) {
    h = 32
    w = h * ratio
  }
  if (w > nw) {
    w = nw
    h = w / ratio
  }
  if (h > nh) {
    h = nh
    w = h * ratio
  }
  x = Math.min(Math.max(0, x), nw - w)
  y = Math.min(Math.max(0, y), nh - h)
  crop.value = { x, y, w, h }
}

function onPointerDown(kind: 'move' | 'nw' | 'ne' | 'sw' | 'se', e: PointerEvent) {
  e.preventDefault()
  e.stopPropagation()
  dragging.value = kind
  dragStart.value = { x: e.clientX, y: e.clientY, crop: { ...crop.value } }
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
}

function onPointerMove(e: PointerEvent) {
  if (!dragging.value) return
  const s = scale.value || 1
  const dx = (e.clientX - dragStart.value.x) / s
  const dy = (e.clientY - dragStart.value.y) / s
  const c = dragStart.value.crop
  const ratio = props.aspect
  if (dragging.value === 'move') {
    clampCrop({ x: c.x + dx, y: c.y + dy, w: c.w, h: c.h })
    return
  }
  let x = c.x
  let y = c.y
  let w = c.w
  let h = c.h
  if (dragging.value.includes('e')) w = c.w + dx
  if (dragging.value.includes('w')) {
    w = c.w - dx
    x = c.x + dx
  }
  if (dragging.value.includes('s')) h = w / ratio
  if (dragging.value.includes('n')) {
    h = w / ratio
    y = c.y + c.h - h
  }
  if (!dragging.value.includes('n') && !dragging.value.includes('s')) {
    h = w / ratio
  }
  clampCrop({ x, y, w, h })
}

function onPointerUp() {
  dragging.value = null
}

function confirm() {
  const { w, h } = natural.value
  if (!w || !h) return
  emit('confirm', {
    x: crop.value.x / w,
    y: crop.value.y / h,
    width: crop.value.w / w,
    height: crop.value.h / h,
  })
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('cancel')
}

onMounted(() => {
  window.addEventListener('keydown', onKey)
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', onPointerUp)
  window.addEventListener('pointercancel', onPointerUp)
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', onPointerUp)
  window.removeEventListener('pointercancel', onPointerUp)
})

watch(() => props.src, () => {
  crop.value = { x: 0, y: 0, w: 0, h: 0 }
})
</script>

<template>
  <div class="crop-mask" @click.self="emit('cancel')">
    <div class="crop-panel" @click.stop>
      <div class="crop-head">
        <div class="crop-title">{{ title }}</div>
        <div class="crop-hint">拖动选区，四角可缩放。比例 {{ aspectLabel }}</div>
      </div>
      <div ref="stage" class="crop-stage">
        <div class="crop-frame">
          <img
            ref="img"
            class="crop-img"
            :src="src"
            alt=""
            :style="imgStyle"
            draggable="false"
            @load="fitImage"
          />
          <div v-if="crop.w" class="crop-box" :style="boxStyle()" @pointerdown="onPointerDown('move', $event)">
            <span class="crop-handle crop-handle--nw" @pointerdown="onPointerDown('nw', $event)" />
            <span class="crop-handle crop-handle--ne" @pointerdown="onPointerDown('ne', $event)" />
            <span class="crop-handle crop-handle--sw" @pointerdown="onPointerDown('sw', $event)" />
            <span class="crop-handle crop-handle--se" @pointerdown="onPointerDown('se', $event)" />
          </div>
        </div>
      </div>
      <div class="crop-actions">
        <button class="crop-btn" type="button" @click="emit('cancel')">取消</button>
        <button class="crop-btn crop-btn--primary" type="button" @click="confirm">确认裁切</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.crop-mask {
  position: fixed;
  inset: 0;
  /* 高于看图 lightbox（200）：裁切从查看器内唤起，必须盖住它 */
  z-index: 220;
  background: rgba(0, 0, 0, 0.72);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
}
.crop-panel {
  width: min(920px, 100%);
  max-height: calc(100vh - 32px);
  display: flex;
  flex-direction: column;
  background: var(--sa-bg, #111);
  border: 1px solid var(--sa-border, rgba(255, 255, 255, 0.12));
  border-radius: 16px;
  overflow: hidden;
}
.crop-head {
  padding: 14px 18px 8px;
}
.crop-title {
  font-size: 15px;
  font-weight: 650;
  color: var(--sa-text-primary, #fff);
}
.crop-hint {
  margin-top: 4px;
  font-size: 12px;
  color: var(--sa-text-tertiary, rgba(255, 255, 255, 0.55));
}
.crop-stage {
  flex: 1;
  min-height: 280px;
  max-height: min(70vh, 640px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 8px 18px 12px;
}
.crop-frame {
  position: relative;
  line-height: 0;
}
.crop-img {
  display: block;
  max-width: none;
  user-select: none;
  pointer-events: none;
}
.crop-box {
  position: absolute;
  box-sizing: border-box;
  border: 2px solid #fff;
  box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.45);
  cursor: move;
  touch-action: none;
}
.crop-handle {
  position: absolute;
  width: 14px;
  height: 14px;
  background: #fff;
  border-radius: 2px;
  touch-action: none;
}
.crop-handle--nw { left: -7px; top: -7px; cursor: nwse-resize; }
.crop-handle--ne { right: -7px; top: -7px; cursor: nesw-resize; }
.crop-handle--sw { left: -7px; bottom: -7px; cursor: nesw-resize; }
.crop-handle--se { right: -7px; bottom: -7px; cursor: nwse-resize; }
.crop-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 18px 16px;
}
.crop-btn {
  padding: 7px 16px;
  border: 1px solid var(--sa-border, rgba(255, 255, 255, 0.2));
  border-radius: 10px;
  background: transparent;
  color: var(--sa-text-primary, #fff);
  cursor: pointer;
  font-size: 13px;
}
.crop-btn--primary {
  background: var(--sa-accent, #7c5cfc);
  border-color: var(--sa-accent, #7c5cfc);
  color: #fff;
}
</style>
