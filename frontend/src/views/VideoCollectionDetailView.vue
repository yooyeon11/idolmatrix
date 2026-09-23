<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NSpin, useDialog, useMessage } from 'naive-ui'
import SaHeader from '@/components/SaHeader.vue'
import SaPagination from '@/components/SaPagination.vue'
import SaDensitySwitch from '@/components/SaDensitySwitch.vue'
import SaMobileSettings from '@/components/SaMobileSettings.vue'
import VideoCard from '@/components/VideoCard.vue'
import { videoCollectionsApi } from '@/api/videoCollections'
import type { MusicVideo, VideoCollection } from '@/types/models'
import { fetchByRouteParam, videoCollectionPath } from '@/utils/routes'
import { useGridDensity, useGridPageSize } from '@/composables/useGridDensity'
import { BookmarkOutlined } from '@/components/icons'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const dialog = useDialog()

const collection = ref<VideoCollection | null>(null)
const videos = ref<MusicVideo[]>([])
const total = ref(0)
const pageNum = ref(1)
const loading = ref(false)
const loadingVideos = ref(false)

const { density } = useGridDensity()
// 网格列数实测 → page_size = 列数 × 行数：整页恰好铺满网格，最后一行不再缺位。
// 该接口后端限制 page_size ≤ 100
const gridEl = ref<HTMLElement | null>(null)
function bindGrid(el: unknown) {
  gridEl.value = (el as HTMLElement | null) ?? null
}
const { pageSize } = useGridPageSize(gridEl, 100)
const videosLoaded = ref(false)
const notFound = ref(false)
const editing = ref(false)
const nameDraft = ref('')
const saving = ref(false)

const routeKey = computed(() => String(route.params.uid || ''))

async function loadCollection() {
  const param = routeKey.value.trim()
  if (!param) {
    notFound.value = true
    return
  }
  loading.value = true
  notFound.value = false
  try {
    const col = await fetchByRouteParam(
      param,
      videoCollectionsApi.getByUid,
      videoCollectionsApi.get,
    )
    collection.value = col
    nameDraft.value = col.name
    if (col.uid && col.uid !== param) {
      router.replace(videoCollectionPath(col.uid))
    }
    pageNum.value = 1
    videos.value = []
    total.value = 0
    videosLoaded.value = false
    // pageSize 尚未实测完成时跳过，由下方 watch 在测量就绪后触发首次加载
    if (pageSize.value > 0) {
      await loadVideos(1)
    }
  } catch (e) {
    notFound.value = true
    message.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

async function loadVideos(targetPage: number) {
  if (!collection.value || loadingVideos.value) return
  loadingVideos.value = true
  try {
    const r = await videoCollectionsApi.videos(collection.value.id, {
      page: targetPage,
      page_size: pageSize.value,
    })
    videos.value = r.items
    total.value = r.total
    pageNum.value = targetPage
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    loadingVideos.value = false
    videosLoaded.value = true
  }
}

function onPageChange(p: number) {
  void loadVideos(p).then(() => window.scrollTo({ top: 0 }))
}

async function saveName() {
  if (!collection.value) return
  const name = nameDraft.value.trim()
  if (!name) {
    message.warning('名称不能为空')
    return
  }
  saving.value = true
  try {
    collection.value = await videoCollectionsApi.update(collection.value.id, { name })
    editing.value = false
    message.success('已保存')
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    saving.value = false
  }
}

function confirmDelete() {
  if (!collection.value) return
  const col = collection.value
  dialog.warning({
    title: '删除收藏夹',
    content: `删除「${col.name}」不会删除视频文件，只去掉这个夹。确定删除？`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await videoCollectionsApi.remove(col.id)
        message.success('已删除')
        router.push('/collections')
      } catch (e) {
        message.error((e as Error).message)
      }
    },
  })
}

onMounted(loadCollection)
watch(
  () => route.params.uid,
  (next, prev) => {
    if (!next || next === prev) return
    void loadCollection()
  },
)

// page_size 就绪或变化（首次测量完成 / 切换密度档位 / 视口跨列数边界）：回到第 1 页重新加载
watch(pageSize, (v) => {
  if (v > 0 && collection.value) void loadVideos(1)
})
</script>

<template>
  <div class="mv-page">
    <SaHeader />
    <div class="mv-container">
      <n-spin :show="loading">
        <div v-if="notFound" class="empty-state">
          <BookmarkOutlined :size="40" />
          <p>收藏夹不存在或已删除</p>
          <button class="empty-back" type="button" @click="router.push('/collections')">返回收藏</button>
        </div>
        <div v-else-if="collection" class="col-page">
          <div class="col-head">
            <div class="col-title-wrap">
              <input
                v-if="editing"
                v-model="nameDraft"
                class="col-name-input"
                @keydown.enter="saveName"
                @keydown.escape="editing = false"
              />
              <h1 v-else class="col-name">{{ collection.name }}</h1>
              <span class="col-count">{{ collection.video_count }} 个视频</span>
            </div>
            <div class="col-actions">
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
              <button v-if="editing" class="sa-btn" type="button" :disabled="saving" @click="saveName">
                保存
              </button>
              <button v-else class="sa-btn" type="button" @click="editing = true">重命名</button>
              <button class="sa-btn sa-btn--danger" type="button" @click="confirmDelete">删除</button>
            </div>
          </div>
          <div :ref="bindGrid" class="video-grid" :class="`density-${density}`">
            <VideoCard v-for="(v, i) in videos" :key="v.id" :video="v" :index="i" />
          </div>
          <div v-if="!videos.length && videosLoaded" class="empty-state">
            <BookmarkOutlined :size="32" />
            <p>这个收藏夹还没有视频</p>
          </div>
          <SaPagination
            v-if="videos.length"
            :total="total"
            :page="pageNum"
            :page-size="pageSize"
            :disabled="loadingVideos"
            :show-total="false"
            @update:page="onPageChange"
          />
        </div>
      </n-spin>
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
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 80px 0;
  color: var(--sa-text-tertiary);
}
.empty-back {
  padding: 6px 18px;
  border: 1px solid var(--sa-border);
  border-radius: 9999px;
  background: transparent;
  color: var(--sa-text-secondary);
  cursor: pointer;
}
.col-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  padding: 28px 0 18px;
  flex-wrap: wrap;
}
.col-title-wrap {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}
.col-name {
  margin: 0;
  font-size: 28px;
  font-weight: 700;
  color: var(--sa-text-primary);
}
.col-name-input {
  font-size: 24px;
  font-weight: 700;
  padding: 4px 8px;
  border: 1px solid var(--sa-border);
  border-radius: 8px;
  background: var(--sa-elevated);
  color: var(--sa-text-primary);
}
.col-count {
  font-size: 14px;
  color: var(--sa-text-tertiary);
}
.col-actions {
  display: flex;
  gap: 8px;
}
/* 桌面端：head-extra 以 contents 透传给 col-actions 的 flex 布局 */
.head-extra {
  display: contents;
}
.sa-btn {
  padding: 6px 14px;
  border: 1px solid var(--sa-border);
  border-radius: 9999px;
  background: transparent;
  color: var(--sa-text-secondary);
  font-size: 13px;
  cursor: pointer;
}
.sa-btn--danger {
  color: var(--sa-danger, #e5484d);
  border-color: transparent;
}
.video-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(var(--grid-min, 220px), 1fr));
  gap: 18px;
  /* VideoCard 兜底宽 224px：手机 2 列轨道(~165px)装不下会横向溢出压到隔壁卡（收藏夹视频叠在一起），
     与 entity-detail.css 同口径 —— 卡片一律填满网格轨道 */
  --video-card-width: 100%;
}
.density-compact { --grid-min: 180px; }
.density-standard { --grid-min: 220px; }
.density-loose { --grid-min: 280px; }
@media (max-width: 640px) {
  /* 移动端：密度切换隐藏，由 ⚙ 收纳入口替代 */
  .head-extra {
    display: none;
  }
}
@media (max-width: 768px) {
  .mv-container {
    padding: 0 16px 48px;
  }
  .col-name {
    font-size: 22px;
  }
  /* 手机端固定列数（与 entity-detail.css 同口径）：紧凑/标准=2 列、宽松=1 列。
     原先只有紧凑档给了 2 列，标准档走 auto-fill(220px) → 390px 下退化成**单列全宽巨卡**。 */
  .density-compact { --cols-mobile: 2; }
  .density-standard { --cols-mobile: 2; }
  .density-loose { --cols-mobile: 1; }
  .video-grid {
    --video-card-width: 100%;
    grid-template-columns: repeat(var(--cols-mobile, 2), minmax(0, 1fr));
    gap: 12px 10px;
  }
}
</style>
