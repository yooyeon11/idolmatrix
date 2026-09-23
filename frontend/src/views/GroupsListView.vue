<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import SaHeader from '@/components/SaHeader.vue'
import SaPagination from '@/components/SaPagination.vue'
import { groupsApi } from '@/api/groups'
import type { Group } from '@/types/models'
import { formatDate } from '@/utils/format'
import { groupPath } from '@/utils/routes'
import { GroupOutlined } from '@/components/icons'

const router = useRouter()

const items = ref<Group[]>([])
const total = ref(0)
const pageNum = ref(1)
const pageSize = 24
const loading = ref(false)
const keyword = ref('')
const searchInput = ref('')

const avatarPalettes = [
  ['#232a1e', '#15151b'],
  ['#20233a', '#15151b'],
  ['#2a1e2e', '#15151b'],
  ['#1e2a2a', '#15151b'],
  ['#2a241e', '#15151b'],
]
function avatarStyle(id: number) {
  const [c1, c2] = avatarPalettes[id % avatarPalettes.length]
  return { background: `linear-gradient(135deg, ${c1}, ${c2})` }
}
function initialOf(name?: string | null) {
  return (name || '?').trim().charAt(0).toUpperCase()
}

function displayName(g: Group) {
  return g.chinese_name || g.name || `#${g.id}`
}
function displaySub(g: Group) {
  const parts: string[] = []
  const name = g.chinese_name || g.name
  if (g.english_name && g.english_name !== name) parts.push(g.english_name)
  if (g.group_type) parts.push(g.group_type)
  if (g.debut_date) {
    const d = formatDate(g.debut_date)
    if (d !== '--') parts.push(`${d} 出道`)
  }
  return parts.join(' · ')
}
function avatarSrc(g: Group) {
  // w=256：列表卡显示尺寸约 96px，256 覆盖 2x DPR；不传 w 会直出 audiodb 抓的
  // 高清原图（中位 400+KB/张），几十张列表页一进就拉几 MB。不带 ?t= 时间戳，
  // 让 ETag 条件请求生效（换头像后 mtime 变，304 协商自动拿新图）。
  return g.avatar_path ? `${groupsApi.avatarUrl(g.id)}?w=256` : ''
}

async function load(targetPage: number) {
  if (loading.value) return
  loading.value = true
  try {
    const res = await groupsApi.list({
      q: keyword.value || undefined,
      page: targetPage,
      page_size: pageSize,
    })
    items.value = res.items
    total.value = res.total
    pageNum.value = targetPage
  } finally {
    loading.value = false
  }
}

function onPageChange(p: number) {
  void load(p).then(() => window.scrollTo({ top: 0 }))
}

let debounceTimer: ReturnType<typeof setTimeout> | undefined
watch(searchInput, (v) => {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    keyword.value = v.trim()
    load(1)
  }, 350)
})

function goGroup(g: { uid: string }) {
  router.push(groupPath(g.uid))
}

onMounted(() => load(1))
</script>

<template>
  <div class="mv-page">
    <SaHeader />
    <div class="mv-container">
      <div class="page-head">
        <div class="page-title-wrap">
          <h1 class="page-title">组合</h1>
          <span class="page-count">{{ total }}</span>
        </div>
        <input
          v-model="searchInput"
          class="search-input"
          type="text"
          placeholder="搜索组合名称…"
        />
      </div>

      <div v-if="loading && !items.length" class="list-loading">加载中…</div>

      <template v-else-if="items.length">
        <div class="card-grid">
          <button
            v-for="g in items"
            :key="g.id"
            class="card"
            @click="goGroup(g)"
          >
            <div class="card-avatar" :style="avatarStyle(g.id)">
              <img
                v-if="avatarSrc(g)"
                class="card-avatar-img"
                :src="avatarSrc(g)"
                alt=""
              />
              <template v-else>{{ initialOf(displayName(g)) }}</template>
            </div>
            <div class="card-info">
              <div class="card-name">{{ displayName(g) }}</div>
              <div class="card-meta">{{ displaySub(g) || '组合' }}</div>
            </div>
          </button>
        </div>

        <SaPagination
          v-if="items.length"
          :total="total"
          :page="pageNum"
          :page-size="pageSize"
          :disabled="loading"
          :show-total="false"
          @update:page="onPageChange"
        />
      </template>

      <div v-else class="empty-state">
        <GroupOutlined :size="40" />
        <p>没有找到组合</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mv-page {
  min-height: 100vh;
}
.mv-container {
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 32px 64px;
}

.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 36px 0 24px;
}
.page-title-wrap {
  display: flex;
  align-items: center;
  gap: 10px;
}
.page-title {
  margin: 0;
  font-size: 28px;
  font-weight: 700;
  color: var(--sa-text-primary);
  letter-spacing: -0.02em;
}
/* 独立徽标样式：与标题拉开层次，避免读成“标题+数字”粘在一起 */
.page-count {
  font-size: 13px;
  color: var(--sa-text-secondary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  padding: 2px 10px;
  border-radius: 9999px;
  background: var(--sa-subtle);
  border: 1px solid var(--sa-border-subtle);
  line-height: 1.6;
}
.search-input {
  width: 240px;
  padding: 9px 16px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 9999px;
  background: var(--sa-elevated);
  color: var(--sa-text-primary);
  font-size: 14px;
  outline: none;
  transition: border-color 0.2s;
}
.search-input:focus {
  border-color: var(--sa-accent);
}
.search-input::placeholder {
  color: var(--sa-text-tertiary);
}

.list-loading {
  padding: 60px 0;
  text-align: center;
  color: var(--sa-text-tertiary);
  font-size: 14px;
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 20px;
}
.card {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 16px;
  background: var(--sa-elevated);
  padding: 14px;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.2s, transform 0.2s;
}
.card:hover {
  border-color: var(--sa-accent);
  transform: translateY(-2px);
}
.card-avatar {
  width: 100%;
  aspect-ratio: 1;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 40px;
  font-weight: 700;
  color: #fff;
  overflow: hidden;
}
.card-avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.card-info {
  margin-top: 12px;
  min-width: 0;
}
.card-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--sa-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card-meta {
  margin-top: 4px;
  font-size: 12px;
  color: var(--sa-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 80px 0;
  color: var(--sa-text-tertiary);
}
.empty-state p {
  margin: 0;
  font-size: 14px;
}

@media (max-width: 640px) {
  .mv-container {
    padding: 0 16px 48px;
  }
  .page-head {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }
  .search-input {
    width: 100%;
  }
  .card-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }
  .card {
    padding: 10px;
    min-width: 0;
  }
  .card-avatar {
    font-size: 28px;
  }
  .card-name {
    font-size: 14px;
  }
  .card-meta {
    font-size: 11px;
  }
}
</style>
