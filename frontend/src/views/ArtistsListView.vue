<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import SaHeader from '@/components/SaHeader.vue'
import SaPagination from '@/components/SaPagination.vue'
import SaGlassSelect from '@/components/SaGlassSelect.vue'
import SaFilterBar from '@/components/SaFilterBar.vue'
import SaFilterSearch from '@/components/SaFilterSearch.vue'
import type { GlassOption } from '@/components/SaGlassSelect.vue'
import { artistsApi } from '@/api/artists'
import type { Artist } from '@/types/models'
import { formatDate } from '@/utils/format'
import { artistPath } from '@/utils/routes'
import { PersonOutlined } from '@/components/icons'

const router = useRouter()

const items = ref<Artist[]>([])
const total = ref(0)
const pageNum = ref(1)
const pageSize = 24
const loading = ref(false)
const keyword = ref('')
const searchInput = ref('')

// 「仅显示有关联视频的艺人」开关：默认开启（隐藏 AI 分析组合等产生的无视频艺人）
const ONLY_WITH_VIDEOS_KEY = 'artists:only_with_videos'
const onlyWithVideos = ref(
  (() => {
    const saved = localStorage.getItem(ONLY_WITH_VIDEOS_KEY)
    return saved === null ? true : saved === '1'
  })(),
)
// 流光空间玻璃下拉的选项（值仍是原来的字符串，接口参数不变）
const sortOptions: GlassOption[] = [
  { label: '默认排序', value: 'default' },
  { label: '姓名 A→Z', value: 'name' },
  { label: '姓名 Z→A', value: '-name' },
]
const rangeOptions: GlassOption[] = [
  { label: '仅有关联视频', value: 'videos', hint: '推荐' },
  { label: '显示全部', value: 'all' },
]
const rangeValue = computed(() => (onlyWithVideos.value ? 'videos' : 'all'))
function onRangeChange(v: string | number | null) {
  const next = v === 'videos'
  if (next === onlyWithVideos.value) return
  onlyWithVideos.value = next
  localStorage.setItem(ONLY_WITH_VIDEOS_KEY, next ? '1' : '0')
  load(1)
}

// ===== 按姓名排序(默认 = sort_name 库内口径) =====
const ARTISTS_SORT_KEY = 'artists:sort'
const artistSort = ref<'default' | 'name' | '-name'>(
  (localStorage.getItem(ARTISTS_SORT_KEY) as 'default' | 'name' | '-name') || 'default',
)

function onSortPick(v: string | number | null) {
  const next = (v || 'default') as 'default' | 'name' | '-name'
  if (next === artistSort.value) return
  artistSort.value = next
  localStorage.setItem(ARTISTS_SORT_KEY, next)
  load(1)
}

const avatarPalettes = [
  ['#2a2140', '#15151b'],
  ['#22242a', '#15151b'],
  ['#2a2220', '#15151b'],
  ['#1e2a2e', '#15151b'],
  ['#241e2e', '#15151b'],
]
function avatarStyle(id: number) {
  const [c1, c2] = avatarPalettes[id % avatarPalettes.length]
  return { background: `linear-gradient(135deg, ${c1}, ${c2})` }
}
function initialOf(name?: string | null) {
  return (name || '?').trim().charAt(0).toUpperCase()
}

function displayName(a: Artist) {
  return a.chinese_name || a.stage_name || a.name || `#${a.id}`
}
/** 斜体拉丁名（跟在大字下面那一行） */
function latinOf(a: Artist) {
  const latin = a.english_name || a.stage_name || a.name || ''
  return latin && latin !== displayName(a) ? latin : ''
}
function debutLabel(a: Artist) {
  if (!a.debut_date) return ''
  const d = formatDate(a.debut_date)
  return d === '--' ? '' : d
}
function portraitSrc(a: Artist) {
  // 3:4 竖幅立绘：卡宽约 240px，480 覆盖 2x DPR。
  // v=updated_at 作为版本号：换头像/裁切后 updated_at 变化，URL 随之改变，
  // 不会再被浏览器/CDN 的旧缓存顶住（值稳定时仍能命中缓存）。
  return a.avatar_path
    ? `${artistsApi.avatarUrl(a.id)}?w=480&v=${encodeURIComponent(a.updated_at || '')}`
    : ''
}
const brokenCovers = ref<Set<number>>(new Set())
function onCoverError(id: number) {
  brokenCovers.value = new Set(brokenCovers.value).add(id)
}
function hasCover(a: Artist) {
  return !!portraitSrc(a) && !brokenCovers.value.has(a.id)
}
function bioOf(a: Artist) {
  return (a.tagline || a.description || '').replace(/\{BR\}/g, ' ').trim()
}
function roleOf(a: Artist) {
  return a.occupation || (a.gender === 'Female' ? '女艺人' : a.gender === 'Male' ? '男艺人' : '艺人')
}

async function load(targetPage: number) {
  if (loading.value) return
  loading.value = true
  try {
    const res = await artistsApi.list({
      q: keyword.value || undefined,
      page: targetPage,
      page_size: pageSize,
      only_with_videos: onlyWithVideos.value || undefined,
      sort: artistSort.value === 'default' ? undefined : artistSort.value,
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

function goArtist(a: { uid: string }) {
  router.push(artistPath(a.uid))
}

onMounted(() => load(1))
</script>

<template>
  <div class="ap-page">
    <SaHeader />
    <div class="ap-wrap">
      <header class="ap-head">
        <div class="ap-title-wrap">
          <h1 class="ap-title">艺人画廊</h1>
          <span class="ap-count">{{ total }}</span>
        </div>
        <SaFilterBar class="ap-actions">
          <SaGlassSelect
            :model-value="rangeValue"
            :options="rangeOptions"
            prefix="范围"
            width="12rem"
            aria-label="筛选范围"
            @update:model-value="onRangeChange"
          />
          <span class="sa-fb-sep" />
          <SaGlassSelect
            :model-value="artistSort"
            :options="sortOptions"
            prefix="排序"
            align="right"
            width="12rem"
            aria-label="排序方式"
            @update:model-value="onSortPick"
          />
          <span class="sa-fb-sep" />
          <SaFilterSearch v-model="searchInput" placeholder="艺人 / 韩文…" aria-label="检索艺人" />
        </SaFilterBar>
      </header>

      <div v-if="loading && !items.length" class="ap-loading">加载中…</div>

      <template v-else-if="items.length">
        <!-- 方案 I 艺人视图：3:4 竖幅黄金立绘墙 -->
        <div class="ap-gallery">
          <article
            v-for="(a, i) in items"
            :key="a.id"
            class="ac"
            :style="{ animationDelay: `${i * 35}ms` }"
            @click="goArtist(a)"
          >
            <div class="ac-frame">
              <img
                v-if="hasCover(a)"
                class="ac-img"
                :src="portraitSrc(a)"
                alt=""
                loading="lazy"
                @error="onCoverError(a.id)"
              />
              <div v-else class="ac-fallback" :style="avatarStyle(a.id)">
                {{ initialOf(displayName(a)) }}
              </div>
              <div class="ac-shade" aria-hidden="true" />

              <!-- 悬停从底部滑出的一句话短评 -->
              <div class="ac-drawer">
                <div class="ac-role">{{ roleOf(a) }}</div>
                <p class="ac-bio">{{ bioOf(a) || '暂无简介' }}</p>
              </div>
            </div>

            <div class="ac-foot">
              <div class="ac-names">
                <h3 class="ac-name">{{ displayName(a) }}</h3>
                <div v-if="latinOf(a)" class="ac-en">{{ latinOf(a) }}</div>
              </div>
              <span v-if="debutLabel(a)" class="ac-debut">{{ debutLabel(a) }}</span>
            </div>
          </article>
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

      <div v-else class="ap-empty">
        <PersonOutlined :size="36" />
        <p>没有找到艺人</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ap-page {
  min-height: 100vh;
  background: var(--sa-bg);
  color: var(--sa-text-primary);
}
.ap-wrap {
  /* 不居中：左边缘与顶栏品牌 idolmatrix 的 i 同一竖线（任意屏宽） */
  max-width: none;
  margin: 0;
  padding: 0 var(--sa-gutter, 36px) 72px;
}

/* ===== 刊头 ===== */
.ap-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 14px 20px;
  padding: 30px 0 16px;
  border-bottom: 1px solid var(--sa-border-subtle);
  margin-bottom: 30px;
}
.ap-title-wrap {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.ap-title {
  margin: 0;
  font-family: 'Noto Serif SC', 'Songti SC', var(--sa-font-sans, sans-serif);
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.01em;
}
.ap-count {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  color: var(--sa-text-tertiary);
}

.ap-loading,
.ap-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 90px 0;
  color: var(--sa-text-tertiary);
  font-size: 13px;
}

/* ===== 立绘墙：3:4 竖幅 ===== */
.ap-gallery {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 26px 16px;
}
@media (min-width: 640px) {
  .ap-gallery {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 30px 20px;
  }
}
@media (min-width: 1024px) {
  .ap-gallery {
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 34px 24px;
  }
}
@media (min-width: 1360px) {
  .ap-gallery {
    grid-template-columns: repeat(5, minmax(0, 1fr));
  }
}
@media (min-width: 1700px) {
  .ap-gallery {
    grid-template-columns: repeat(6, minmax(0, 1fr));
  }
}

.ac {
  cursor: pointer;
  opacity: 0;
  animation: acReveal 0.55s cubic-bezier(0.19, 1, 0.22, 1) forwards;
}
@keyframes acReveal {
  from {
    opacity: 0;
    transform: translateY(16px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

.ac-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 10px;
  color: var(--sa-text-tertiary);
}
.ac-code {
  color: var(--sa-accent);
  font-weight: 700;
  letter-spacing: 0.06em;
}
.ac-tag {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ac-frame {
  position: relative;
  aspect-ratio: 3 / 4.1;
  border-radius: 20px;
  overflow: hidden;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  transition:
    transform 0.6s var(--spring, cubic-bezier(0.16, 1, 0.3, 1)),
    border-color 0.35s ease,
    box-shadow 0.6s var(--spring, cubic-bezier(0.16, 1, 0.3, 1));
}
.ac:hover .ac-frame {
  transform: translateY(-6px);
  border-color: color-mix(in oklab, var(--sa-text-primary) 38%, transparent);
  box-shadow: 0 26px 55px -12px rgba(0, 0, 0, 0.55);
}
.ac-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  transition: transform 0.95s cubic-bezier(0.19, 1, 0.22, 1);
}
.ac:hover .ac-img {
  transform: scale(1.05);
}
.ac-fallback {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 44px;
  font-weight: 700;
  color: rgba(255, 255, 255, 0.9);
}
.ac-shade {
  position: absolute;
  inset: 0;
  background: linear-gradient(to top, rgba(0, 0, 0, 0.9) 0%, transparent 52%);
  pointer-events: none;
}
.ac-cams {
  position: absolute;
  top: 10px;
  right: 10px;
  padding: 3px 8px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  background: rgba(0, 0, 0, 0.65);
  backdrop-filter: blur(8px);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 9.5px;
  color: #6ee7b7;
}

/* 悬停抽屉 */
.ac-drawer {
  position: absolute;
  inset-inline: 0;
  bottom: 0;
  padding: 10px 12px 12px;
  background: rgba(13, 14, 20, 0.95);
  border-top: 1px solid rgba(255, 255, 255, 0.15);
  backdrop-filter: blur(14px);
  transform: translateY(101%);
  transition: transform 0.5s cubic-bezier(0.19, 1, 0.22, 1);
}
.ac:hover .ac-drawer {
  transform: translateY(0);
}
.ac-role {
  margin-bottom: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 9.5px;
  letter-spacing: 0.1em;
  color: #c4b5fd;
}
.ac-bio {
  margin: 0;
  font-size: 11px;
  line-height: 1.55;
  color: rgba(255, 255, 255, 0.88);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.ac-foot {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-top: 10px;
  padding: 0 2px;
}
.ac-names {
  min-width: 0;
}
.ac-name {
  margin: 0;
  font-family: 'Noto Serif SC', 'Songti SC', 'Playfair Display', serif;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--sa-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: color 0.3s ease;
}
.ac:hover .ac-name {
  color: var(--sa-accent);
}
.ac-en {
  margin-top: 2px;
  font-family: 'Instrument Serif', 'Playfair Display', serif;
  font-style: italic;
  font-size: 12.5px;
  color: var(--sa-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ac-debut {
  flex-shrink: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 9.5px;
  color: var(--sa-text-tertiary);
}

@media (max-width: 640px) {
  .ap-wrap {
    padding: 0 16px 48px;
  }
  .ac-frame {
    border-radius: 16px;
  }
}
</style>
