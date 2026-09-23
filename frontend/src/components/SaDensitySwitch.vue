<script setup lang="ts">
import { computed } from 'vue'
import { useGridDensity, type GridDensity } from '../composables/useGridDensity'

// iconOnly：只显示三条密度示意线（移动端筛选工具栏按图标陈列，不显示文字）
withDefaults(defineProps<{ iconOnly?: boolean }>(), { iconOnly: false })

const { density, setDensity } = useGridDensity()

// 单按钮循环：紧凑 → 标准 → 宽松 → 紧凑
const ORDER: GridDensity[] = ['compact', 'standard', 'loose']
const LABELS: Record<GridDensity, string> = {
  compact: '紧凑',
  standard: '标准',
  loose: '宽松',
}

const next = computed(
  () => ORDER[(ORDER.indexOf(density.value) + 1) % ORDER.length],
)

function cycle() {
  setDensity(next.value)
}
</script>

<template>
  <button
    type="button"
    class="sa-density"
    :class="{ 'sa-density--icon': iconOnly }"
    :title="`当前显示密度：${LABELS[density]}，点击切换为${LABELS[next]}`"
    :aria-label="`显示密度：${LABELS[density]}，点击切换为${LABELS[next]}`"
    @click="cycle"
  >
    <span class="sa-density-bars" :data-density="density" aria-hidden="true">
      <i></i><i></i><i></i>
    </span>
    <span v-if="!iconOnly" class="sa-density-label">{{ LABELS[density] }}</span>
  </button>
</template>

<style scoped>
.sa-density {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 32px;
  padding: 0 12px 0 10px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 9999px;
  background: var(--sa-elevated);
  color: var(--sa-text-secondary);
  font-size: 13px;
  white-space: nowrap;
  cursor: pointer;
  transition: color 0.2s, border-color 0.2s;
}
.sa-density:hover {
  color: var(--sa-text-primary);
  border-color: var(--sa-accent);
}
.sa-density:focus-visible {
  outline: 2px solid var(--sa-accent);
  outline-offset: 1px;
}
/* 三条横线以间距示意密度档位：紧凑间距小，宽松间距大 */
.sa-density-bars {
  display: inline-flex;
  flex-direction: column;
  justify-content: center;
  gap: 2px;
  height: 14px;
}
.sa-density-bars i {
  display: block;
  width: 14px;
  height: 2px;
  border-radius: 1px;
  background: currentColor;
  transition: gap 0.2s;
}
.sa-density-bars[data-density='standard'] {
  gap: 3px;
}
.sa-density-bars[data-density='loose'] {
  gap: 4px;
}
.sa-density-label {
  font-weight: 500;
}
/* 仅图标形态：与筛选工具栏其他圆形图标按钮同尺寸 */
.sa-density--icon {
  width: 30px;
  height: 30px;
  padding: 0;
  justify-content: center;
}
</style>
