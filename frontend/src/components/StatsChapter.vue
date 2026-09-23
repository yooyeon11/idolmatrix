<script setup lang="ts">
/**
 * 年度报告章节框架：超大淡色编号 + 章节小标 + 主文案 + 内容插槽。
 * 统计页推翻重建后的固定章节语法，保证全页节奏一致。
 */
defineProps<{
  /** 章节编号，如 "01" */
  no: string
  /** 章节小标，如「收藏节奏」 */
  kicker: string
  /** 主文案（一句人话，≤15 字） */
  title: string
}>()
</script>

<template>
  <section class="chapter">
    <div class="chapter-head">
      <span class="chapter-no" aria-hidden="true">{{ no }}</span>
      <div class="chapter-copy">
        <div class="chapter-kicker">{{ kicker }}</div>
        <h2 class="chapter-title">{{ title }}</h2>
      </div>
    </div>
    <div class="chapter-body">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.chapter {
  min-width: 0;
}
.chapter-head {
  display: flex;
  align-items: center;
  gap: 20px;
  margin-bottom: 26px;
}
.chapter-no {
  font-size: 3.1rem;
  font-weight: 800;
  line-height: 1;
  letter-spacing: -0.04em;
  color: var(--sa-accent);
  opacity: 0.16;
  font-variant-numeric: tabular-nums;
  flex: none;
}
.chapter-kicker {
  font-size: 12px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--sa-accent);
  font-weight: 700;
}
.chapter-title {
  margin: 4px 0 0;
  font-size: clamp(1.3rem, 2.2vw, 1.7rem);
  font-weight: 800;
  letter-spacing: -0.02em;
  line-height: 1.3;
  overflow-wrap: anywhere;
}
@media (max-width: 768px) {
  .chapter-head {
    gap: 14px;
    margin-bottom: 18px;
  }
  .chapter-no {
    font-size: 2.3rem;
  }
}
</style>
