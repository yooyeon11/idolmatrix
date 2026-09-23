<script setup lang="ts">
import { ref, watch } from 'vue'
import { NButton, NInput, NModal, NSpin, useMessage } from 'naive-ui'
import { musicVideosApi } from '@/api/musicVideos'
import type { MusicVideo } from '@/types/models'
import { VIDEO_TYPE_LABEL } from '@/types/models'
import { IMG_W_THUMB } from '@/utils/imageSizes'

const props = withDefaults(
  defineProps<{
    show: boolean
    /** 已选中的视频 id（顺序即轮播顺序） */
    selected: number[]
    max?: number
  }>(),
  { max: 6 },
)

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'confirm', ids: number[]): void
}>()

const message = useMessage()

const PAGE_SIZE = 20
const loading = ref(false)
const videos = ref<MusicVideo[]>([])
const total = ref(0)
const page = ref(1)
const keyword = ref('')
// 点选顺序即轮播顺序
const picked = ref<number[]>([])
const brokenThumbs = ref<Set<number>>(new Set())
let searchTimer: ReturnType<typeof setTimeout> | undefined

function cardLabel(mv: MusicVideo) {
  const type = VIDEO_TYPE_LABEL[mv.video_type] || mv.video_type || ''
  const who =
    (mv.groups || []).map((g) => g.chinese_name || g.name).find(Boolean) ||
    (mv.artists || []).map((a) => a.chinese_name || a.name).find(Boolean) ||
    ''
  return [type, who].filter(Boolean).join(' · ')
}

async function load() {
  loading.value = true
  try {
    const res = await musicVideosApi.list({
      q: keyword.value.trim() || undefined,
      ingestion_status: 'library',
      page: page.value,
      page_size: PAGE_SIZE,
    })
    videos.value = res.items
    total.value = res.total
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

function onSearch(q: string) {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    keyword.value = q
    page.value = 1
    void load()
  }, 280)
}

function toggle(mv: MusicVideo) {
  const idx = picked.value.indexOf(mv.id)
  if (idx >= 0) {
    picked.value = picked.value.filter((id) => id !== mv.id)
    return
  }
  if (picked.value.length >= props.max) {
    message.warning(`最多选择 ${props.max} 条视频`)
    return
  }
  picked.value = [...picked.value, mv.id]
}

function prevPage() {
  if (page.value <= 1) return
  page.value -= 1
  void load()
}

function nextPage() {
  if (page.value * PAGE_SIZE >= total.value) return
  page.value += 1
  void load()
}

function confirmPick() {
  emit('confirm', [...picked.value])
  emit('update:show', false)
}

function thumbSrc(mv: MusicVideo) {
  if (brokenThumbs.value.has(mv.id)) return undefined
  return musicVideosApi.thumbnailUrl(mv.id, mv.file_hash || mv.file_size, IMG_W_THUMB)
}

function onThumbError(id: number) {
  brokenThumbs.value = new Set(brokenThumbs.value).add(id)
}

watch(
  () => props.show,
  (open) => {
    if (open) {
      picked.value = [...props.selected]
      keyword.value = ''
      page.value = 1
      brokenThumbs.value = new Set()
      void load()
    }
  },
)
</script>

<template>
  <n-modal
    :show="show"
    preset="card"
    title="选择轮播视频"
    style="width: min(920px, 94vw)"
    :bordered="false"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <div class="hvp">
      <div class="hvp-head">
        <n-input
          :value="keyword"
          placeholder="搜索已入库视频（标题 / 艺人 / 歌曲）"
          clearable
          size="small"
          style="max-width: 320px"
          @update:value="onSearch"
        />
        <span class="hvp-tip">按点选顺序轮播，第一条为主打视频</span>
      </div>

      <div v-if="loading" class="hvp-empty">
        <n-spin size="medium" />
        <p>正在加载视频…</p>
      </div>

      <template v-else>
        <div v-if="videos.length" class="hvp-grid">
          <button
            v-for="mv in videos"
            :key="mv.id"
            type="button"
            class="hvp-cell"
            :class="{ 'hvp-cell--on': picked.includes(mv.id) }"
            @click="toggle(mv)"
          >
            <div class="hvp-cover">
              <img
                v-if="thumbSrc(mv)"
                :src="thumbSrc(mv)!"
                :alt="mv.name"
                loading="lazy"
                @error="onThumbError(mv.id)"
              />
              <span v-else class="hvp-nocover">无封面</span>
              <span v-if="picked.includes(mv.id)" class="hvp-order">
                {{ picked.indexOf(mv.id) + 1 }}
              </span>
            </div>
            <div class="hvp-name" :title="mv.name">{{ mv.name }}</div>
            <div class="hvp-meta" :title="cardLabel(mv)">{{ cardLabel(mv) }}</div>
          </button>
        </div>
        <div v-else class="hvp-empty">
          <p>没有匹配的视频</p>
        </div>

        <div v-if="videos.length" class="hvp-pager">
          <n-button size="tiny" quaternary :disabled="page <= 1" @click="prevPage">上一页</n-button>
          <span class="hvp-page">{{ page }} / {{ Math.max(1, Math.ceil(total / PAGE_SIZE)) }}</span>
          <n-button
            size="tiny"
            quaternary
            :disabled="page * PAGE_SIZE >= total"
            @click="nextPage"
          >
            下一页
          </n-button>
        </div>
      </template>
    </div>

    <template #footer>
      <div class="hvp-foot">
        <div class="hvp-picked">
          已选 {{ picked.length }} / {{ max }}
          <button
            v-if="picked.length"
            type="button"
            class="hvp-clear"
            @click="picked = []"
          >
            清空
          </button>
        </div>
        <div>
          <n-button style="margin-right: 8px" @click="emit('update:show', false)">取消</n-button>
          <n-button type="primary" @click="confirmPick">确定</n-button>
        </div>
      </div>
    </template>
  </n-modal>
</template>

<style scoped>
.hvp {
  min-height: 220px;
}

.hvp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.hvp-tip {
  font-size: 12px;
  color: var(--n-text-color-disabled, #999);
}

.hvp-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  max-height: 56vh;
  overflow-y: auto;
  padding: 2px;
}

@media (max-width: 720px) {
  .hvp-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

.hvp-cell {
  text-align: left;
  padding: 0;
  border: 2px solid transparent;
  border-radius: 10px;
  background: transparent;
  cursor: pointer;
  transition: border-color 0.15s ease, transform 0.15s ease;
}

.hvp-cell:hover {
  transform: translateY(-2px);
}

.hvp-cell--on {
  border-color: var(--primary-color, #18a058);
}

.hvp-cover {
  position: relative;
  aspect-ratio: 16 / 9;
  border-radius: 8px;
  overflow: hidden;
  background: var(--sa-subtle, #f2f2f2);
}

.hvp-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.hvp-nocover {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: var(--n-text-color-disabled, #999);
}

.hvp-order {
  position: absolute;
  top: 6px;
  left: 6px;
  width: 20px;
  height: 20px;
  border-radius: 999px;
  background: var(--primary-color, #18a058);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}

.hvp-name {
  margin-top: 6px;
  font-size: 12px;
  font-weight: 500;
  color: var(--n-text-color, #333);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.hvp-meta {
  font-size: 11px;
  color: var(--n-text-color-disabled, #999);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.hvp-pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  margin-top: 12px;
}

.hvp-page {
  font-size: 12px;
  color: var(--n-text-color-disabled, #999);
  font-variant-numeric: tabular-nums;
}

.hvp-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 48px 0;
  color: var(--n-text-color-disabled, #999);
  font-size: 13px;
}

.hvp-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.hvp-picked {
  font-size: 12px;
  color: var(--n-text-color-disabled, #999);
  display: flex;
  align-items: center;
  gap: 10px;
}

.hvp-clear {
  border: none;
  background: none;
  padding: 0;
  font-size: 12px;
  color: var(--n-text-color-3, #666);
  cursor: pointer;
  text-decoration: underline;
}

.hvp-clear:hover {
  color: var(--primary-color, #18a058);
}
</style>
