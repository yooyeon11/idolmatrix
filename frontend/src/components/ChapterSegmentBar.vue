<script setup lang="ts">
import { computed } from 'vue'
import type { VideoChapter } from '@/types/models'

const props = defineProps<{
  chapters: VideoChapter[]
  currentTime: number
  duration: number
}>()
const emit = defineEmits<{ (e: 'seek', time: number): void }>()

interface Seg {
  start: number
  end: number
  title: string
}

// 纯展示分段条：按章节切分整段时长，点击跳转到该章节起点。
// 保留播放器原生进度条，本条只做章节级导航。
const segments = computed<Seg[]>(() => {
  const total = props.duration
  const list = (props.chapters || []).filter((c) => c.time >= 0 && (!total || c.time < total))
  if (!list.length || !total) return []
  return list.map((c, i) => ({
    start: c.time,
    end: i < list.length - 1 ? list[i + 1].time : total,
    title: c.title,
  }))
})

function isActive(seg: Seg) {
  return props.currentTime >= seg.start && props.currentTime < seg.end
}
function isPast(seg: Seg) {
  return props.currentTime >= seg.end
}
function onSeek(seg: Seg) {
  emit('seek', seg.start)
}
function pct(seg: Seg) {
  const total = props.duration || 0
  if (!total) return 0
  return Math.max(0, Math.min(100, ((seg.end - seg.start) / total) * 100))
}
</script>

<template>
  <div v-if="segments.length" class="chapter-bar" role="group" aria-label="章节导航">
    <button
      v-for="(seg, i) in segments"
      :key="i"
      type="button"
      class="chapter-seg"
      :class="{ 'chapter-seg--active': isActive(seg), 'chapter-seg--past': isPast(seg) }"
      :style="{ flexGrow: String(Math.max(pct(seg), 1)) }"
      :title="seg.title"
      :aria-label="seg.title"
      @click="onSeek(seg)"
    >
      <span class="chapter-seg-fill" />
    </button>
  </div>
</template>

<style scoped>
/* 叠在播放器画面上（深色底），故分段用浅色体系 */
.chapter-bar {
  display: flex;
  gap: 4px;
  width: 100%;
  height: 10px;
  align-items: flex-end;
}
.chapter-seg {
  position: relative;
  flex-basis: 0;
  min-width: 8px;
  height: 6px;
  padding: 0;
  border: none;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.3);
  cursor: pointer;
  overflow: hidden;
  transition:
    height 0.25s cubic-bezier(0.16, 1, 0.3, 1),
    background-color 0.25s ease;
}
.chapter-seg:hover {
  height: 10px;
  background: rgba(255, 255, 255, 0.62);
}
.chapter-seg--past {
  background: color-mix(in oklab, var(--sa-accent) 45%, rgba(255, 255, 255, 0.35));
}
.chapter-seg--active {
  background: var(--sa-accent);
  box-shadow: 0 0 12px color-mix(in oklab, var(--sa-accent) 75%, transparent);
}
.chapter-seg-fill {
  position: absolute;
  inset: 0;
  background: transparent;
}
</style>
