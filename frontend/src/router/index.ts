import { nextTick } from 'vue'
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { MIRROR_MODE } from '@/utils/mirror'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { title: '登录', public: true },
  },
  {
    path: '/setup',
    name: 'setup',
    component: () => import('@/views/SetupView.vue'),
    meta: { title: '初始化', public: true },
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    children: [
      {
        path: '',
        name: 'home',
        component: () => import('@/views/HomeView.vue'),
        meta: { title: '首页' },
      },
      {
        path: 'browse',
        name: 'browse',
        component: () => import('@/views/BrowseView.vue'),
        meta: { title: '浏览' },
      },
      {
        path: 'shorts',
        name: 'shorts',
        component: () => import('@/views/ShortsView.vue'),
        meta: { title: '短视频' },
      },
      {
        path: 'shorts/feed',
        name: 'shorts-feed',
        component: () => import('@/views/ShortsFeedView.vue'),
        meta: { title: '随机刷' },
      },
      {
        path: 'songs',
        name: 'songs-list',
        component: () => import('@/views/SongsListView.vue'),
        meta: { title: '歌曲列表' },
      },
      {
        path: 'songs/:uid',
        name: 'song-detail',
        component: () => import('@/views/SongDetailView.vue'),
        meta: { title: '歌曲详情' },
      },
      {
        path: 'stats',
        name: 'stats',
        component: () => import('@/views/StatsView.vue'),
        meta: { title: '统计' },
      },
      {
        path: 'videos/:uid',
        name: 'video-play',
        component: () => import('@/views/VideoPlayView.vue'),
        meta: { title: '视频播放' },
      },
      {
        path: 'artists',
        name: 'artists-list',
        component: () => import('@/views/ArtistsListView.vue'),
        meta: { title: '艺人列表' },
      },
      {
        path: 'artists/:uid',
        name: 'artist-detail',
        component: () => import('@/views/ArtistDetailView.vue'),
        meta: { title: '艺人详情' },
      },
      {
        path: 'groups',
        name: 'groups-list',
        component: () => import('@/views/GroupsListView.vue'),
        meta: { title: '组合列表' },
      },
      {
        path: 'groups/:uid',
        name: 'group-detail',
        component: () => import('@/views/GroupDetailView.vue'),
        meta: { title: '组合详情' },
      },
      {
        path: 'db',
        name: 'data-health',
        component: () => import('@/views/DataHealthView.vue'),
        meta: { title: '资料库 · 数据体检' },
      },
      {
        path: 'db/list',
        name: 'db-list',
        component: () => import('@/views/DbListView.vue'),
        meta: { title: '资料库 · 组合' },
      },
      {
        path: 'db/artists',
        name: 'db-artists-list',
        component: () => import('@/views/DbArtistsList.vue'),
        meta: { title: '资料库 · 艺人' },
      },
      {
        path: 'db/albums',
        name: 'db-albums-list',
        component: () => import('@/views/DbAlbumsList.vue'),
        meta: { title: '资料库 · 专辑' },
      },
      {
        path: 'db/albums/:uid',
        name: 'db-album-workspace',
        component: () => import('@/views/DbAlbumWorkspace.vue'),
        meta: { title: '资料库 · 专辑工作台' },
      },
      {
        path: 'db/songs',
        name: 'db-songs-list',
        component: () => import('@/views/DbSongsList.vue'),
        meta: { title: '资料库 · 歌曲' },
      },
      {
        path: 'db/recycle',
        name: 'db-recycle',
        component: () => import('@/views/DbRecycleView.vue'),
        meta: { title: '资料库 · 回收站' },
      },
      {
        path: 'db/songs/:uid',
        name: 'db-song-workspace',
        component: () => import('@/views/DbSongWorkspace.vue'),
        meta: { title: '资料库 · 歌曲工作台' },
      },
      {
        path: 'db/groups/:uid',
        name: 'db-group-workspace',
        component: () => import('@/views/DbGroupWorkspace.vue'),
        meta: { title: '资料库 · 实体工作台' },
      },
      {
        path: 'db/artists/:uid',
        name: 'db-artist-workspace',
        component: () => import('@/views/DbArtistWorkspace.vue'),
        meta: { title: '资料库 · 艺人工作台' },
      },
      {
        path: 'collections',
        name: 'collections-list',
        component: () => import('@/views/CollectionsListView.vue'),
        meta: { title: '收藏' },
      },
      {
        path: 'collections/:uid',
        name: 'collection-detail',
        component: () => import('@/views/CollectionDetailView.vue'),
        meta: { title: '收藏夹' },
      },
      {
        path: 'video-collections/:uid',
        name: 'video-collection-detail',
        component: () => import('@/views/VideoCollectionDetailView.vue'),
        meta: { title: '视频收藏夹' },
      },
      {
        path: 'uploaders',
        name: 'uploaders-list',
        component: () => import('@/views/UploadersListView.vue'),
        meta: { title: '博主列表' },
      },
      {
        path: 'uploaders/:name',
        name: 'uploader-detail',
        component: () => import('@/views/UploaderDetailView.vue'),
        meta: { title: '博主详情' },
      },
      {
        path: 'settings',
        name: 'settings',
        component: () => import('@/views/SettingsView.vue'),
        meta: { title: '设置' },
      },
      {
        path: ':pathMatch(.*)*',
        name: 'not-found',
        component: () => import('@/views/NotFoundView.vue'),
        meta: { title: '页面不存在' },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// ===== 顶栏导航共享元素动画（FLIP）=====
// 首页导航标签成组在右栏左缘，其它页面在页面正中。路由切换时记录旧位置，
// 新页面渲染后把新导航反向平移回旧位置再弹簧滑动到目标位 —— 视觉上同一组
// 标签连续滑动（进入其它页滑向居中，返回首页滑回原位）。仅桌面端生效。
const NAV_FLIP_SELECTORS = ['nav', 'actions'] as const
type NavFlipKind = (typeof NAV_FLIP_SELECTORS)[number]
let navFlipSnapshot: { kind: NavFlipKind; rect: DOMRect }[] = []

function snapshotHeaderNav() {
  navFlipSnapshot = []
  for (const kind of NAV_FLIP_SELECTORS) {
    const el = document.querySelector<HTMLElement>(`[data-sa-${kind}]`)
    if (el) navFlipSnapshot.push({ kind, rect: el.getBoundingClientRect() })
  }
}

async function playHeaderNavFlip() {
  const snapshot = navFlipSnapshot
  navFlipSnapshot = []
  if (!snapshot.length) return
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  await nextTick()
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
  for (const { kind, rect } of snapshot) {
    // 旧导航已滚出视口（如首页右栏 scrolled 上去）时不做位移
    if (rect.bottom < 0 || rect.top > window.innerHeight) continue
    const el = document.querySelector<HTMLElement>(`[data-sa-${kind}]`)
    if (!el) continue
    const now = el.getBoundingClientRect()
    const dx = rect.left - now.left
    const dy = rect.top - now.top
    if (Math.abs(dx) < 2 && Math.abs(dy) < 2) continue
    el.getAnimations().forEach((a) => a.cancel())
    el.animate(
      [
        { transform: `translate(${dx}px, ${dy}px)` },
        { transform: 'translate(0px, 0px)' },
      ],
      { duration: 480, easing: 'cubic-bezier(0.16, 1, 0.3, 1)' },
    )
  }
}

router.beforeEach(async (to) => {
  snapshotHeaderNav()
  // 在线版不开放设置页（含直接输入地址），一律回首页
  if (MIRROR_MODE && to.path.startsWith('/settings')) {
    return { path: '/' }
  }
  const auth = useAuthStore()
  if (!auth.ready) {
    await auth.loadStatus()
  }
  const isPublic = to.meta.public === true
  if (auth.bootstrapRequired && to.name !== 'setup') {
    return { name: 'setup' }
  }
  if (!auth.bootstrapRequired && to.name === 'setup' && auth.authenticated) {
    return { path: '/' }
  }
  if (!isPublic && !auth.authenticated && auth.authRequired) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (auth.authenticated && (to.name === 'login' || to.name === 'setup')) {
    return { path: '/' }
  }
  return true
})

router.afterEach((to) => {
  const title = (to.meta?.title as string) || ''
  document.title = title ? `${title} · idolMatrix` : 'idolMatrix'
  void playHeaderNavFlip()
})

export default router
