import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

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

router.beforeEach(async (to) => {
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
})

export default router
