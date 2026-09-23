<script setup lang="ts">
/**
 * 状态徽标（警告三角）
 *
 * 仿 HeroUI 图标的圆角三角：有 issue 时数字画在三角里（白字），
 * 没有时画白色对勾。颜色由调用方传（红 = 有错误、琥珀 = 待补、绿 = 健康）。
 *
 * 几何：viewBox 24×22，顶点 (12, 2.2)、底边 y=19.8；描边同色 + `stroke-linejoin: round`
 * 把尖角描圆（视觉近似 HeroUI 的圆角图标，不用手写圆角路径）。
 */
withDefaults(
  defineProps<{
    /** 问题总数；0 = 健康（显示对勾） */
    count: number
    /** 三角颜色，默认健康绿 */
    color?: string
    /** 悬停/无障碍说明 */
    label?: string
  }>(),
  { color: '', label: '状态' },
)
</script>

<template>
  <span
    class="sa-issue-badge"
    :style="color ? { '--sa-issue-color': color } : {}"
    role="status"
    :aria-label="label"
    :title="label"
  >
    <svg width="24" height="22" viewBox="0 0 24 22" fill="none">
      <path
        class="sa-issue-badge__tri"
        d="M12 2.2 L21.8 19.8 H2.2 Z"
        stroke-width="2.5"
        stroke-linejoin="round"
      />
      <text
        v-if="count > 0"
        class="sa-issue-badge__num"
        x="12"
        y="17.4"
        text-anchor="middle"
      >
        {{ count > 99 ? '99+' : count }}
      </text>
      <path
        v-else
        class="sa-issue-badge__check"
        d="M7.4 11.9 l3.1 3.2 6-6.8"
        stroke-width="2.2"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
    </svg>
  </span>
</template>

<style scoped>
.sa-issue-badge {
  display: inline-flex;
  line-height: 0;
  cursor: default;
}
.sa-issue-badge__tri {
  fill: var(--sa-issue-color, var(--sa-progress-stroke, var(--sa-accent)));
  stroke: var(--sa-issue-color, var(--sa-progress-stroke, var(--sa-accent)));
}
.sa-issue-badge__num {
  font-size: 9.5px;
  font-weight: 700;
  fill: #fff;
}
.sa-issue-badge__check {
  stroke: #fff;
}
</style>
