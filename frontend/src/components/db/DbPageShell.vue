<script setup lang="ts">
import SaHeader from '@/components/SaHeader.vue'
import DbTabs from '@/components/db/DbTabs.vue'

withDefaults(
  defineProps<{
    title: string
    count?: string
    /** 内嵌进设置页·资料库时置 true：去掉站点头 / 页签 / 整屏外壳，只留可选的紧凑操作条 */
    embedded?: boolean
  }>(),
  { embedded: false },
)
</script>

<template>
  <div :class="embedded ? 'db-embed' : 'db-page'">
    <SaHeader v-if="!embedded" />
    <div :class="embedded ? 'db-embed-body' : 'db-container'">
      <template v-if="!embedded">
        <div class="page-head">
          <div class="page-title-wrap">
            <h1 class="page-title">{{ title }}</h1>
            <span v-if="count" class="page-count">{{ count }}</span>
          </div>
          <div class="page-actions">
            <slot name="actions" />
          </div>
        </div>

        <DbTabs class="shell-tabs" />
      </template>

      <div v-else-if="$slots.actions" class="db-embed-bar">
        <span v-if="count" class="db-embed-count">{{ count }}</span>
        <div class="page-actions">
          <slot name="actions" />
        </div>
      </div>

      <slot />
    </div>
  </div>
</template>

<style scoped>
.db-page {
  min-height: 100vh;
}
/* 内嵌进设置页·资料库：不占整屏、无站点头 / 页签 / 页头大标题 */
.db-embed,
.db-embed-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.db-embed-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.db-embed-count {
  font-size: 12.5px;
  color: var(--sa-text-tertiary);
}
/* 与顶栏 .sa-container 同宽（1280px），保证所有标签页左右对齐一致 */
.db-container {
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 32px 64px;
}
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 36px 0 16px;
}
.page-title-wrap {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}
.page-title {
  font-size: 24px;
  font-weight: 700;
  margin: 0;
  white-space: nowrap;
}
.page-count {
  font-size: 13px;
  color: var(--sa-text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.page-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
/* class 落在 DbTabs 根元素上，用它控制与下方内容的间距 */
.shell-tabs {
  margin-bottom: 16px;
}

@media (max-width: 768px) {
  .db-container {
    padding: 0 16px 110px;
  }
  .page-head {
    padding: 24px 0 14px;
  }
  .page-title {
    font-size: 20px;
  }
}
</style>
