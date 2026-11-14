<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import SaHeader from '@/components/SaHeader.vue'
import SaPagination from '@/components/SaPagination.vue'
import SaGlassSelect from '@/components/SaGlassSelect.vue'
import SaFilterBar from '@/components/SaFilterBar.vue'
import SaFilterSearch from '@/components/SaFilterSearch.vue'
import type { GlassOption } from '@/components/SaGlassSelect.vue'
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
// 默认只显示有视频直接关联的组合（被翻唱的原唱不算）；切换状态记住偏好
const GROUPS_FILTER_KEY = 'groups:filter'
const onlyWithVideos = ref(localStorage.getItem(GROUPS_FILTER_KEY) !== '0')
const sortOrder = ref<'default' | 'name' | '-name'>(
  (localStorage.getItem('groups:sort') as 'default' | 'name' | '-name') || 'default',
)

// 流光空间玻璃下拉的选项（值仍是原来的字符串，接口参数不变）
const sortOptions: GlassOption[] = [
  { label: '默认排序', value: 'default' },
  { label: '名称 A→Z', value: 'name' },
  { label: '名称 Z→A', value: '-name' },
]
const rangeOptions: GlassOption[] = [
  { label: '仅有视频', value: 'videos', hint: '推荐' },
  { label: '显示全部', value: 'all' },
]
const rangeValue = computed(() => (onlyWithVideos.value ? 'videos' : 'all'))
function onRangeChange(v: string | number | null) {
  const next = v === 'videos'
  if (next === onlyWithVideos.value) return
  onlyWithVideos.value = next
  localStorage.setItem(GROUPS_FILTER_KEY, next ? '1' : '0')
  void load(1)
}
function onSortPick(v: string | number | null) {
  const next = (v || 'default') as 'default' | 'name' | '-name'
  if (next === sortOrder.value) return
  sortOrder.value = next
  localStorage.setItem('groups:sort', next)
  void load(1)
}

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
  return g.chinese_name || g.english_name || g.name || `#${g.id}`
}
/** 斜体拉丁名（跟在大字下面那一行） */
function latinOf(g: Group) {
  const latin = g.english_name || g.korean_name || g.name || ''
  return latin && latin !== displayName(g) ? latin : ''
}
function debutLabel(g: Group) {
  if (!g.debut_date) return ''
  const d = formatDate(g.debut_date)
  return d === '--' ? '' : d
}
/** 竖幅立绘取图：头像优先（方形裁 3:4 损失小），回退横幅。
 *  v=updated_at 作版本号：换海报后 URL 变化，不会被一年期 immutable 缓存顶住。 */
function portraitSrc(g: Group) {
  const v = encodeURIComponent(g.updated_at || '')
  if (g.avatar_path) return `${groupsApi.avatarUrl(g.id)}?w=480&v=${v}`
  if (g.banner_path) return `${groupsApi.bannerUrl(g.id)}?w=480&v=${v}`
  return ''
}
const brokenCovers = ref<Set<number>>(new Set())
function onCoverError(id: number) {
  brokenCovers.value = new Set(brokenCovers.value).add(id)
}
function hasCover(g: Group) {
  return !!portraitSrc(g) && !brokenCovers.value.has(g.id)
}
/** 悬停抽屉里的资料（只用列表接口已有字段） */
function drawerFacts(g: Group) {
  const facts: { label: string; value: string }[] = []
  if (g.group_type) facts.push({ label: '类型', value: g.group_type })
  if (g.origin_country) facts.push({ label: '国家', value: g.origin_country })
  const alias = (g.aliases || []).find((x) => x && x !== displayName(g))
  if (alias) facts.push({ label: '别名', value: alias })
  if (g.gender_type) facts.push({ label: '性别', value: g.gender_type })
  return facts
}

async function load(targetPage: number) {
  if (loading.value) return
  loading.value = true
  try {
    const res = await groupsApi.list({
      q: keyword.value || undefined,
      page: targetPage,
      page_size: pageSize,
      filter: onlyWithVideos.value ? 'videos' : 'all',
      sort: sortOrder.value === 'default' ? undefined : sortOrder.value,
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

onMounted(() => {
  void load(1)
})
</script>

<template>
  <div class="gp-page">
    <SaHeader />
    <div class="gp-wrap">
      <header class="gp-head">
        <div class="gp-title-wrap">
          <h1 class="gp-title">组合画廊</h1>
          <span class="gp-count">{{ total }}</span>
        </div>
        <SaFilterBar class="gp-actions">
          <SaGlassSelect
            :model-value="rangeValue"
            :options="rangeOptions"
            prefix="范围"
            width="11rem"
            aria-label="筛选范围"
            @update:model-value="onRangeChange"
          />
          <span class="sa-fb-sep" />
          <SaGlassSelect
            :model-value="sortOrder"
            :options="sortOptions"
            prefix="排序"
            align="right"
            width="12rem"
            aria-label="排序方式"
            @update:model-value="onSortPick"
          />
          <span class="sa-fb-sep" />
          <SaFilterSearch v-model="searchInput" placeholder="组合 / 韩文…" aria-label="检索组合" />
        </SaFilterBar>
      </header>

      <div v-if="loading && !items.length" class="gp-loading">加载中…</div>

      <template v-else-if="items.length">
        <!-- 与艺人画廊同构：3:4 竖幅立绘墙 -->
        <div class="gp-gallery">
          <article
            v-for="(g, i) in items"
            :key="g.id"
            class="gc"
            :style="{ animationDelay: `${i * 35}ms` }"
            @click="goGroup(g)"
          >
            <div class="gc-frame">
              <img
                v-if="hasCover(g)"
                class="gc-img"
                :src="portraitSrc(g)"
                alt=""
                loading="lazy"
                @error="onCoverError(g.id)"
              />
              <div v-else class="gc-fallback" :style="avatarStyle(g.id)">
                {{ initialOf(displayName(g)) }}
              </div>
              <div class="gc-shade" aria-hidden="true" />

              <div class="gc-drawer">
                <div class="gc-role">{{ g.group_type || '组合' }}</div>
                <div class="gc-facts">
                  <span v-for="f in drawerFacts(g)" :key="f.label" class="gc-fact">{{ f.value }}</span>
                </div>
              </div>
            </div>

            <div class="gc-foot">
              <div class="gc-names">
                <h3 class="gc-name">{{ displayName(g) }}</h3>
                <div v-if="latinOf(g)" class="gc-en">{{ latinOf(g) }}</div>
              </div>
              <span v-if="debutLabel(g)" class="gc-debut">{{ debutLabel(g) }}</span>
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

      <div v-else class="gp-empty">
        <GroupOutlined :size="36" />
        <p>没有找到组合</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.gp-page {
  min-height: 100vh;
  background: var(--sa-bg);
  color: var(--sa-text-primary);
}
.gp-wrap {
  /* 不居中：左边缘与顶栏品牌 idolmatrix 的 i 同一竖线（任意屏宽） */
  max-width: none;
  margin: 0;
  padding: 0 var(--sa-gutter, 36px) 72px;
}

/* ===== 刊头 ===== */
.gp-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 14px 20px;
  padding: 30px 0 16px;
  border-bottom: 1px solid var(--sa-border-subtle);
  margin-bottom: 30px;
}
.gp-title-wrap {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.gp-title {
  margin: 0;
  font-family: 'Noto Serif SC', 'Songti SC', var(--sa-font-sans, sans-serif);
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.01em;
}
.gp-count {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  color: var(--sa-text-tertiary);
}

.gp-loading,
.gp-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 90px 0;
  color: var(--sa-text-tertiary);
  font-size: 13px;
}

/* ===== 画廊：与艺人页同构的 3:4 竖幅墙 ===== */
.gp-gallery {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 26px 16px;
}
@media (min-width: 640px) {
  .gp-gallery {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 30px 20px;
  }
}
@media (min-width: 1024px) {
  .gp-gallery {
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 34px 24px;
  }
}
@media (min-width: 1360px) {
  .gp-gallery {
    grid-template-columns: repeat(5, minmax(0, 1fr));
  }
}
@media (min-width: 1700px) {
  .gp-gallery {
    grid-template-columns: repeat(6, minmax(0, 1fr));
  }
}

.gc {
  cursor: pointer;
  opacity: 0;
  animation: gcReveal 0.55s cubic-bezier(0.19, 1, 0.22, 1) forwards;
}
@keyframes gcReveal {
  from {
    opacity: 0;
    transform: translateY(16px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

.gc-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 10px;
  color: var(--sa-text-tertiary);
}
.gc-code {
  color: var(--sa-accent);
  font-weight: 700;
  letter-spacing: 0.06em;
}
.gc-tag {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.gc-frame {
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
.gc:hover .gc-frame {
  transform: translateY(-6px);
  border-color: color-mix(in oklab, var(--sa-text-primary) 38%, transparent);
  box-shadow: 0 26px 55px -12px rgba(0, 0, 0, 0.55);
}
.gc-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  transition: transform 0.95s cubic-bezier(0.19, 1, 0.22, 1);
}
.gc:hover .gc-img {
  transform: scale(1.05);
}
.gc-fallback {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 44px;
  font-weight: 700;
  color: rgba(255, 255, 255, 0.9);
}
.gc-shade {
  position: absolute;
  inset: 0;
  background: linear-gradient(to top, rgba(0, 0, 0, 0.9) 0%, transparent 52%);
  pointer-events: none;
}
.gc-cams {
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
.gc-drawer {
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
.gc:hover .gc-drawer {
  transform: translateY(0);
}
.gc-role {
  margin-bottom: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 9.5px;
  letter-spacing: 0.1em;
  color: #c4b5fd;
}
.gc-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.gc-fact {
  padding: 2px 7px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(255, 255, 255, 0.06);
  font-size: 10px;
  color: rgba(255, 255, 255, 0.88);
}

.gc-foot {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-top: 10px;
  padding: 0 2px;
}
.gc-names {
  min-width: 0;
}
.gc-name {
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
.gc:hover .gc-name {
  color: var(--sa-accent);
}
.gc-en {
  margin-top: 2px;
  font-family: 'Instrument Serif', 'Playfair Display', serif;
  font-style: italic;
  font-size: 12.5px;
  color: var(--sa-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.gc-debut {
  flex-shrink: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 9.5px;
  color: var(--sa-text-tertiary);
}

@media (max-width: 640px) {
  .gp-wrap {
    padding: 0 16px 48px;
  }
  .gc-frame {
    border-radius: 16px;
  }
}
</style>
