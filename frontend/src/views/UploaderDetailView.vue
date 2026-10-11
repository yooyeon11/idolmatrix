<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import SaHeader from '@/components/SaHeader.vue'
import SaPagination from '@/components/SaPagination.vue'
import SaDensitySwitch from '@/components/SaDensitySwitch.vue'
import SaMobileSettings from '@/components/SaMobileSettings.vue'
import { musicVideosApi } from '@/api/musicVideos'
import { uploadersApi, type MissingReport, type MissingVideoItem } from '@/api/uploaders'
import { useSettingsStore } from '@/stores/settings'
import type { MusicVideo } from '@/types/models'
import { VIDEO_TYPE_LABEL } from '@/types/models'
import { DownloadOutlined, LinkOutlined, MovieOutlined, RefreshOutlined, SearchOutlined } from '@/components/icons'
import { videoPath } from '@/utils/routes'
import { formatDate, formatDuration, formatVideoSongs } from '@/utils/format'
import { IMG_W_CARD } from '@/utils/imageSizes'
import { useGridDensity, useGridPageSize } from '@/composables/useGridDensity'

const route = useRoute()
const router = useRouter()
const settings = useSettingsStore()
const message = useMessage()

const uploaderName = computed(() =>
  decodeURIComponent((route.params.name as string) || '')
)
const loading = ref(false)
const videos = ref<MusicVideo[]>([])
const total = ref(0)
const pageNum = ref(1)
const searchQuery = ref('')
const brokenThumbs = ref<Set<number>>(new Set())

const { density } = useGridDensity()
// 网格列数实测 → page_size = 列数 × 行数：整页恰好铺满网格，最后一行不再缺位
const gridEl = ref<HTMLElement | null>(null)
function bindGrid(el: unknown) {
  gridEl.value = (el as HTMLElement | null) ?? null
}
const { pageSize } = useGridPageSize(gridEl)

// 加载序列号：丢弃过期的异步回调（快速切换路由时旧请求不应覆盖新数据）
let loadSeq = 0

async function load(targetPage: number) {
  const seq = ++loadSeq
  loading.value = true
  try {
    const res = await musicVideosApi.list({
      ingestion_status: 'library',
      uploader: uploaderName.value,
      page: targetPage,
      page_size: pageSize.value,
      q: searchQuery.value.trim() || undefined,
    })
    if (seq !== loadSeq) return
    videos.value = res.items
    total.value = res.total
    pageNum.value = targetPage
  } catch (e) {
    if (seq !== loadSeq) return
    message.error((e as Error).message)
    if (targetPage === 1) {
      videos.value = []
      total.value = 0
    }
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function onPageChange(p: number) {
  void load(p).then(() => window.scrollTo({ top: 0 }))
}

// 搜索：防抖 350ms，改为服务端过滤（与其他列表页一致）
let searchDebounce: ReturnType<typeof setTimeout> | undefined
watch(searchQuery, () => {
  clearTimeout(searchDebounce)
  searchDebounce = setTimeout(() => load(1), 350)
})

function videoTypeName(t: string) {
  return VIDEO_TYPE_LABEL[t] || t
}

function thumbUrl(mv: MusicVideo) {
  if (!settings.showCovers || brokenThumbs.value.has(mv.id)) return undefined
  return musicVideosApi.thumbnailUrl(mv.id, mv.file_hash || mv.file_size, IMG_W_CARD)
}
function onThumbError(mv: MusicVideo) {
  brokenThumbs.value = new Set(brokenThumbs.value).add(mv.id)
}

function goVideo(mv: { uid: string }) {
  router.push(videoPath(mv.uid))
}

// ===== 缺失视频检测（YouTube 频道完整投稿 − 库内视频）=====
const missingReport = ref<MissingReport | null>(null)
const missingChecking = ref(false)
const missingError = ref('')
const brokenMissingThumbs = ref<Set<string>>(new Set())

/**
 * 缺失区块状态（决定渲染什么）：
 * - 'none'      库内该博主没有 YouTube 视频（B 站博主等）→ 整块不渲染
 * - 'error'     报告读取失败 → 显示原因，而不是静默消失
 * - 'nourl'     未配置 YouTube 频道地址 → 一行提示 + 去配置入口
 * - 'disabled'  配了频道但未勾选「缺失检测」→ 一行提示 + 去配置入口
 * - 'active'    已配置且已启用 → 完整区块（检测按钮 + 缺失清单）
 */
const missingState = computed<'none' | 'error' | 'nourl' | 'disabled' | 'active'>(() => {
  if (missingError.value) return 'error'
  if (missingReport.value?.has_youtube_videos === false) return 'none'
  if (!missingReport.value?.channel_configured) return 'nourl'
  if (!missingReport.value?.missing_check_enabled) return 'disabled'
  return 'active'
})
/** 完整区块（含检测与清单）仅在已启用时展示 */
const missingEnabled = computed(() => missingState.value === 'active')
const missingVideos = computed(() =>
  (missingReport.value?.items || []).filter((i) => !i.is_short),
)
const missingShorts = computed(() =>
  (missingReport.value?.items || []).filter((i) => i.is_short),
)
/** 从未检测过：快照为空 */
const neverChecked = computed(() => !missingReport.value?.last_checked_at)

/**
 * 视图切换：全部 / 仅库内 / 仅缺失。
 *
 * 页面结构是「库内视频在上、缺失视频在下」，切换只是隐藏其中一块；缺失检测未启用时
 * 该切换不出现，此时两个开关的兜底条件（!missingEnabled）都成立，两块照常显示。
 */
const VIEW_FILTERS: { value: 'all' | 'library' | 'missing'; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'library', label: '仅库内' },
  { value: 'missing', label: '仅缺失' },
]
const viewFilter = ref<'all' | 'library' | 'missing'>('all')
const showLibrary = computed(() => viewFilter.value !== 'missing' || !missingEnabled.value)
const showMissing = computed(() => viewFilter.value !== 'library' || !missingEnabled.value)

async function loadMissing() {
  try {
    missingError.value = ''
    missingReport.value = await uploadersApi.missingVideos(uploaderName.value)
  } catch (e) {
    // 不再静默：区块会保留并把失败原因显示出来
    missingReport.value = null
    missingError.value = (e as Error).message || '加载失败'
  }
}

// ⚠ 首次加载与路由切换必须放在 missingReport / missingError 等 const 声明之后：
// 函数声明会提升，但 const 处于 TDZ —— 提前调用 loadMissing() 会在赋值前访问
// missingError.value 抛 ReferenceError，且该错误发生在 async 函数体内被静默吞掉
// （void 丢弃了 rejected promise），页面就永远停在「该博主未配置 YouTube 频道地址」。
watch(uploaderName, () => {
  load(1)
  void loadMissing()
})
void loadMissing()

// page_size 变化（首次测量完成 / 切换密度档位 / 视口跨列数边界）：回到第 1 页重新加载
watch(pageSize, (v) => {
  if (v > 0) void load(1)
})

/** 跳到 设置 · 资料库 · 博主管理（深链，见 SettingsView 的 query 解析） */
function gotoUploaderSettings() {
  router.push({ path: '/settings', query: { section: 'db', db: 'uploaders' } })
}

async function runMissingCheck() {
  if (missingChecking.value) return
  missingChecking.value = true
  try {
    missingReport.value = await uploadersApi.missingCheck(uploaderName.value)
    const r = missingReport.value
    message.success(`检测完成：频道共 ${r.channel_video_total} 个，库内 ${r.library_count} 个，缺失 ${r.missing_count} 个`)
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    missingChecking.value = false
  }
}

function missingCoverUrl(item: MissingVideoItem) {
  if (brokenMissingThumbs.value.has(item.video_id) || !item.thumbnail_path) return undefined
  return uploadersApi.missingCoverUrl(item.video_id)
}
function onMissingThumbError(item: MissingVideoItem) {
  brokenMissingThumbs.value = new Set(brokenMissingThumbs.value).add(item.video_id)
}

async function copyText(text: string, tip: string) {
  try {
    await navigator.clipboard.writeText(text)
    message.success(tip)
  } catch {
    message.error('复制失败，请手动复制')
  }
}

function copyMissingLink(item: MissingVideoItem) {
  void copyText(item.url, '已复制视频链接')
}

function copyAllMissingLinks() {
  const links = (missingReport.value?.items || []).map((i) => i.url)
  if (!links.length) return
  void copyText(links.join('\n'), `已复制 ${links.length} 条缺失视频链接`)
}

function exportMissingList() {
  const items = missingReport.value?.items || []
  if (!items.length) return
  const lines = [
    `# ${uploaderName.value} 缺失视频清单（${items.length} 条）`,
    `# 导出时间：${new Date().toLocaleString()}`,
    ...items.map((i) => {
      const date = i.published_at ? i.published_at.slice(0, 10) : '未知日期'
      const dur = i.duration != null ? formatDuration(i.duration) : ''
      return `${date}\t${dur}\t${i.title}\t${i.url}`
    }),
  ]
  const blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `缺失视频-${uploaderName.value}.txt`
  a.click()
  URL.revokeObjectURL(a.href)
}
</script>

<template>
  <div class="mv-page">
    <SaHeader />
    <div class="mv-container">
      <div class="detail-head">
        <div class="detail-title-wrap">
          <h1 class="detail-title">{{ uploaderName }}</h1>
          <span class="detail-count">{{ total }} 个视频</span>
        </div>
        <div class="head-tools">
          <!-- 移动端收纳入口：密度档位进底部抽屉，桌面端按断点自动隐藏 -->
          <SaMobileSettings :breakpoint="640" title="显示设置">
            <div class="sms-group">
              <div class="sms-label">宽松度</div>
              <SaDensitySwitch />
            </div>
          </SaMobileSettings>
          <!-- display: contents：桌面端不参与布局，移动端整体隐藏 -->
          <span class="head-extra">
            <SaDensitySwitch />
          </span>
          <div class="search-box">
            <SearchOutlined :size="15" />
            <input
              v-model="searchQuery"
              class="search-input"
              type="text"
              placeholder="搜索该频道视频…"
            />
          </div>
        </div>
      </div>

      <!-- 视图切换：全部 / 仅库内 / 仅缺失。缺失检测未启用时不显示，此时只有库内视频 -->
      <div v-if="missingEnabled" class="view-switch">
        <button
          v-for="opt in VIEW_FILTERS"
          :key="opt.value"
          class="view-switch-btn"
          :class="{ 'is-active': viewFilter === opt.value }"
          type="button"
          @click="viewFilter = opt.value"
        >
          {{ opt.label }}
        </button>
      </div>

      <!-- ===== 库内视频（上）===== -->
      <template v-if="showLibrary">
        <div v-if="loading && !videos.length" class="list-loading">
          加载中…
          <!-- 空网格的 auto-fill 轨道数与有数据时一致：加载态也渲染网格供列数测量，避免首屏死锁 -->
          <div :ref="bindGrid" class="card-grid" :class="`density-${density}`" aria-hidden="true"></div>
        </div>

        <template v-else>
          <div :ref="bindGrid" class="card-grid" :class="`density-${density}`">
            <a
              v-for="mv in videos"
              :key="mv.id"
              class="poster-card"
              :href="videoPath(mv.uid)"
              @click.prevent="goVideo(mv)"
            >
              <div class="poster-cover">
                <img
                  v-if="thumbUrl(mv)"
                  :src="thumbUrl(mv)!"
                  :alt="mv.name"
                  class="poster-img"
                  loading="lazy"
                  @error="onThumbError(mv)"
                />
                <div v-else class="poster-img poster-placeholder">
                  <MovieOutlined :size="26" />
                </div>
                <span v-if="mv.duration" class="card-duration">
                  {{ Math.floor(mv.duration / 60) }}:{{ String(mv.duration % 60).padStart(2, '0') }}
                </span>
              </div>
              <div class="poster-title" :title="mv.name">{{ mv.name }}</div>
              <div class="poster-meta">
                <span>{{ videoTypeName(mv.video_type) }}</span>
                <span v-if="formatVideoSongs(mv.songs, mv.song_name, mv.song_chinese_name)">
                  {{ formatVideoSongs(mv.songs, mv.song_name, mv.song_chinese_name) }}
                </span>
              </div>
            </a>
          </div>
          <SaPagination
            v-if="videos.length"
            :total="total"
            :page="pageNum"
            :page-size="pageSize"
            :disabled="loading"
            :show-total="false"
            @update:page="onPageChange"
          />
          <div v-if="!videos.length" class="empty-state">
            <MovieOutlined :size="40" />
            <p>该博主暂无视频</p>
          </div>
        </template>
      </template>

      <!-- ===== 缺失视频检测（下）===== -->
      <template v-if="showMissing">
        <!-- 未启用检测：一行可发现提示（不再整块消失导致「找不到按钮」） -->
        <div v-if="missingState === 'nourl' || missingState === 'disabled'" class="missing-hint">
          <span class="missing-hint-text">
            <template v-if="missingState === 'nourl'">
              缺失视频检测：该博主未配置 YouTube 频道地址
            </template>
            <template v-else>
              缺失视频检测：频道已配置，但未勾选「缺失检测」
            </template>
          </span>
          <button class="missing-inline-btn" type="button" @click="gotoUploaderSettings">
            去「资料库 · 博主管理」配置
          </button>
        </div>

        <!-- 读取失败：把原因显示出来，方便定位问题 -->
        <div v-else-if="missingState === 'error'" class="missing-hint missing-hint--error">
          <span class="missing-hint-text">缺失视频报告读取失败：{{ missingError }}</span>
          <button class="missing-inline-btn" type="button" @click="loadMissing">重试</button>
        </div>

        <!-- 已启用：完整区块（不出网读快照，检测按钮才拉取） -->
        <section v-else-if="missingEnabled" class="missing-block">
          <div class="missing-head">
            <div class="missing-title-wrap">
              <h2 class="missing-title">缺失视频</h2>
              <span class="missing-summary">
                <template v-if="neverChecked">尚未检测</template>
                <template v-else>
                  库内 {{ missingReport!.library_count }} / 频道 {{ missingReport!.channel_video_total }}
                  <template v-if="missingReport!.missing_count">
                    · 缺 <strong>{{ missingReport!.missing_count }}</strong>
                  </template>
                  <template v-else>· 已全部收齐</template>
                  <template v-if="missingReport!.last_checked_at">
                    · 检测于 {{ formatDate(missingReport!.last_checked_at.slice(0, 10)) }}
                  </template>
                </template>
              </span>
            </div>

            <!-- 相似博主匹配：博主名与规则名肉眼同名但写法不同（空格 / 全角 / 零宽字符）时
                 标注命中来源，避免用户以为配置没生效 -->
            <p v-if="missingReport!.matched_rule_name" class="missing-match-note">
              已按相似博主「{{ missingReport!.matched_rule_name }}」的频道配置匹配（名字写法略有差异，按同一博主处理）
            </p>
            <div class="missing-actions">
              <button
                class="sa-btn-ghost missing-check-btn"
                type="button"
                :disabled="missingChecking"
                @click="runMissingCheck"
              >
                <RefreshOutlined :size="13" :class="{ 'is-spin': missingChecking }" />
                {{ missingChecking ? '检测中…' : '检测缺失视频' }}
              </button>
              <button
                v-if="missingReport!.missing_count"
                class="sa-btn-ghost"
                type="button"
                @click="copyAllMissingLinks"
              >
                <LinkOutlined :size="13" />
                复制全部链接
              </button>
              <button
                v-if="missingReport!.missing_count"
                class="sa-btn-ghost"
                type="button"
                @click="exportMissingList"
              >
                <DownloadOutlined :size="13" />
                导出清单
              </button>
            </div>
          </div>

          <div v-if="neverChecked" class="missing-empty">
            点击「检测缺失视频」拉取频道完整投稿并与库内比对。
          </div>
          <div v-else-if="!missingReport!.missing_count" class="missing-empty">
            频道视频已全部入库，没有缺失。
          </div>
          <template v-else>
            <div v-if="missingVideos.length" class="missing-group">
              <div class="missing-group-title">视频（{{ missingVideos.length }}）</div>
              <div class="missing-grid">
                <div v-for="item in missingVideos" :key="item.video_id" class="missing-card">
                  <div class="missing-cover">
                    <img
                      v-if="missingCoverUrl(item)"
                      :src="missingCoverUrl(item)!"
                      :alt="item.title"
                      loading="lazy"
                      @error="onMissingThumbError(item)"
                    />
                    <div v-else class="missing-cover missing-cover--placeholder">
                      <MovieOutlined :size="24" />
                    </div>
                    <span v-if="item.duration != null" class="missing-duration">
                      {{ formatDuration(item.duration) }}
                    </span>
                  </div>
                  <div class="missing-info">
                    <a
                      class="missing-name"
                      :href="item.url"
                      target="_blank"
                      rel="noopener noreferrer"
                      :title="item.title"
                    >{{ item.title }}</a>
                    <div class="missing-meta">
                      <span>{{ formatDate(item.published_at) }}</span>
                      <button
                        class="missing-copy"
                        type="button"
                        title="复制链接"
                        @click="copyMissingLink(item)"
                      >
                        <LinkOutlined :size="12" />
                        复制
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <div v-if="missingShorts.length" class="missing-group">
              <div class="missing-group-title">Shorts（{{ missingShorts.length }}）</div>
              <div class="missing-grid missing-grid--shorts">
                <div v-for="item in missingShorts" :key="item.video_id" class="missing-card missing-card--short">
                  <div class="missing-cover missing-cover--short">
                    <img
                      v-if="missingCoverUrl(item)"
                      :src="missingCoverUrl(item)!"
                      :alt="item.title"
                      loading="lazy"
                      @error="onMissingThumbError(item)"
                    />
                    <div v-else class="missing-cover missing-cover--short missing-cover--placeholder">
                      <MovieOutlined :size="24" />
                    </div>
                  </div>
                  <div class="missing-info">
                    <a
                      class="missing-name"
                      :href="item.url"
                      target="_blank"
                      rel="noopener noreferrer"
                      :title="item.title"
                    >{{ item.title }}</a>
                    <div class="missing-meta">
                      <span>{{ formatDate(item.published_at) }}</span>
                      <button
                        class="missing-copy"
                        type="button"
                        title="复制链接"
                        @click="copyMissingLink(item)"
                      >
                        <LinkOutlined :size="12" />
                        复制
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </template>
        </section>
      </template>
    </div>
  </div>
</template>

<style scoped>
.mv-page {
  min-height: 100vh;
}

/* ===== 视图切换（全部 / 仅库内 / 仅缺失）===== */
.view-switch {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-bottom: 20px;
  padding: 2px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 999px;
  background: var(--sa-subtle);
}
.view-switch-btn {
  height: 26px;
  padding: 0 14px;
  border: none;
  border-radius: 999px;
  background: transparent;
  font-size: 12.5px;
  font-family: inherit;
  color: var(--sa-text-tertiary);
  cursor: pointer;
  transition: background 0.2s, color 0.2s;
}
.view-switch-btn:hover {
  color: var(--sa-text-secondary);
}
.view-switch-btn.is-active {
  background: var(--sa-elevated);
  color: var(--sa-text-primary);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
}

/* ===== 缺失视频区块 ===== */
/* 未启用检测/读取失败时的一行提示：保持可见但不抢视线 */
.missing-hint {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 24px;
  padding: 10px 16px;
  border: 1px dashed var(--sa-border);
  border-radius: 12px;
  background: var(--sa-subtle);
  font-size: 12.5px;
  color: var(--sa-text-tertiary);
}
.missing-hint--error {
  border-style: solid;
  border-color: var(--sa-danger, #e5484d);
  color: var(--sa-text-secondary);
}
.missing-hint-text {
  min-width: 0;
  overflow-wrap: anywhere;
}
.missing-inline-btn {
  flex-shrink: 0;
  height: 28px;
  padding: 0 12px;
  border: 1px solid var(--sa-border);
  border-radius: 999px;
  background: var(--sa-elevated);
  font-size: 12px;
  font-family: inherit;
  color: var(--sa-text-secondary);
  cursor: pointer;
  transition: border-color 0.2s, color 0.2s;
}
.missing-inline-btn:hover {
  border-color: var(--sa-accent);
  color: var(--sa-accent);
}
.missing-block {
  margin-bottom: 40px;
  padding: 20px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 16px;
  background: var(--sa-elevated);
}
.missing-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.missing-title-wrap {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  min-width: 0;
}
.missing-title {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
  color: var(--sa-text-primary);
  letter-spacing: -0.01em;
}
.missing-summary {
  font-size: 12.5px;
  color: var(--sa-text-tertiary);
}
.missing-match-note {
  margin: 10px 0 0;
  font-size: 12.5px;
  color: var(--sa-text-tertiary);
}
.missing-summary strong {
  color: var(--sa-accent);
  font-weight: 600;
}
.missing-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.is-spin {
  animation: missing-spin 1s linear infinite;
}
@keyframes missing-spin {
  to {
    transform: rotate(360deg);
  }
}
.missing-empty {
  padding: 28px 0 8px;
  text-align: center;
  font-size: 13px;
  color: var(--sa-text-tertiary);
}
.missing-group {
  margin-top: 18px;
}
.missing-group-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--sa-text-secondary);
  margin-bottom: 10px;
}
.missing-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 16px;
}
.missing-grid--shorts {
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
}
.missing-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.missing-cover {
  position: relative;
  aspect-ratio: 16 / 9;
  border-radius: 10px;
  overflow: hidden;
  background: var(--sa-subtle);
}
.missing-cover--short {
  aspect-ratio: 9 / 16;
}
.missing-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.missing-cover--placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--sa-text-tertiary);
}
.missing-duration {
  position: absolute;
  right: 6px;
  bottom: 6px;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.7);
  color: #fff;
  font-size: 11px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  line-height: 1.6;
}
.missing-info {
  min-width: 0;
}
.missing-name {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  font-size: 13px;
  font-weight: 500;
  color: var(--sa-text-primary);
  text-decoration: none;
  line-height: 1.45;
  word-break: break-word;
}
.missing-name:hover {
  color: var(--sa-accent);
}
.missing-meta {
  margin-top: 5px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.missing-copy {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 999px;
  background: transparent;
  color: var(--sa-text-tertiary);
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
  flex-shrink: 0;
  transition: color 0.2s, border-color 0.2s;
}
.missing-copy:hover {
  color: var(--sa-accent);
  border-color: var(--sa-accent);
}
@media (max-width: 640px) {
  .missing-block {
    padding: 14px;
  }
  .missing-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .missing-grid--shorts {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

.mv-container {
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 32px 64px;
}
.detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 36px 0 28px;
  flex-wrap: wrap;
}
.detail-title-wrap {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}
.detail-title {
  font-size: 28px;
  font-weight: 700;
  color: var(--sa-text-primary);
  margin: 0;
  letter-spacing: -0.02em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-count {
  font-size: 14px;
  color: var(--sa-text-tertiary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  flex-shrink: 0;
}
.head-tools {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
/* 桌面端：head-extra 以 contents 透传给 head-tools 的 flex 布局 */
.head-extra {
  display: contents;
}
.search-box {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 16px;
  border-radius: 9999px;
  border: 1px solid var(--sa-border-subtle);
  background: var(--sa-elevated);
  color: var(--sa-text-tertiary);
}
.search-box:focus-within {
  border-color: var(--sa-accent);
}
.search-input {
  border: none;
  background: transparent;
  color: var(--sa-text-primary);
  font-size: 14px;
  outline: none;
  width: 200px;
}
.search-input::placeholder {
  color: var(--sa-text-tertiary);
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(var(--grid-min, 220px), 1fr));
  gap: 24px;
}
.density-compact { --grid-min: 180px; }
.density-standard { --grid-min: 220px; }
.density-loose { --grid-min: 280px; }
.poster-card {
  cursor: pointer;
  text-decoration: none;
  display: block;
}
.poster-cover {
  position: relative;
  aspect-ratio: 16 / 9;
  border-radius: 12px;
  overflow: hidden;
  background: var(--sa-subtle);
}
.poster-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.3s;
}
.poster-card:hover .poster-cover img {
  transform: scale(1.05);
}
.poster-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.75);
}
.card-duration {
  position: absolute;
  right: 8px;
  bottom: 8px;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.7);
  color: #fff;
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  line-height: 1.6;
}
.poster-title {
  margin-top: 10px;
  font-size: 14px;
  font-weight: 500;
  color: var(--sa-text-primary);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.poster-meta {
  margin-top: 6px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  color: var(--sa-text-tertiary);
  white-space: nowrap;
  overflow: hidden;
}
.poster-meta span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.list-loading {
  padding: 60px 0;
  text-align: center;
  color: var(--sa-text-tertiary);
  font-size: 14px;
}
.empty-state {
  padding: 80px 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  color: var(--sa-text-tertiary);
}
.empty-state p {
  margin: 0;
  font-size: 14px;
}

@media (max-width: 640px) {
  .detail-head {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }
  /* 移动端工具栏只留：搜索框 + ⚙ 收纳入口 */
  .head-tools {
    width: 100%;
    flex-wrap: nowrap;
  }
  .head-extra {
    display: none;
  }
  .search-box {
    flex: 1;
    width: auto;
  }
  .card-grid.density-compact {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .search-input {
    flex: 1;
    width: auto;
  }
}
</style>
