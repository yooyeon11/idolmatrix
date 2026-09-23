<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { recycleApi } from '@/api/recycle'
import { dashboardApi } from '@/api/dashboard'
import type { RecycleItem, RecycleKind } from '@/types/models'
import { RefreshOutlined } from '@/components/icons'
import DbPageShell from '@/components/db/DbPageShell.vue'

// 内嵌进设置页·资料库时为 true（去掉整屏外壳）
defineProps<{ embedded?: boolean }>()

const message = useMessage()

const RECYCLE_TABS: { key: RecycleKind; label: string }[] = [
  { key: 'videos', label: '视频' },
  { key: 'artists', label: '艺人' },
  { key: 'groups', label: '组合' },
  { key: 'songs', label: '歌曲' },
  { key: 'albums', label: '专辑' },
  { key: 'companies', label: '公司' },
]
const recycleTab = ref<RecycleKind>('videos')
const recycleSearch = ref('')
const recyclePage = ref(1)
const recyclePageSize = 20
const recycleItems = ref<RecycleItem[]>([])
const recycleTotal = ref(0)
const recycleLoading = ref(false)
const recycleBusyId = ref<number | null>(null)

async function loadRecycle(resetPage = false) {
  if (resetPage) recyclePage.value = 1
  recycleLoading.value = true
  try {
    const r = await recycleApi.list(recycleTab.value, {
      q: recycleSearch.value.trim() || undefined,
      page: recyclePage.value,
      page_size: recyclePageSize,
    })
    recycleItems.value = r.items
    recycleTotal.value = r.total
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    recycleLoading.value = false
  }
}

function switchRecycleTab(key: RecycleKind) {
  if (recycleTab.value === key) return
  recycleTab.value = key
  recycleSearch.value = ''
  void loadRecycle(true)
}

function formatDeletedAt(iso?: string | null) {
  if (!iso) return '--'
  return iso.replace('T', ' ').slice(0, 19)
}

async function restoreRecycleItem(item: RecycleItem) {
  recycleBusyId.value = item.id
  try {
    await recycleApi.restore(recycleTab.value, item.id)
    message.success(item.file_missing ? '已恢复（源文件当前不在磁盘上）' : '已恢复')
    await loadRecycle()
    dashboardApi.stats().catch(() => null)
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    recycleBusyId.value = null
  }
}

async function purgeRecycleItem(item: RecycleItem) {
  const extra =
    recycleTab.value === 'videos'
      ? '视频会同步删除缩略图与转码缓存；不会删除磁盘上的视频文件本身。'
      : ''
  if (!window.confirm(`确定永久删除「${item.name}」？此操作不可恢复。${extra}`)) return
  recycleBusyId.value = item.id
  try {
    await recycleApi.purge(recycleTab.value, item.id)
    message.success('已永久删除')
    await loadRecycle()
    dashboardApi.stats().catch(() => null)
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    recycleBusyId.value = null
  }
}

onMounted(() => void loadRecycle(true))
</script>

<style scoped src="./settings/settings-shared.css"></style>

<style scoped>
.db-tabs {
  margin-bottom: 12px;
}
.op-row {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
}
.db-purge-btn {
  border: 1px solid rgba(208, 48, 80, 0.4);
  background: transparent;
  color: #d03050;
  border-radius: 7px;
  font-size: 12px;
  padding: 4px 10px;
  cursor: pointer;
  font-family: inherit;
}
.db-purge-btn:hover {
  background: rgba(208, 48, 80, 0.08);
}
.db-purge-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
</style>

<template>
  <DbPageShell title="资料库 · 回收站" :count="recycleTotal ? `共 ${recycleTotal} 条` : ''" :embedded="embedded">
    <template #actions>
      <button class="sa-btn sa-btn--ghost" :disabled="recycleLoading" @click="loadRecycle()">
        <RefreshOutlined :size="14" />
      </button>
    </template>

    <p class="field-hint" style="margin: 0 0 12px">
      软删除的记录留在这里：恢复后回到资料库列表（关系边不自动重建）；永久删除则连同数据库行一起清除。视频若源文件已不在磁盘上，恢复后仍可能被失效清理再次移入。
    </p>
    <div class="db-tabs" role="tablist">
      <button
        v-for="t in RECYCLE_TABS"
        :key="t.key"
        class="db-tab"
        :class="{ 'db-tab--active': recycleTab === t.key }"
        role="tab"
        :aria-selected="recycleTab === t.key"
        @click="switchRecycleTab(t.key)"
      >
        {{ t.label }}
      </button>
    </div>
    <div class="db-toolbar">
      <div class="db-count">共 {{ recycleTotal }} 条</div>
      <div class="db-search">
        <input
          v-model="recycleSearch"
          class="sa-input"
          type="text"
          placeholder="搜索名称…"
          @keyup.enter="loadRecycle(true)"
        />
        <button class="sa-btn sa-btn--ghost" :disabled="recycleLoading" @click="loadRecycle(true)">
          搜索
        </button>
      </div>
    </div>
    <div v-if="recycleLoading" class="db-loading">加载中…</div>
    <div v-else class="db-table-wrap">
      <table class="db-table">
        <thead>
          <tr>
            <th>名称</th>
            <th class="hide-sm">中文名</th>
            <th>删除时间</th>
            <th class="hide-sm">备注</th>
            <th class="db-op-col">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="recycleItems.length === 0">
            <td colspan="5" class="db-empty">回收站是空的</td>
          </tr>
          <tr v-for="item in recycleItems" :key="item.id">
            <td class="db-name">{{ item.name }}</td>
            <td class="db-sub hide-sm">{{ item.chinese_name || '--' }}</td>
            <td class="db-sub">{{ formatDeletedAt(item.deleted_at) }}</td>
            <td class="db-sub hide-sm">
              <span v-if="item.file_missing">源文件不在磁盘</span>
              <span v-else>{{ item.extra || '--' }}</span>
            </td>
            <td class="db-op-col">
              <div class="op-row">
                <button
                  class="db-edit-btn"
                  :disabled="recycleBusyId === item.id"
                  @click="restoreRecycleItem(item)"
                >
                  恢复
                </button>
                <button
                  class="db-purge-btn"
                  :disabled="recycleBusyId === item.id"
                  @click="purgeRecycleItem(item)"
                >
                  永久删除
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="db-pager">
      <button
        class="sa-btn sa-btn--ghost"
        :disabled="recyclePage <= 1 || recycleLoading"
        @click="recyclePage--; loadRecycle()"
      >
        上一页
      </button>
      <span class="db-page-num">{{ recyclePage }}</span>
      <button
        class="sa-btn sa-btn--ghost"
        :disabled="recyclePage * recyclePageSize >= recycleTotal || recycleLoading"
        @click="recyclePage++; loadRecycle()"
      >
        下一页
      </button>
    </div>
  </DbPageShell>
</template>
