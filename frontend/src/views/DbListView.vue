<script setup lang="ts">
// 资料库 · 组合列表整页：壳（标题/计数/头部按钮）+ DbGroupListPanel（列表本体）。
// 列表逻辑全部在 components/db/DbGroupListPanel.vue（设置页内嵌同一组件）。
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { RefreshOutlined } from '@/components/icons'
import DbPageShell from '@/components/db/DbPageShell.vue'
import DbGroupListPanel from '@/components/db/DbGroupListPanel.vue'
import type { GroupListResponse } from '@/api/dbViews'

const router = useRouter()
const panel = ref<InstanceType<typeof DbGroupListPanel> | null>(null)
const countText = ref('')

function onLoaded(res: GroupListResponse | null) {
  countText.value = res ? `${res.total} 个组合 · 只读预览版` : ''
}
</script>

<template>
  <div class="mv-page">
    <DbPageShell title="资料库 · 组合" :count="countText">
      <template #actions>
        <button class="create-btn" type="button" @click="panel?.openCreate()">新建组合</button>
        <button class="reload-btn" @click="panel?.load()">
          <RefreshOutlined :size="15" />
          刷新
        </button>
      </template>

      <DbGroupListPanel
        ref="panel"
        :show-actions="false"
        @open="(uid) => router.push(`/db/groups/${uid}`)"
        @created="(uid) => router.push(`/db/groups/${uid}`)"
        @loaded="onLoaded"
      />
    </DbPageShell>
  </div>
</template>

<style scoped>
.mv-page {
  min-height: 100vh;
  --dh-ok: #18a058;
  --dh-warn: #d97706;
  --dh-bad: #d03050;
}
.reload-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 9px 18px;
  border: 1px solid var(--sa-border);
  border-radius: 10px;
  background: var(--sa-elevated);
  color: var(--sa-text-primary);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.reload-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.create-btn {
  border: 1px solid var(--sa-accent-border);
  background: var(--sa-accent-subtle);
  color: var(--sa-accent);
  border-radius: 10px;
  font-size: 13px;
  font-weight: 700;
  padding: 7px 14px;
  cursor: pointer;
  font-family: inherit;
}
</style>
