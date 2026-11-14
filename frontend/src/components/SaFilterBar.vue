<script setup lang="ts">
/**
 * 选项按钮统一容器（口径来自「浏览」页的 `.control-bar`）
 *
 * 全站筛选/排序/检索控件一律装进这颗胶囊：
 *   36px 高（4px 内边距 + 26px 控件）/ 全圆角 / 1px --sa-border / --sa-elevated 底 /
 *   发丝级微投影；控件之间用 `<span class="sa-fb-sep" />` 竖线分隔。
 *
 * 为什么不各页各写一套：浏览页那套是业主认定的基准，但之前只有它自己用，
 * 艺人 / 组合 / 歌曲 / 上传者 / 收藏夹各自一套高宽和字号，并排看像三个系统。
 */
</script>

<template>
  <div class="sa-fb">
    <slot />
  </div>
</template>

<style scoped>
.sa-fb {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 4px;
  border-radius: 999px;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

/* 组间竖线：由使用方插入 <span class="sa-fb-sep" />，样式统一在这里定义 */
.sa-fb :deep(.sa-fb-sep) {
  flex: none;
  width: 1px;
  height: 16px;
  margin: 0 1px;
  background: var(--sa-border-subtle);
}

/* 窄屏：整条占满一行，控件按内容宽排；放不下时横向滚动而不是换行 */
@media (max-width: 768px) {
  .sa-fb {
    width: 100%;
    gap: 0;
    overflow-x: auto;
    scrollbar-width: none;
  }
  .sa-fb::-webkit-scrollbar {
    display: none;
  }
}
</style>
