<script setup lang="ts">
import { computed } from 'vue'
import type { RecapHour } from '@/types/models'

/** 观看时钟：24 小时观看次数柱状分布，峰值小时高亮并标注。 */
const props = defineProps<{ hours: RecapHour[]; peakHour: number | null }>()

const max = computed(() => Math.max(1, ...props.hours.map((h) => h.count)))
const hasData = computed(() => props.hours.some((h) => h.count > 0))

function barHeight(count: number) {
  if (!count) return '3px'
  return `${Math.max(8, (count / max.value) * 100)}%`
}

function pad(n: number) {
  return String(n).padStart(2, '0')
}
</script>

<template>
  <div class="clock">
    <div v-if="hasData" class="clock-bars">
      <div
        v-for="h in hours"
        :key="h.hour"
        class="clock-col"
        :class="{ 'clock-col--peak': peakHour === h.hour }"
        :title="`${pad(h.hour)}:00 · ${h.count} 次`"
      >
        <span v-if="peakHour === h.hour" class="clock-peak-label">{{ pad(h.hour) }}:00</span>
        <div class="clock-bar" :style="{ height: barHeight(h.count) }" />
      </div>
    </div>
    <div class="clock-axis">
      <span>0点</span>
      <span>6点</span>
      <span>12点</span>
      <span>18点</span>
      <span>23点</span>
    </div>
    <p v-if="!hasData" class="clock-empty">还没有观看记录</p>
  </div>
</template>

<style scoped>
.clock {
  min-width: 0;
}
.clock-bars {
  display: flex;
  align-items: flex-end;
  gap: 5px;
  height: 180px;
  padding-top: 24px; /* 峰值标注的预留空间 */
}
.clock-col {
  flex: 1;
  min-width: 0;
  height: 100%;
  display: flex;
  align-items: flex-end;
  position: relative;
}
.clock-bar {
  width: 100%;
  min-height: 3px;
  border-radius: 4px 4px 0 0;
  background: var(--sa-subtle);
  transition: height 0.4s ease;
}
.clock-col:hover .clock-bar {
  background: var(--sa-border);
}
.clock-col--peak .clock-bar {
  background: var(--sa-accent);
  box-shadow: 0 0 0 1px var(--sa-accent-border);
}
.clock-col--peak:hover .clock-bar {
  background: var(--sa-accent-hover);
}
.clock-peak-label {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  bottom: calc(100% - 20px);
  font-size: 11px;
  font-weight: 700;
  color: var(--sa-accent);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.clock-axis {
  display: flex;
  justify-content: space-between;
  margin-top: 8px;
  font-size: 11px;
  color: var(--sa-text-tertiary);
  font-variant-numeric: tabular-nums;
}
.clock-empty {
  margin: 24px 0 0;
  font-size: 13px;
  color: var(--sa-text-secondary);
}
</style>
