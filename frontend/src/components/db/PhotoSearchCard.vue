<script setup lang="ts">
import { ref } from 'vue'
import { providersApi } from '@/api/providers'
import { artistsApi } from '@/api/artists'
import { groupsApi } from '@/api/groups'
import type { ProviderSearchItem } from '@/types/models'

// 艺人/组合工作台共用的「照片搜索」卡片：
// 按名称聚合检索外部候选（TheAudioDB / Deezer / iTunes / Wikidata / TMDB），
// 应用后下载头像并绑定到实体（不影响简介）。
const props = defineProps<{
  kind: 'artist' | 'group'
  entityId: number
  /** 预填搜索词（默认当前实体名） */
  initialQuery?: string
}>()

const emit = defineEmits<{
  (e: 'applied'): void
}>()

const q = ref(props.initialQuery || '')
const items = ref<ProviderSearchItem[]>([])
const searching = ref(false)
const applyingId = ref('')
const error = ref('')

async function search() {
  const term = q.value.trim()
  if (!term) return
  searching.value = true
  error.value = ''
  try {
    items.value = await providersApi.searchAudiodb(term, props.kind)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '照片搜索失败'
  } finally {
    searching.value = false
  }
}

async function apply(item: ProviderSearchItem) {
  error.value = ''
  applyingId.value = item.external_id
  try {
    const payload = { external_id: item.external_id, apply_biography: false, apply_image: true }
    if (props.kind === 'artist') {
      await artistsApi.fetchExternal(props.entityId, payload)
    } else {
      await groupsApi.fetchExternal(props.entityId, payload)
    }
    items.value = []
    q.value = ''
    emit('applied')
  } catch (e) {
    error.value = e instanceof Error ? e.message : '应用头像失败'
  } finally {
    applyingId.value = ''
  }
}
</script>

<template>
  <div>
    <div class="ps-search">
      <input
        v-model="q"
        type="text"
        class="sa-input"
        :placeholder="kind === 'artist' ? '输入艺人外文名搜索照片' : '输入组合外文名搜索照片'"
        @keyup.enter="search"
      />
      <button class="sa-btn-add" type="button" :disabled="!q.trim() || searching" @click="search">
        {{ searching ? '搜索中…' : '搜索' }}
      </button>
    </div>
    <div v-if="error" class="ps-error">{{ error }}</div>
    <div v-if="!items.length && !error" class="sa-empty">
      搜索外部站点（TheAudioDB / Deezer / iTunes / Wikidata / TMDB），点「应用」下载并绑定头像，不影响简介。
    </div>
    <div v-if="items.length" class="ps-results">
      <div v-for="item in items" :key="item.external_id" class="sa-row">
        <img v-if="item.thumbnail" :src="item.thumbnail" alt="" loading="lazy" />
        <span v-else class="ps-nopic">无图</span>
        <div class="ps-item-tx">
          <b>{{ item.name }}</b>
          <small>{{ [item.source, item.bio_excerpt].filter(Boolean).join(' · ') }}</small>
        </div>
        <button
          class="sa-btn-ghost"
          type="button"
          :disabled="applyingId === item.external_id"
          @click="apply(item)"
        >
          {{ applyingId === item.external_id ? '应用中…' : '应用' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 搜索行与结果行内的控件统一用全局口径（styles/db-controls.css）：
   .sa-input / .sa-btn-add / .sa-row / .sa-empty —— 这里只留本卡片特有的部分。 */
.ps-search { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
.ps-search > .sa-input { flex: 1; min-width: 0; }
.ps-error { margin: 0 0 8px; padding: 6px 10px; border-radius: 8px; background: rgba(208, 48, 80, 0.1); color: var(--dh-bad, #d03050); font-size: 11.5px; }
.ps-results { display: flex; flex-direction: column; gap: 8px; max-height: 340px; overflow-y: auto; }
.sa-row img { width: 44px; height: 44px; border-radius: 50%; object-fit: cover; flex-shrink: 0; }
.ps-nopic { width: 44px; height: 44px; border-radius: 50%; background: var(--sa-subtle); color: var(--sa-text-tertiary); font-size: 10px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.ps-item-tx { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.ps-item-tx b { font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ps-item-tx small { font-size: 10px; color: var(--sa-text-tertiary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>
