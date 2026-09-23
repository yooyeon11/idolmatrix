<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import SaHeader from '@/components/SaHeader.vue'
import SaPagination from '@/components/SaPagination.vue'
import SaDensitySwitch from '@/components/SaDensitySwitch.vue'
import VideoCard from '@/components/VideoCard.vue'
import { musicVideosApi } from '@/api/musicVideos'
import type { MusicVideo } from '@/types/models'
import { useGridDensity, useGridPageSize } from '@/composables/useGridDensity'
import { CloseOutlined, MovieOutlined, SearchOutlined, SortOutlined } from '@/components/icons'

const message = useMessage()
const loading = ref(false)
const items = ref<MusicVideo[]>([])
const total = ref(0)
const pageNum = ref(1)
const searchQuery = ref('')
const loadError = ref('')
const sortOrder = ref('created_desc')
// 与浏览页排序选项保持一致
const SORT_OPTIONS = [
  { value: 'created_desc', label: '最近上传' },
  { value: 'performance_date_desc', label: '表演日期（新→旧）' },
  { value: 'performance_date_asc', label: '表演日期（旧→新）' },
  { value: 'published_date_desc', label: '上传日期（新→旧）' },
  { value: 'published_date_asc', label: '上传日期（旧→新）' },
]

// ===== 移动端紧凑工具栏（与浏览页同一套交互）=====
// ≤640px：搜索框在最左，排序 / 宽松度收成 30px 圆形图标与搜索同一行、不显示文字
// （排序走下拉菜单，宽松度点击循环档位），原来的 ⚙ 底部抽屉已下线。
const narrowMq = window.matchMedia('(max-width: 640px)')
const isNarrow = ref(narrowMq.matches)
function onNarrowChange(e: MediaQueryListEvent) {
  isNarrow.value = e.matches
}

const sortDropdownOptions = computed(() =>
  SORT_OPTIONS.map((o) => ({ label: o.label, key: o.value })),
)
function onSortSelect(key: string | number) {
  sortOrder.value = String(key)
}
// 图标本身不显示文字，当前排序值写进 title / aria
const sortIconLabel = computed(
  () => `排序：${SORT_OPTIONS.find((o) => o.value === sortOrder.value)?.label ?? '最近上传'}`,
)
const { density } = useGridDensity()
// 网格列数实测 → page_size = 列数 × 行数：整页恰好铺满网格，最后一行不再缺位
const gridEl = ref<HTMLElement | null>(null)
function bindGrid(el: unknown) {
  gridEl.value = (el as HTMLElement | null) ?? null
}
const { pageSize } = useGridPageSize(gridEl)
let loadSeq = 0
let searchDebounce: ReturnType<typeof setTimeout> | undefined

async function load(targetPage: number) {
  const seq = ++loadSeq
  loading.value = true
  loadError.value = ''
  try {
    const res = await musicVideosApi.list({
      ingestion_status: 'library',
      is_short: true,
      page: targetPage,
      page_size: pageSize.value,
      q: searchQuery.value.trim() || undefined,
      sort: sortOrder.value === 'created_desc' ? undefined : sortOrder.value,
    })
    if (seq !== loadSeq) return
    total.value = res.total
    items.value = res.items
    pageNum.value = targetPage
  } catch (e) {
    if (seq !== loadSeq) return
    loadError.value = (e as Error).message
    message.error(loadError.value)
    if (targetPage === 1) items.value = []
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function onPageChange(p: number) {
  void load(p).then(() => window.scrollTo({ top: 0 }))
}

// page_size 变化（首次测量完成 / 视口跨列数边界 / 切换密度档位）：回到第 1 页重新加载
watch(pageSize, (v) => {
  if (v > 0) void load(1)
})
watch(searchQuery, () => {
  clearTimeout(searchDebounce)
  searchDebounce = setTimeout(() => void load(1), 350)
})
watch(sortOrder, () => void load(1))

onMounted(() => {
  isNarrow.value = narrowMq.matches
  narrowMq.addEventListener('change', onNarrowChange)
})
onBeforeUnmount(() => narrowMq.removeEventListener('change', onNarrowChange))
</script>

<template>
  <div class="shorts-page">
    <SaHeader />
    <div class="shorts-body">
      <div class="shorts-head">
        <h1 class="shorts-title">短视频</h1>
        <div class="head-tools">
          <!-- display: contents：桌面端不参与布局，移动端整体隐藏 -->
          <span class="head-extra">
            <select v-model="sortOrder" class="sort-select" aria-label="排序">
              <option v-for="o in SORT_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
            <SaDensitySwitch />
          </span>
          <div class="search-box">
            <SearchOutlined :size="15" />
            <input
              v-model="searchQuery"
              class="search-input"
              type="text"
              :placeholder="isNarrow ? '搜索…' : '搜索短视频'"
            />
            <button
              v-if="searchQuery"
              class="search-clear"
              type="button"
              aria-label="清空搜索"
              @click="searchQuery = ''"
            >
              <CloseOutlined :size="12" />
            </button>
          </div>
          <!-- 移动端紧凑工具栏：与搜索同一行，全部图标化（无文字），排序走下拉 -->
          <div class="head-icons">
            <n-dropdown
              :options="sortDropdownOptions"
              trigger="click"
              placement="bottom-end"
              @select="onSortSelect"
            >
              <button
                class="head-icon"
                :class="{ 'head-icon--on': sortOrder !== 'created_desc' }"
                type="button"
                :title="sortIconLabel"
                :aria-label="sortIconLabel"
              >
                <SortOutlined :size="16" />
              </button>
            </n-dropdown>
            <SaDensitySwitch icon-only />
          </div>
        </div>
      </div>

      <div v-if="loading && !items.length" class="empty">
        加载中…
        <!-- 空网格的轨道数与有数据时一致：加载态也渲染网格供列数测量，避免首屏死锁 -->
        <div :ref="bindGrid" class="shorts-grid" :class="`density-${density}`" aria-hidden="true"></div>
      </div>
      <template v-else>
        <!-- 网格无条件渲染：首载前也保持测量目标在 DOM 里，否则列数测不到、page_size 恒为 0，首载永不触发 -->
        <div :ref="bindGrid" class="shorts-grid" :class="`density-${density}`">
          <VideoCard v-for="(mv, i) in items" :key="mv.id" :video="mv" :index="i" portrait hideType />
        </div>
        <SaPagination
          v-if="items.length"
          :total="total"
          :page="pageNum"
          :page-size="pageSize"
          :disabled="loading"
          @update:page="onPageChange"
        />
        <div v-if="!items.length" class="empty">
          <MovieOutlined :size="40" />
          <p>{{ loadError || '暂无短视频' }}</p>
          <button v-if="loadError" class="empty-retry" @click="load(1)">重试</button>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.shorts-page {
  min-height: 100vh;
  background: var(--sa-bg);
  color: var(--sa-text-primary);
}
.shorts-body {
  max-width: 1280px;
  margin: 0 auto;
  padding: 24px 32px 64px;
}
.shorts-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
}
.shorts-title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
}
/* 搜索框与浏览页同一套：药丸形，图标与清除按钮绝对定位 */
.search-box {
  position: relative;
  display: flex;
  align-items: center;
}
.search-box svg {
  position: absolute;
  left: 14px;
  color: var(--sa-text-tertiary);
  pointer-events: none;
}
.search-input {
  width: 220px;
  padding: 9px 34px 9px 38px;
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
.search-clear {
  position: absolute;
  right: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  border: none;
  border-radius: 9999px;
  background: var(--sa-subtle);
  color: var(--sa-text-tertiary);
  cursor: pointer;
  transition: color 0.15s, background 0.15s;
}
.search-clear:hover {
  color: var(--sa-text-primary);
  background: var(--sa-hover);
}
.sort-select {
  height: 36px;
  padding: 0 10px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 8px;
  background: var(--sa-subtle);
  color: var(--sa-text-secondary);
  font-size: 13px;
  outline: none;
  cursor: pointer;
  transition: border-color 0.2s;
}
.sort-select:focus {
  border-color: var(--sa-accent);
}
.head-tools {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
/* 桌面端：head-extra 以 contents 透传给 head-tools 的 flex 布局 */
.head-extra {
  display: contents;
}
/* 移动端图标筛选栏：桌面端整体不渲染，≤640px 与搜索框同一行展示 */
.head-icons {
  display: none;
}
.head-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  padding: 0;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 9999px;
  background: var(--sa-elevated);
  color: var(--sa-text-secondary);
  cursor: pointer;
  transition: color 0.2s, border-color 0.2s, background 0.2s;
}
.head-icon:hover {
  color: var(--sa-text-primary);
  border-color: var(--sa-accent);
}
/* 已生效的排序：图标点亮 */
.head-icon--on {
  color: var(--sa-accent);
  border-color: var(--sa-accent);
  background: color-mix(in srgb, var(--sa-accent) 12%, transparent);
}
.shorts-grid {
  display: grid;
  grid-template-columns: repeat(var(--cols, 3), minmax(0, 1fr));
  gap: 16px;
}
.shorts-grid.density-compact {
  --cols: 4;
}
.shorts-grid.density-standard {
  --cols: 3;
}
.shorts-grid.density-loose {
  --cols: 2;
}
/* 手机端列数：紧凑 3 / 标准 2 / 宽松 2（竖屏卡片窄，一行两张阅读体验最好） */
@media (max-width: 640px) {
  .shorts-grid.density-compact {
    --cols: 3;
  }
  .shorts-grid.density-standard {
    --cols: 2;
  }
  .shorts-grid.density-loose {
    --cols: 2;
  }
}
@media (min-width: 768px) {
  .shorts-grid {
    grid-template-columns: repeat(var(--cols, 6), minmax(0, 1fr));
    gap: 20px;
  }
  .shorts-grid.density-compact {
    --cols: 8;
  }
  .shorts-grid.density-standard {
    --cols: 6;
  }
  .shorts-grid.density-loose {
    --cols: 5;
  }
}
.empty {
  color: var(--sa-text-secondary);
  font-size: 13px;
}
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 64px 0;
}
.empty-retry {
  padding: 4px 16px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 999px;
  background: transparent;
  color: var(--sa-accent);
  font-size: 12px;
  cursor: pointer;
}
.empty-retry:hover {
  filter: brightness(1.2);
}
/* 移动端：标题独占一行，工具栏 = 搜索框（左，占满余量）+ 排序/宽松度图标（右），与浏览页一致 */
@media (max-width: 640px) {
  .shorts-body {
    padding: 20px 16px 48px;
  }
  .shorts-head {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
    margin-bottom: 16px;
  }
  .head-tools {
    flex-wrap: nowrap;
    gap: 8px;
  }
  .head-extra {
    display: none;
  }
  .head-icons {
    display: flex;
    align-items: center;
    gap: 6px;
    flex: none;
  }
  .search-box {
    flex: 1 1 auto;
    min-width: 0;
  }
  .search-box svg {
    left: 11px;
  }
  .search-input {
    width: 100%;
    padding: 8px 28px 8px 30px;
    font-size: 13px;
  }
  .search-clear {
    right: 7px;
  }
}
</style>
