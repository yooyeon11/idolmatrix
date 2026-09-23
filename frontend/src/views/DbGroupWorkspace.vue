<script setup lang="ts">
// 资料库 · 组合工作台整页：薄壳（站点头 / 页签行 / 路由 uid）+ DbGroupWorkspacePanel。
// 工作台本体在 components/db/DbGroupWorkspacePanel.vue（设置页·资料库 内嵌同一组件）。
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SaHeader from '@/components/SaHeader.vue'
import DbTabs from '@/components/db/DbTabs.vue'
import DbGroupWorkspacePanel from '@/components/db/DbGroupWorkspacePanel.vue'

const route = useRoute()
const router = useRouter()
const uid = computed(() => String(route.params.uid || ''))
</script>

<template>
  <div class="mv-page">
    <SaHeader />
    <div class="mv-container">
      <div class="ws-tab-row">
        <DbTabs />
        <button class="back-btn" type="button" @click="router.push('/db/list')">返回列表</button>
      </div>

      <DbGroupWorkspacePanel :uid="uid" @back="router.push('/db/list')" />
    </div>
  </div>
</template>

<style scoped>
.mv-page {
  --dh-ok: #18a058;
  --dh-warn: #d97706;
  --dh-bad: #d03050;
}
.mv-container {
  max-width: 1280px;
  margin: 0 auto;
  padding: 14px 22px 64px;
}
.ws-tab-row {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.back-btn {
  border: none;
  background: none;
  color: var(--sa-text-tertiary);
  font-size: 12px;
  cursor: pointer;
}
</style>
