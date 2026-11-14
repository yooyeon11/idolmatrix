<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { homeApi, type HeroStageItem } from '@/api/home'
import { artistsApi } from '@/api/artists'
import { membershipsApi } from '@/api/memberships'
import { musicVideosApi } from '@/api/musicVideos'
import type { MusicVideo } from '@/types/models'
import { artistPath, groupPath } from '@/utils/routes'
import BottomNav from '@/components/BottomNav.vue'
import HomeSpotlightCover from '@/components/home/HomeSpotlightCover.vue'
import HomeEditorialFeed, {
  type HomeIdolCard,
} from '@/components/home/HomeEditorialFeed.vue'

const router = useRouter()
const message = useMessage()

// 左列焦点封面池（随机艺人/组合，服务端保证有头像 + 有关联视频）
const stagePool = ref<HeroStageItem[]>([])
const stageIndex = ref(0)

// ===== 左右联动轮动：一个时钟同时推进「焦点海报」与「艺人立绘墙」 =====
// 同一 tick 切一次，两边动画时长/缓动一致，整页看起来是一次动作而不是两处各动各的
const ROTATE_MS = 6000
const GALLERY_SIZE = 4
const galleryPage = ref(0)
const rotationPaused = ref(false)
let rotateTimer: ReturnType<typeof setInterval> | undefined
let reducedMotion = false

/** 每次轮动 +1，用来触发右侧重新随机抽 4 人 */
const gallerySeed = ref(0)

/**
 * 右侧立绘墙：与左侧同一套规则——
 * 随机抽 GALLERY_SIZE 个，且排除左侧当前展示的艺人（原则二）。
 */
const galleryItems = computed(() => {
  void gallerySeed.value
  const cur = stagePool.value[stageIndex.value]
  const pool = idols.value.filter(
    (x) => !(cur && cur.type === 'artist' && x.id === cur.id),
  )
  const arr = [...pool]
  for (let i = arr.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[arr[i], arr[j]] = [arr[j], arr[i]]
  }
  return arr.slice(0, GALLERY_SIZE)
})

let lastAdvanceAt = 0

/**
 * 推进一步：**顺序 +1，不跳号**。
 *
 * 这里原来是「随机取一个不等于当前的下标」，结果就是第 1 张之后可能直接跳到
 * 第 3、第 5 张 —— 平板上海报占满整屏，换图非常显眼，业主判定为「轮换乱跳」。
 * 随机性保留在**池子**里（服务端 heroStage 随机抽 6 个），顺序不再随机。
 *
 * 2.5s 防抖是为 WebView 准备的：从后台/原生播放器回来时，被挂起的
 * `setInterval` 有几率补发两次回调，补发也只算一步。
 */
function advanceRotation() {
  if (document.hidden || rotationPaused.value) return
  const now = Date.now()
  if (now - lastAdvanceAt < 2500) return
  lastAdvanceAt = now
  const len = stagePool.value.length
  if (len > 1) stageIndex.value = (stageIndex.value + 1) % len
  gallerySeed.value += 1
}

function startRotation() {
  stopRotation()
  if (reducedMotion) return
  rotateTimer = setInterval(advanceRotation, ROTATE_MS)
}

function stopRotation() {
  if (rotateTimer) {
    clearInterval(rotateTimer)
    rotateTimer = undefined
  }
}

// 右列两个板块：焦点艺人立绘 + 最新入库视频
const idols = ref<HomeIdolCard[]>([])
const videos = ref<MusicVideo[]>([])
const artistTotal = ref(0)
const videoTotal = ref(0)
const loading = ref(false)
// 各板块本次加载是否失败：失败显示重试入口，成功但无数据才显示「暂无」
const failed = ref({ stage: false, idols: false, videos: false })

function idolName(a: {
  stage_name?: string | null
  name?: string | null
  chinese_name?: string | null
}) {
  return (a.stage_name || '').trim() || (a.name || '').trim() || (a.chinese_name || '').trim()
}

async function load() {
  loading.value = true
  failed.value = { stage: false, idols: false, videos: false }
  try {
    const [stageRes, artistsRes, videosRes] = await Promise.all([
      homeApi.heroStage(6).catch(() => {
        failed.value.stage = true
        return null
      }),
      artistsApi
        .list({ page_size: 40, only_with_videos: true, sort: '-videos' })
        .catch(() => {
          failed.value.idols = true
          return null
        }),
      // 首页只保留设计稿两个板块，短视频不进最新入库流
      musicVideosApi
        .list({ page_size: 2, ingestion_status: 'library', is_short: false })
        .catch(() => {
          failed.value.videos = true
          return null
        }),
    ])

    stagePool.value = stageRes?.items ?? []
    stageIndex.value = 0
    galleryPage.value = 0
    artistTotal.value = artistsRes?.total ?? 0
    videoTotal.value = videosRes?.total ?? 0
    videos.value = videosRes?.items ?? []

    // 立绘副标题需要所属组合名：一次批量拿全部在团关系（20 个请求会拖慢首页）
    // 立绘墙与左侧轮动同一套规则：只收「有竖屏海报（头像）」的艺人
    const artistList = (artistsRes?.items ?? []).filter(
      (a) => !!String(a.avatar_path || '').trim(),
    )
    const membershipRows = artistList.length
      ? await membershipsApi
          .byArtists(artistList.map((a) => a.id))
          .catch(() => [] as Awaited<ReturnType<typeof membershipsApi.byArtists>>)
      : []
    const byArtist = new Map<number, typeof membershipRows>()
    for (const m of membershipRows) {
      const key = (m as { artist_id?: number }).artist_id
      if (typeof key !== 'number') continue
      const list = byArtist.get(key) ?? []
      list.push(m)
      byArtist.set(key, list)
    }
    idols.value = artistList.map((a) => {
      const ms = byArtist.get(a.id)
      const active = ms?.find((m) => (m.status || 'active') === 'active') ?? ms?.[0]
      const groupName = active?.group_chinese_name || active?.group_name
      return {
        id: a.id,
        uid: a.uid,
        name: idolName(a),
        sub: groupName || '',
        avatar: `${artistsApi.avatarUrl(a.id)}?v=${encodeURIComponent(a.updated_at || '')}`,
        updated: a.updated_at || '',
      }
    })
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

function goStage(item: HeroStageItem) {
  router.push(item.type === 'artist' ? artistPath(item.uid) : groupPath(item.uid))
}

onMounted(() => {
  reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  void load().finally(startRotation)
})

onBeforeUnmount(stopRotation)
</script>

<template>
  <div class="home">
    <main class="home-main">
      <div
        class="home-grid"
        @mouseenter="rotationPaused = true"
        @mouseleave="rotationPaused = false"
      >
        <!-- 左 5/12：通顶 sticky 焦点海报封面 -->
        <aside class="home-cover">
          <HomeSpotlightCover
            :pool="stagePool"
            :index="stageIndex"
            :failed="failed.stage"
            :auto="false"
            @update:index="stageIndex = $event"
            @open="goStage"
            @retry="load"
          />
        </aside>

        <!-- 右 7/12：mini 导航 + 艺人立绘错位画廊 + 最新入库视频 -->
        <section class="home-feed">
          <HomeEditorialFeed
            :idols="galleryItems"
            :page="0"
            :videos="videos"
            :artist-total="artistTotal"
            :video-total="videoTotal"
            :loading="loading"
            :failed="{ idols: failed.idols, videos: failed.videos }"
            @retry="load"
          />
        </section>
      </div>
    </main>

    <!-- 移动端底栏（桌面端组件自行隐藏），H 方案无全局顶栏 -->
    <BottomNav />
  </div>
</template>

<style scoped>
.home {
  min-height: 100vh;
  background: var(--sa-bg);
  color: var(--sa-text-primary);
  font-size: 14px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}

/* H 方案骨架：左 5 列通顶封面 + 右 7 列阶梯流（满宽铺满视口，不做居中限宽） */
.home-grid {
  display: grid;
  grid-template-columns: minmax(0, 5fr) minmax(0, 7fr);
  min-height: 100vh;
}

/* 封面列 sticky 通顶：H 方案无全局顶栏，封面直接贴视口顶部 */
.home-cover {
  position: sticky;
  top: 0;
  height: 100vh;
  min-height: 0;
}

/* 顶部内边距与封面左上角品牌烙印（top:32px）齐平，让顶栏与烙印同一水平线 */
.home-feed {
  min-width: 0;
  padding: 28px 44px 96px;
}

/* 移动端：封面降级为顶部块，右列单栏 */
@media (max-width: 768px) {
  .home-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .home-cover {
    position: relative;
    top: auto;
    height: 62vh;
    min-height: 440px;
  }
  .home-feed {
    padding: 24px 16px 56px;
  }
}
</style>
