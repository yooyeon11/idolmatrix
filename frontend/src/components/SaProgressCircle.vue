<script setup lang="ts">
/**
 * 环形进度（HeroUI ProgressCircle 规格）
 *
 * 几何抄自 heroui v3 的 `progress-circle.tsx`：viewBox `0 0 36 36`、圆心 18、描边 4、
 * 半径 = 18 − 4/2 = **16**、周长 `2πr ≈ 100.53`；填充圆 `stroke-dasharray = 周长`、
 * `stroke-dashoffset = 周长 × (1 − pct)`、线帽 round、起点用 `rotate(-90 18 18)` 转到 12 点方向；
 * 过渡 `stroke-dashoffset 300ms`（尊重 prefers-reduced-motion）。
 * 尺寸档位照它的 sm/md/lg = 20/28/36px（SVG 用 viewBox 整体缩放，描边跟着等比）。
 *
 * 数字默认画在环心（就是 HeroUI 文档里的 With Label 用法）。
 */
withDefaults(
  defineProps<{
    /** 0–100 */
    value: number
    /** 外径 px，默认 36（HeroUI 的 lg 档） */
    size?: number
    /** 进度弧颜色，默认主题色 */
    color?: string
    /** 轨道颜色 */
    trackColor?: string
    /** 是否在环心显示数字 */
    showValue?: boolean
    /** 无障碍标签 */
    label?: string
  }>(),
  { size: 36, showValue: true, label: '完整度' },
)

// HeroUI 的常量原值：viewBox 36、描边 4、半径 16
const CENTER = 18
const STROKE = 4
const RADIUS = CENTER - STROKE / 2
const CIRCUMFERENCE = 2 * Math.PI * RADIUS

function offsetOf(pct: number) {
  const clamped = Math.max(0, Math.min(100, pct || 0))
  return CIRCUMFERENCE * (1 - clamped / 100)
}
</script>

<template>
  <span
    class="sa-progress-circle"
    :style="{
      width: `${size}px`,
      height: `${size}px`,
      // 颜色走 CSS 变量注入：SVG 的 presentation attribute（stroke=…）优先级低于样式表，
      // 直接用 :stroke 会被下面的 CSS 规则压掉。
      ...(color ? { '--sa-progress-stroke': color } : {}),
      ...(trackColor ? { '--sa-progress-track': trackColor } : {}),
    }"
    role="progressbar"
    :aria-valuenow="Math.max(0, Math.min(100, value || 0))"
    aria-valuemin="0"
    aria-valuemax="100"
    :aria-label="label"
  >
    <svg :width="size" :height="size" :viewBox="`0 0 ${CENTER * 2} ${CENTER * 2}`" fill="none">
      <circle class="sa-progress-circle__track" :cx="CENTER" :cy="CENTER" :r="RADIUS" :stroke-width="STROKE" />
      <circle
        class="sa-progress-circle__fill"
        :cx="CENTER"
        :cy="CENTER"
        :r="RADIUS"
        :stroke-width="STROKE"
        :stroke-dasharray="CIRCUMFERENCE"
        :stroke-dashoffset="offsetOf(value)"
        stroke-linecap="round"
        :transform="`rotate(-90 ${CENTER} ${CENTER})`"
      />
      <text
        v-if="showValue"
        class="sa-progress-circle__text"
        :x="CENTER"
        :y="CENTER"
        text-anchor="middle"
        dominant-baseline="central"
      >
        {{ Math.round(value || 0) }}
      </text>
    </svg>
  </span>
</template>

<style scoped>
.sa-progress-circle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  line-height: 0;
}
.sa-progress-circle__track {
  stroke: var(--sa-progress-track, var(--sa-subtle));
}
.sa-progress-circle__fill {
  stroke: var(--sa-progress-stroke, var(--sa-accent));
  transition: stroke-dashoffset 300ms cubic-bezier(0.16, 1, 0.3, 1);
}
.sa-progress-circle__text {
  font-size: 12px;
  font-weight: 700;
  fill: var(--sa-text-primary);
}
@media (prefers-reduced-motion: reduce) {
  .sa-progress-circle__fill {
    transition: none;
  }
}
</style>
