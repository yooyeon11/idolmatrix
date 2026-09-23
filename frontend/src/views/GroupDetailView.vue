<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NSpin } from 'naive-ui'
import SaHeader from '@/components/SaHeader.vue'
import VideoCard from '@/components/VideoCard.vue'
import VideoFilterBar from '@/components/VideoFilterBar.vue'
import PhotoGallery from '@/components/PhotoGallery.vue'
import { groupsApi } from '@/api/groups'
import { artistsApi } from '@/api/artists'
import { membershipsApi } from '@/api/memberships'
import { companiesApi } from '@/api/companies'
import { musicVideosApi } from '@/api/musicVideos'
import type { Company, Group, MembershipWithArtist } from '@/types/models'
import { formatDate, initialOf } from '@/utils/format'
import { artistPath, fetchByRouteParam, groupPath, videoPath } from '@/utils/routes'
import { MV_TYPES, LIVE_TYPES, videoHasType } from '@/utils/videoTypes'
import { heroGradient } from '@/utils/heroPalette'
import { nameSizeTier } from '@/utils/heroTitle'
import { useGridDensity } from '@/composables/useGridDensity'
import { useEntityDetail, ENTITY_TABS } from '@/composables/useEntityDetail'
import { useDetailPageTheme } from '@/composables/useDetailPageTheme'
import { GroupOutlined, MovieOutlined } from '@/components/icons'

const MEMBER_COLLAPSE_AT = 12

const router = useRouter()
const activeTab = ref('overview')
// listAll 全量加载、无分页：密度切换只改变网格列数
const { density } = useGridDensity()

const members = ref<MembershipWithArtist[]>([])
const companies = ref<Company[]>([])
const portraitStamp = ref(Date.now())
// 成员头像已改走 ETag 协商（memberAvatarSrc），不再需要缓存戳

const {
  entity: group,
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
} = useEntityDetail<Group>(
  {
    fetchEntity: (param) => fetchByRouteParam(param, groupsApi.getByUid, groupsApi.get),
    entityPath: groupPath,
    fetchVideos: (g) => musicVideosApi.listAll({ group_id: g.id, ingestion_status: 'library' }),
    fetchExtras: async (g, isCurrent) => {
      const [ms, cs] = await Promise.all([
        membershipsApi.byGroup(g.id).catch(() => [] as MembershipWithArtist[]),
        companiesApi.brief().catch(() => [] as Company[]),
      ])
      if (!isCurrent()) return
      members.value = ms
      companies.value = cs
    },
  },
  activeTab,
)

const heroBg = computed(() => heroGradient(group.value?.id ?? 0))

const heroName = computed(() =>
  group.value?.english_name ||
  group.value?.name ||
  group.value?.chinese_name ||
  group.value?.korean_name ||
  '',
)
const heroAvatarSrc = computed(() =>
  group.value?.avatar_path
    ? `${groupsApi.avatarUrl(group.value.id)}?t=${portraitStamp.value}`
    : '',
)
const heroBannerSrc = computed(() =>
  group.value?.banner_path
    ? `${groupsApi.bannerUrl(group.value.id)}?t=${portraitStamp.value}`
    : '',
)
const { heroKind, coverSrc, pageStyle } = useDetailPageTheme({
  entityId: () => group.value?.id ?? 0,
  hasAvatar: () => !!group.value?.avatar_path,
  hasBanner: () => !!group.value?.banner_path,
  avatarSrc: () => heroAvatarSrc.value,
  bannerSrc: () => heroBannerSrc.value,
})
/**
 * PC 整页气氛层取图：横幅优先、无横幅回退封面。
 * 轻模糊铺满视口顶部，统一取 480px 变体即可。
 */
const heroBgSrc = computed(() => {
  const g = group.value
  if (!g) return ''
  const base = g.banner_path
    ? groupsApi.bannerUrl(g.id)
    : g.avatar_path
      ? groupsApi.avatarUrl(g.id)
      : ''
  return base ? `${base}?w=480&t=${portraitStamp.value}` : ''
})
/**
 * 手机端封面大字 = **组合英文名**（如 ARTMS）；没有英文名才按 韩文名 → 中文名 → 原名 退回。
 * 旧口径是「英文名 (韩文名)」两段，2026-09-21 改为只显示英文名、不再拼括号。
 * ⚠ PC 大字 `heroName` 仍是 `english_name → name → chinese_name → korean_name`，两套口径有意不同。
 * `name` 只作最后的兜底（前三级全空时才会用到），不影响用户指定的回退次序。
 */
const coverName = computed(() => {
  const g = group.value
  if (!g) return ''
  return g.english_name || g.korean_name || g.chinese_name || g.name || ''
})
// 手机端大字的分档类名：只看大字本身（不含任何后缀），
// 阈值与首页轮播共用 nameSizeTier()，见 entity-detail.css 的 .hero-cover-name
const coverNameClass = computed(() => {
  const tier = nameSizeTier(coverName.value)
  const cls = tier === 'base' ? [] : [`hero-cover-name--${tier}`]
  // 名字是汉字（退到中文名的组合，如「本月少女」）才挂自托管思源宋体；
  // 纯拉丁/韩文刻意不挂，字体与旧版一致
  if (/[\u3400-\u9fff]/.test(coverName.value)) cls.push('hero-cover-name--han')
  return cls.join(' ')
})
const companyNames = computed(() => {
  const ids = group.value?.company_ids || []
  if (!ids.length) return []
  return companies.value
    .filter((x) => ids.includes(x.id))
    .map((c) => c.chinese_name || c.name)
    .filter(Boolean)
})
const companyName = computed(() => companyNames.value[0] || '')

const sortedMembers = computed(() => {
  const list = [...members.value]
  list.sort((a, b) => {
    const order = (s?: string) => (s === 'Active' ? 0 : 1)
    const od = order(a.status) - order(b.status)
    if (od) return od
    return (a.join_date || '').localeCompare(b.join_date || '')
  })
  return list
})

const activeMemberCount = computed(
  () => members.value.filter((x) => x.status === 'Active').length,
)
const membersExpanded = ref(false)
watch(
  () => group.value?.id,
  () => {
    membersExpanded.value = false
  },
)
const hiddenMemberCount = computed(() =>
  Math.max(0, sortedMembers.value.length - MEMBER_COLLAPSE_AT),
)

const memberMeta = computed(() => {
  const n = activeMemberCount.value || members.value.length
  return n ? `${n}名成员` : ''
})
const worksMeta = computed(() => `${longVideos.value.length}部作品`)

const stats = computed(() => {
  const mv = longVideos.value.filter((v) => videoHasType(v, MV_TYPES)).length
  const live = longVideos.value.filter((v) =>
    videoHasType(v, LIVE_TYPES),
  ).length
  return [
    { num: activeMemberCount.value, label: '现成员' },
    { num: members.value.length, label: '成员总数' },
    { num: mv, label: 'MV 出演' },
    { num: live, label: '现场 · 直拍' },
    { num: longVideos.value.length, label: '全部作品' },
  ]
})

const bio = computed(() => (group.value?.description || '').replace(/\{BR\}/g, '\n'))
const bioExpanded = ref(false)
const toggleBio = () => {
  bioExpanded.value = !bioExpanded.value
}

type FactRow = {
  label: string
  value?: string
  to?: string
  links?: { label: string; to: string }[]
  mobileOnly?: boolean
}

const quickFacts = computed(() => {
  const g = group.value
  if (!g) return [] as FactRow[]
  const facts: FactRow[] = []
  if (g.korean_name) facts.push({ label: '韩文名', value: g.korean_name })
  if (g.debut_date) facts.push({ label: '出道', value: formatDate(g.debut_date) })
  if (g.group_type) facts.push({ label: '组合类型', value: g.group_type })
  if (g.gender_type) facts.push({ label: '性别类型', value: g.gender_type })
  if (g.origin_country) facts.push({ label: '出道国家/地区', value: g.origin_country })
  if (companyNames.value.length) {
    facts.push({ label: '所属公司', value: companyNames.value.join('、') })
  }
  if (g.parent_name) {
    facts.push({
      label: '隶属于',
      value: g.parent_name,
      to: g.parent_uid ? groupPath(g.parent_uid) : undefined,
      mobileOnly: true,
    })
  }
  if (g.sub_units?.length) {
    facts.push({
      label: '旗下小分队',
      links: g.sub_units.map((s) => ({
        label: s.chinese_name || s.name,
        to: groupPath(s.uid),
      })),
    })
  }
  if (g.aliases?.length) facts.push({ label: '别名', value: g.aliases.join(' / ') })
  return facts
})

function memberName(m: MembershipWithArtist) {
  return (
    m.artist_chinese_name ||
    m.artist_stage_name ||
    m.artist_korean_name ||
    m.artist_name ||
    `#${m.artist_id}`
  )
}

function memberAvatarSrc(m: MembershipWithArtist) {
  // w=256：成员头像是小缩略图，用缩放变体；去掉 ?t= 让 ETag 协商生效，
  // 否则每次进详情页几十个成员头像全部重拉原图（原 stamp 机制从未 bump，纯击穿缓存）
  return `${artistsApi.avatarUrl(m.artist_id)}?w=256`
}

function goVideo(uid: string) {
  router.push(videoPath(uid))
}

function goArtist(uid?: string | null) {
  if (!uid) return
  router.push(artistPath(uid))
}

function goBack() {
  if (window.history.length > 1) router.back()
  else router.push('/')
}

async function onPortraitApplied() {
  if (!group.value) return
  try {
    group.value = await groupsApi.get(group.value.id)
    portraitStamp.value = Date.now()
  } catch {
    /* 裁切已成功，刷新失败时下次进入页面会看到 */
  }
}
</script>

<template>
  <div
    class="mv-page"
    :class="{ 'mv-page--atmosphere': !!group }"
    :style="pageStyle"
  >
    <div v-if="group" class="hero-bg" aria-hidden="true">
      <img v-if="heroBgSrc" class="hero-bg-img" :src="heroBgSrc" alt="" />
      <div v-else class="hero-bg-fallback" :style="{ background: heroBg }" />
    </div>
    <SaHeader />
    <div class="mv-container">
      <n-spin :show="loading">
        <div v-if="notFound" class="empty-state">
          <GroupOutlined :size="40" />
          <p>组合不存在或已删除</p>
          <button class="empty-back" @click="goBack">返回首页</button>
        </div>

        <div v-else-if="group">
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
                  <span v-if="memberMeta">{{ memberMeta }}</span>
                  <span>{{ worksMeta }}</span>
                  <span v-if="companyName">{{ companyName }}</span>
                </div>
              </div>
            </div>
            <div class="hero-content">
              <div class="hero-avatar" :style="{ background: heroBg }">
                <img
                  v-if="group.avatar_path"
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
                    <span v-if="memberMeta" class="hero-meta-item">{{ memberMeta }}</span>
                    <span class="hero-meta-item">{{ worksMeta }}</span>
                  </div>
                </div>
                <div v-if="companyName" class="hero-group hero-pc">
                  <span class="hero-group-label">所属公司</span>
                  <span class="hero-group-name">{{ companyName }}</span>
                </div>
                <div v-if="group?.parent_name" class="hero-group hero-pc">
                  <span class="hero-group-label">隶属于</span>
                  <a
                    v-if="group?.parent_uid"
                    class="hero-group-name hero-link"
                    :href="groupPath(group.parent_uid)"
                    @click.prevent="router.push(groupPath(group!.parent_uid!))"
                    >{{ group.parent_name }}</a
                  >
                  <span v-else class="hero-group-name">{{ group.parent_name }}</span>
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
                <h2 class="block-title">组合简介</h2>
                <p v-if="bio" class="bio" :class="{ 'bio--expanded': bioExpanded }" @click="toggleBio">{{ bio }}</p>
                <p v-else class="bio bio--muted">暂无简介</p>
              </section>

              <section v-if="members.length" class="block">
                <h2 class="block-title">成员（{{ members.length }}）</h2>
                <div
                  class="member-grid"
                  :class="{ 'member-grid--collapsed': !membersExpanded && hiddenMemberCount > 0 }"
                >
                  <button
                    v-for="m in sortedMembers"
                    :key="m.id"
                    class="member-card"
                    @click="goArtist(m.artist_uid)"
                  >
                    <div class="member-avatar">
                      <img
                        v-if="m.artist_avatar_path"
                        class="member-avatar-img"
                        :src="memberAvatarSrc(m)"
                        alt=""
                      />
                      <template v-else>{{ initialOf(memberName(m)) }}</template>
                    </div>
                    <div class="member-name">
                      {{ memberName(m) }}
                      <span v-if="m.status === 'Former'" class="member-former">已退出</span>
                    </div>
                  </button>
                </div>
                <button
                  v-if="hiddenMemberCount > 0"
                  class="member-more"
                  type="button"
                  @click="membersExpanded = !membersExpanded"
                >
                  {{ membersExpanded ? '收起' : `展开其余 ${hiddenMemberCount} 位成员` }}
                </button>
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
                  <div
                    v-for="row in quickFacts"
                    :key="row.label"
                    class="side-row"
                    :class="{ 'side-row--mobile': row.mobileOnly }"
                  >
                    <div class="side-label">{{ row.label }}</div>
                    <div class="side-value">
                      <template v-if="row.links?.length">
                        <template v-for="(l, i) in row.links" :key="l.to">
                          <button class="side-link" type="button" @click="router.push(l.to)">
                            {{ l.label }}
                          </button>
                          <span v-if="i < row.links.length - 1" class="side-sep">、</span>
                        </template>
                      </template>
                      <button
                        v-else-if="row.to"
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
            v-else-if="isPhotoTab && group"
            owner-type="group"
            :owner-id="group.id"
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
