<script setup lang="ts">
// 设置页 · 资料库 · 艺人展示：首页主舞台轮动池的人工勾选清单。
// 每行 = 一个组合或一个艺人；展示是否有竖版海报 / 横幅海报，
// 勾选后才加入首页轮动（写入复用 PATCH /artists/{id}、/groups/{id}）。
import { computed, onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { dbViewsApi, type ShowcaseRow } from '@/api/dbViews'
import { artistsApi } from '@/api/artists'
import { groupsApi } from '@/api/groups'

const message = useMessage()

const rows = ref<ShowcaseRow[]>([])
const loading = ref(false)
const loadError = ref('')
const keyword = ref('')
const savingKeys = ref(new Set<string>())

const featuredCount = computed(() => rows.value.filter((r) => r.featured_in_home).length)

const filtered = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  if (!q) return rows.value
  return rows.value.filter((r) =>
    [r.name, r.chinese_name].some((v) => (v || '').toLowerCase().includes(q)),
  )
})

function keyOf(r: ShowcaseRow) {
  return `${r.type}-${r.id}`
}

/** 缩略图优先竖版海报，其次横幅；两者都无时用首字占位 */
function thumbUrl(r: ShowcaseRow) {
  if (r.has_poster) {
    return r.type === 'artist' ? artistsApi.avatarUrl(r.id) : groupsApi.avatarUrl(r.id)
  }
  if (r.has_banner) {
    return r.type === 'artist' ? artistsApi.bannerUrl(r.id) : groupsApi.bannerUrl(r.id)
  }
  return ''
}

/** 无主视觉的实体不能进轮动池（否则首页轮播帧是空的） */
function canFeature(r: ShowcaseRow) {
  return r.has_poster || r.has_banner
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const res = await dbViewsApi.getShowcaseRows()
    rows.value = res.items
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function toggle(r: ShowcaseRow, ev: Event) {
  const input = ev.target as HTMLInputElement
  const checked = input.checked
  const prev = r.featured_in_home
  const key = keyOf(r)
  savingKeys.value.add(key)
  r.featured_in_home = checked
  try {
    if (r.type === 'artist') {
      await artistsApi.update(r.id, { featured_in_home: checked })
    } else {
      await groupsApi.update(r.id, { featured_in_home: checked })
    }
  } catch (e) {
    r.featured_in_home = prev
    input.checked = prev
    message.error(e instanceof Error ? e.message : '保存失败')
  } finally {
    savingKeys.value.delete(key)
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="showcase">
    <div class="showcase-head">
      <div class="showcase-summary">
        共 <b>{{ rows.length }}</b> 个 · 已加入轮动 <b>{{ featuredCount }}</b> 个
      </div>
      <input
        v-model="keyword"
        class="showcase-search"
        type="text"
        placeholder="搜索名称 / 中文名"
      />
    </div>

    <div class="showcase-cols">
      <span>名称</span>
      <span class="col-flag">海报</span>
      <span class="col-flag">横幅海报</span>
      <span class="col-check">加入轮动</span>
    </div>

    <div v-if="loading" class="showcase-tip">加载中…</div>
    <div v-else-if="loadError" class="showcase-tip showcase-tip--err">
      {{ loadError }}
      <button class="showcase-retry" type="button" @click="load">重试</button>
    </div>
    <div v-else-if="!filtered.length" class="showcase-tip">
      {{ rows.length ? '没有匹配的艺人或组合' : '库里还没有艺人或组合' }}
    </div>

    <ul v-else class="showcase-list">
      <li v-for="r in filtered" :key="keyOf(r)" class="showcase-row">
        <span class="showcase-name">
          <span class="showcase-thumb">
            <img v-if="thumbUrl(r)" :src="thumbUrl(r)" :alt="r.name" loading="lazy" />
            <span v-else class="showcase-thumb-empty">{{ (r.name || '?').slice(0, 1) }}</span>
          </span>
          <span class="showcase-text">
            <span class="showcase-text-main">{{ r.name }}</span>
            <span class="showcase-text-sub">
              {{ r.type === 'group' ? '组合' : '艺人' }}{{ r.chinese_name ? ` · ${r.chinese_name}` : '' }}
            </span>
          </span>
        </span>
        <span class="showcase-flag col-flag" :class="{ on: r.has_poster }">
          {{ r.has_poster ? '有' : '无' }}
        </span>
        <span class="showcase-flag col-flag" :class="{ on: r.has_banner }">
          {{ r.has_banner ? '有' : '无' }}
        </span>
        <span class="col-check">
          <label
            class="showcase-check"
            :title="canFeature(r) ? '勾选后加入首页轮动' : '需先设置海报或横幅海报'"
          >
            <input
              type="checkbox"
              :checked="r.featured_in_home"
              :disabled="!canFeature(r) || savingKeys.has(keyOf(r))"
              @change="toggle(r, $event)"
            />
          </label>
        </span>
      </li>
    </ul>

    <p class="showcase-foot">
      轮动池为「勾选制」：未勾选的不会出现在首页焦点海报位；
      一个都没勾选时首页自动回退到「有海报 + 有关联视频」的随机展示。
    </p>
  </div>
</template>

<style scoped>
.showcase {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.showcase-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.showcase-summary {
  font-size: 13px;
  color: var(--sa-text-secondary);
}
.showcase-summary b {
  color: var(--sa-text-primary);
  font-weight: 600;
}
.showcase-search {
  width: 220px;
  max-width: 100%;
  padding: 7px 10px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 8px;
  background: var(--sa-bg);
  color: var(--sa-text-primary);
  font-size: 13px;
  outline: none;
}
.showcase-search:focus {
  border-color: var(--sa-accent);
}

/* 表头与行共用同一套列宽：名称自适应 + 两个指示列 + 勾选列 */
.showcase-cols,
.showcase-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 72px 92px 96px;
  align-items: center;
  gap: 8px;
}
.showcase-cols {
  padding: 0 12px 6px;
  font-size: 11px;
  color: var(--sa-text-secondary);
  letter-spacing: 0.04em;
}
.col-flag,
.col-check {
  text-align: center;
}
.showcase-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 12px;
  overflow: hidden;
}
.showcase-row {
  padding: 8px 12px;
  border-bottom: 1px solid var(--sa-border-subtle);
}
.showcase-row:last-child {
  border-bottom: none;
}
.showcase-name {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.showcase-thumb {
  flex: none;
  width: 34px;
  height: 44px;
  border-radius: 6px;
  overflow: hidden;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  display: flex;
  align-items: center;
  justify-content: center;
}
.showcase-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.showcase-thumb-empty {
  font-size: 13px;
  color: var(--sa-text-secondary);
}
.showcase-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.showcase-text-main {
  font-size: 13px;
  font-weight: 600;
  color: var(--sa-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.showcase-text-sub {
  font-size: 11px;
  color: var(--sa-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.showcase-flag {
  font-size: 12px;
  color: var(--sa-text-secondary);
}
.showcase-flag.on {
  color: var(--sa-accent);
  font-weight: 600;
}
.showcase-check {
  display: flex;
  align-items: center;
  justify-content: center;
}
.showcase-check input {
  width: 16px;
  height: 16px;
  accent-color: var(--sa-accent);
  cursor: pointer;
}
.showcase-check input:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}
.showcase-tip {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px;
  font-size: 13px;
  color: var(--sa-text-secondary);
}
.showcase-tip--err {
  color: var(--sa-text-primary);
}
.showcase-retry {
  padding: 4px 12px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 7px;
  background: var(--sa-elevated);
  color: var(--sa-text-primary);
  font-size: 12px;
  cursor: pointer;
}
.showcase-foot {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--sa-text-secondary);
}

@media (max-width: 640px) {
  .showcase-cols,
  .showcase-row {
    grid-template-columns: minmax(0, 1fr) 56px 68px 66px;
  }
  .showcase-search {
    width: 100%;
  }
}
</style>
