<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NSpin } from 'naive-ui'
import SaHeader from '@/components/SaHeader.vue'
import VideoCard from '@/components/VideoCard.vue'
import VideoFilterBar from '@/components/VideoFilterBar.vue'
import PhotoGallery from '@/components/PhotoGallery.vue'
import { artistsApi } from '@/api/artists'
import { membershipsApi } from '@/api/memberships'
import { musicVideosApi } from '@/api/musicVideos'
import type { Artist, MembershipWithGroup } from '@/types/models'
import { formatDate, initialOf } from '@/utils/format'
import { formatPositions } from '@/utils/positions'
import { artistPath, fetchByRouteParam, groupPath, videoPath } from '@/utils/routes'
import { MV_TYPES, LIVE_TYPES, videoHasType } from '@/utils/videoTypes'
import { heroGradient } from '@/utils/heroPalette'
import { nameSizeTier } from '@/utils/heroTitle'
import { useGridDensity } from '@/composables/useGridDensity'
import { useEntityDetail, ENTITY_TABS } from '@/composables/useEntityDetail'
import { useDetailPageTheme } from '@/composables/useDetailPageTheme'
import { MovieOutlined } from '@/components/icons'

const router = useRouter()
const activeTab = ref('overview')
// listAll 全量加载、无分页：密度切换只改变网格列数
const { density } = useGridDensity()

const memberships = ref<MembershipWithGroup[]>([])
const portraitStamp = ref(Date.now())

const {
  entity: artist,
  loading,
  notFound,
  videosError,
  retryVideos,
  longVideos,
  latestVideos,
  activeVideos,
  videoSongId,
  videoSearchText,
  videoSortKey,
  videoSongOptions,
  displayedVideos,
  hasVideoFilters,
  resetVideoFilters,
  timeline,
  isPhotoTab,
  photoSection,
  tabsEl,
  tabsHidden,
} = useEntityDetail<Artist>(
  {
    fetchEntity: (param) => fetchByRouteParam(param, artistsApi.getByUid, artistsApi.get),
    entityPath: artistPath,
    fetchVideos: (a) => musicVideosApi.listAll({ artist_id: a.id, ingestion_status: 'library' }),
    fetchExtras: async (a, isCurrent) => {
      const ms = await membershipsApi.byArtist(a.id).catch(() => [] as MembershipWithGroup[])
      if (isCurrent()) memberships.value = ms
    },
  },
  activeTab,
)

const heroBg = computed(() => heroGradient(artist.value?.id ?? 0))

const heroName = computed(() =>
  artist.value?.chinese_name ||
  artist.value?.stage_name ||
  artist.value?.korean_name ||
  artist.value?.name ||
  '',
)
const heroAvatarSrc = computed(() =>
  artist.value?.avatar_path
    ? `${artistsApi.avatarUrl(artist.value.id)}?t=${portraitStamp.value}`
    : '',
)
const heroBannerSrc = computed(() =>
  artist.value?.banner_path
    ? `${artistsApi.bannerUrl(artist.value.id)}?t=${portraitStamp.value}`
    : '',
)
const { heroKind, coverSrc, pageStyle } = useDetailPageTheme({
  entityId: () => artist.value?.id ?? 0,
  hasAvatar: () => !!artist.value?.avatar_path,
  hasBanner: () => !!artist.value?.banner_path,
  avatarSrc: () => heroAvatarSrc.value,
  bannerSrc: () => heroBannerSrc.value,
})
/**
 * PC 整页气氛层取图：横幅优先、无横幅回退封面。
 * 轻模糊铺满视口顶部，统一取 480px 变体即可。
 */
const heroBgSrc = computed(() => {
  const a = artist.value
  if (!a) return ''
  const base = a.banner_path
    ? artistsApi.bannerUrl(a.id)
    : a.avatar_path
      ? artistsApi.avatarUrl(a.id)
      : ''
  return base ? `${base}?w=480&t=${portraitStamp.value}` : ''
})
/**
 * 手机端封面大字 = **艺名**（拉丁优先，如 HeeJin）；没有艺名才按 中文名 → 韩文名 → 原名 退回。
 * 旧口径是「中文名 (韩文名)」两段，2026-09-21 改为只显示艺名、不再拼括号。
 * ⚠ PC 大字 `heroName` 仍是 `chinese_name → stage_name → …`，两套口径有意不同。
 */
const coverName = computed(() => {
  const a = artist.value
  if (!a) return ''
  return a.stage_name || a.chinese_name || a.korean_name || a.name || ''
})
/**
 * 封面小字里前缀的本名：中文名 → 韩文名 → 英文名（与大字重复就不显示）。
 * 成品形如「田姬振 · 27部作品 · ARTMS」。
 */
const coverRealName = computed(() => {
  const a = artist.value
  if (!a) return ''
  const v = a.chinese_name || a.korean_name || a.english_name || ''
  return v && v !== coverName.value ? v : ''
})
// 手机端大字的分档类名：只看大字本身（不含任何后缀），
// 阈值与首页轮播共用 nameSizeTier()，见 entity-detail.css 的 .hero-cover-name
const coverNameClass = computed(() => {
  const tier = nameSizeTier(coverName.value)
  const cls = tier === 'base' ? [] : [`hero-cover-name--${tier}`]
  // 名字是汉字（如中国艺人宋昕冉）才挂思源宋体；拉丁/韩文刻意不挂，字体与旧版一致
  if (/[\u3400-\u9fff]/.test(coverName.value)) cls.push('hero-cover-name--han')
  return cls.join(' ')
})
const heroSub = computed(() => {
  const a = artist.value
  if (!a) return ''
  const main = heroName.value
  const parts: string[] = []
  for (const v of [a.chinese_name, a.stage_name, a.korean_name, a.name]) {
    if (v && v !== main && !parts.includes(v)) parts.push(v)
  }
  return parts.join(' · ')
})

const tags = computed(() => {
  const set: string[] = []
  for (const m of memberships.value) {
    if (m.group_type && !set.includes(m.group_type)) set.push(m.group_type)
  }
  if (artist.value?.occupation) set.push(artist.value.occupation)
  return set.length ? set : ['艺人']
})

const activeMembership = computed(
  () => memberships.value.find((x) => x.status === 'Active') || memberships.value[0] || null,
)
const heroGroupName = computed(() => {
  const m = activeMembership.value
  if (!m) return ''
  // 优先显示母队（完整体型），小分队自动溯源到 top-level 母队
  return (
    m.root_group_chinese_name ||
    m.root_group_name ||
    m.group_chinese_name ||
    m.group_name ||
    ''
  )
})
const heroGroupUid = computed(
  () => activeMembership.value?.root_group_uid || activeMembership.value?.group_uid || '',
)
const heroGroupDetail = computed(() => {
  const m = activeMembership.value
  if (!m) return ''
  const parts: string[] = []
  if (m.join_date) parts.push(`${m.join_date} 加入`)
  if (m.positions?.length) parts.push(formatPositions(m.positions))
  if (m.status === 'Former') parts.push('已退出')
  return parts.join(' · ')
})

const worksMeta = computed(() => `${longVideos.value.length}部作品`)

const stats = computed(() => {
  const mv = longVideos.value.filter((v) => videoHasType(v, MV_TYPES)).length
  const live = longVideos.value.filter((v) =>
    videoHasType(v, LIVE_TYPES),
  ).length
  return [
    { num: mv, label: 'MV 出演' },
    { num: live, label: '现场 · 直拍' },
    { num: longVideos.value.length, label: '全部作品' },
  ]
})

const bio = computed(() => (artist.value?.description || '').replace(/\{BR\}/g, '\n'))
const bioExpanded = ref(false)
const toggleBio = () => {
  bioExpanded.value = !bioExpanded.value
}

const quickFacts = computed(() => {
  const a = artist.value
  if (!a) return []
  const facts: { label: string; value: string; to?: string }[] = []
  if (a.korean_name) facts.push({ label: '韩文名', value: a.korean_name })
  if (a.stage_name) facts.push({ label: '艺名', value: a.stage_name })
  if (a.birth_date) {
    facts.push({
      label: '出生',
      value: `${formatDate(a.birth_date)}${a.birth_place ? ` · ${a.birth_place}` : ''}`,
    })
  }
  if (a.debut_date) facts.push({ label: '出道', value: formatDate(a.debut_date) })
  if (a.occupation) facts.push({ label: '职业', value: a.occupation })
  for (const m of memberships.value) {
    const gname = m.group_chinese_name || m.group_name || ''
    if (!gname) continue
    const parts: string[] = [gname]
    if (m.join_date) parts.push(`${m.join_date} 加入`)
    if (m.status === 'Former') parts.push('已退出')
    facts.push({
      label: '组合',
      value: parts.join(' · '),
      to: m.group_uid ? groupPath(m.group_uid) : undefined,
    })
    if (m.positions?.length) facts.push({ label: '担当', value: formatPositions(m.positions) })
  }
  if (a.aliases?.length) facts.push({ label: '别名', value: a.aliases.join(' / ') })
  return facts
})

function goVideo(uid: string) {
  router.push(videoPath(uid))
}

function goGroup(uid?: string | null) {
  if (!uid) return
  router.push(groupPath(uid))
}

function goBack() {
  if (window.history.length > 1) router.back()
  else router.push('/')
}

async function onPortraitApplied() {
  if (!artist.value) return
  try {
    artist.value = await artistsApi.get(artist.value.id)
    portraitStamp.value = Date.now()
  } catch {
    /* 裁切已成功，刷新失败时下次进入页面会看到 */
  }
}
</script>

<template>
  <div
    class="mv-page"
    :class="{ 'mv-page--atmosphere': !!artist }"
    :style="pageStyle"
  >
    <div v-if="artist" class="hero-bg" aria-hidden="true">
      <img v-if="heroBgSrc" class="hero-bg-img" :src="heroBgSrc" alt="" />
      <div v-else class="hero-bg-fallback" :style="{ background: heroBg }" />
    </div>
    <SaHeader />
    <div class="mv-container">
      <n-spin :show="loading">
        <div v-if="notFound" class="empty-state">
          <MovieOutlined :size="40" />
          <p>艺人不存在或已删除</p>
          <button class="empty-back" @click="goBack">返回首页</button>
        </div>

        <div v-else-if="artist">
          <section
            class="hero hero--pc-bg"
            :class="{
              'hero--banner': heroKind === 'banner',
            }"
          >
            <div class="hero-cover">
              <img v-if="coverSrc" class="hero-cover-img" :src="coverSrc" alt="" />
              <div v-else class="hero-cover-fallback" :style="{ background: heroBg }" />
              <div class="hero-cover-text">
                <div class="hero-cover-name" :class="coverNameClass">{{ coverName }}</div>
                <div class="hero-cover-meta">
                  <span v-if="coverRealName">{{ coverRealName }}</span>
                  <span>{{ worksMeta }}</span>
                  <span v-if="heroGroupName">{{ heroGroupName }}</span>
                </div>
              </div>
            </div>
            <div class="hero-content">
              <div class="hero-avatar" :style="{ background: heroBg }">
                <img
                  v-if="artist.avatar_path"
                  class="hero-avatar-img"
                  :src="heroAvatarSrc"
                  alt=""
                />
                <template v-else>{{ initialOf(heroName) }}</template>
              </div>
              <div class="hero-info">
                <div class="hero-title">
                  <h1 class="hero-name">{{ heroName }}</h1>
                  <div class="hero-meta hero-mobile">
                    <span class="hero-meta-item">{{ worksMeta }}</span>
                  </div>
                  <div v-if="heroSub" class="hero-sub">{{ heroSub }}</div>
                </div>
                <div class="hero-tags">
                  <span v-for="tag in tags" :key="tag" class="badge">{{ tag }}</span>
                </div>
                <div v-if="heroGroupName" class="hero-group">
                  <span class="hero-group-label">组合</span>
                  <button
                    v-if="heroGroupUid"
                    class="hero-group-name hero-group-name--link"
                    type="button"
                    @click="goGroup(heroGroupUid)"
                  >{{ heroGroupName }}</button>
                  <span v-else class="hero-group-name">{{ heroGroupName }}</span>
                  <span v-if="heroGroupDetail" class="hero-group-detail">· {{ heroGroupDetail }}</span>
                </div>
                <div class="hero-stats hero-pc">
                  <div v-for="s in stats" :key="s.label" class="stat">
                    <div class="stat-num">{{ s.num }}</div>
                    <div class="stat-label">{{ s.label }}</div>
                  </div>
                </div>
              </div>
            </div>
          </section>

          <div ref="tabsEl" class="tabs" :class="{ 'tabs--hidden': tabsHidden }">
            <div class="tabs-scroll">
              <button
                v-for="t in ENTITY_TABS"
                :key="t.key"
                class="tab"
                :class="{ 'tab--active': activeTab === t.key }"
                @click="activeTab = t.key"
              >
                {{ t.label }}
              </button>
            </div>
          </div>

          <div v-if="videosError && !isPhotoTab" class="load-error">
            <span>{{ videosError }}</span>
            <button class="load-error-retry" type="button" @click="retryVideos">重试</button>
          </div>

          <div v-if="activeTab === 'overview'" class="overview-grid">
            <div class="overview-main">
              <section class="block">
                <h2 class="block-title">艺人简介</h2>
                <p v-if="bio" class="bio" :class="{ 'bio--expanded': bioExpanded }" @click="toggleBio">{{ bio }}</p>
                <p v-else class="bio bio--muted">暂无简介</p>
              </section>

              <section class="block">
                <h2 class="block-title">最新作品</h2>
                <div v-if="latestVideos.length" class="card-row">
                  <VideoCard v-for="(v, i) in latestVideos" :key="v.id" :video="v" :index="i" />
                </div>
                <p v-else class="empty-tip">暂无作品</p>
              </section>

              <section v-if="timeline.length" class="block">
                <h2 class="block-title">近期动态</h2>
                <div class="timeline">
                  <div v-for="item in timeline" :key="item.id" class="timeline-item">
                    <span class="timeline-dot" />
                    <div class="timeline-body">
                      <button class="timeline-title" @click="goVideo(item.uid)">
                        {{ item.title }}
                      </button>
                      <div class="timeline-meta">{{ formatDate(item.date) }} · {{ item.type }}</div>
                    </div>
                  </div>
                </div>
              </section>
            </div>

            <aside class="overview-side">
              <section v-if="quickFacts.length" class="side-card">
                <h3 class="side-title">资料速览</h3>
                <div class="side-grid">
                  <div v-for="row in quickFacts" :key="row.label + row.value" class="side-row">
                    <div class="side-label">{{ row.label }}</div>
                    <div class="side-value">
                      <button
                        v-if="row.to"
                        class="side-link"
                        type="button"
                        @click="router.push(row.to)"
                      >{{ row.value }}</button>
                      <template v-else>{{ row.value }}</template>
                    </div>
                  </div>
                </div>
              </section>
            </aside>
          </div>

          <PhotoGallery
            v-else-if="isPhotoTab && artist"
            owner-type="artist"
            :owner-id="artist.id"
            :section="photoSection"
            @portrait-applied="onPortraitApplied"
          />

          <div v-else class="video-section">
            <VideoFilterBar
              v-if="activeVideos.length"
              :count="displayedVideos.length"
              :songs="videoSongOptions"
              v-model:song-id="videoSongId"
              v-model:sort="videoSortKey"
              v-model:query="videoSearchText"
            />
            <div
              v-if="displayedVideos.length"
              class="video-grid"
              :class="[`density-${density}`, { 'video-grid--portrait': activeTab === 'shorts' }]"
            >
              <VideoCard
                v-for="(v, i) in displayedVideos"
                :key="v.id"
                :video="v"
                :index="i"
                :portrait="activeTab === 'shorts'"
              />
            </div>
            <div v-else-if="activeVideos.length && hasVideoFilters" class="empty-state">
              <MovieOutlined :size="32" />
              <p>没有符合筛选条件的作品</p>
              <button class="empty-back" type="button" @click="resetVideoFilters">清除筛选</button>
            </div>
            <div v-else class="empty-state">
              <MovieOutlined :size="32" />
              <p>该分类下暂无作品</p>
            </div>
          </div>
        </div>
      </n-spin>
    </div>
  </div>
</template>

<style scoped src="./entity-detail.css"></style>
