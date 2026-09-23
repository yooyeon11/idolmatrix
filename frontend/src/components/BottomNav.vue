<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  BarChartOutlined,
  BookmarkOutlined,
  GroupOutlined,
  MusicNoteOutlined,
  NavBrowseFill,
  NavBrowseLine,
  NavCloseLine,
  NavHomeFill,
  NavHomeLine,
  NavMoreLine,
  NavPersonFill,
  NavPersonLine,
  NavShortsFill,
  NavShortsLine,
  StarOutlined,
  UploadOutlined,
} from '@/components/icons'

const route = useRoute()

const isMobile = ref(false)
const open = ref(false)
const mql = window.matchMedia('(max-width: 768px)')

const moreItems = [
  { label: '艺人', to: '/artists', icon: StarOutlined },
  { label: '组合', to: '/groups', icon: GroupOutlined },
  { label: '歌曲', to: '/songs', icon: MusicNoteOutlined },
  { label: '统计', to: '/stats', icon: BarChartOutlined },
  { label: '收藏', to: '/collections', icon: BookmarkOutlined },
  { label: '博主', to: '/uploaders', icon: UploadOutlined },
]

function isActive(to: string, exact = false) {
  if (exact) return route.path === to
  return route.path === to || route.path.startsWith(`${to}/`)
}

const moreActive = computed(() => moreItems.some((item) => isActive(item.to)))

function onMqChange(e: MediaQueryListEvent) {
  isMobile.value = e.matches
  if (!e.matches) open.value = false
}

onMounted(() => {
  isMobile.value = mql.matches
  mql.addEventListener('change', onMqChange)
})

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') open.value = false
}

watch(open, (value) => {
  if (value) window.addEventListener('keydown', onKeydown)
  else window.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = value ? 'hidden' : ''
})

watch(
  () => route.fullPath,
  () => {
    open.value = false
  },
)

onBeforeUnmount(() => {
  mql.removeEventListener('change', onMqChange)
  window.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = ''
})
</script>

<template>
  <div v-if="isMobile">
    <div class="more-scrim" :class="{ 'is-open': open }" @click="open = false" />
    <nav class="bottom-nav" :class="{ 'is-open': open }" aria-label="底部导航">
      <router-link to="/" class="nav-tab" :class="{ 'is-active': isActive('/', true) }">
        <span class="nav-icon">
          <NavHomeLine class="icon-line" :size="21" />
          <NavHomeFill class="icon-fill" :size="21" />
        </span>
        <span class="nav-tab-label">首页</span>
      </router-link>
      <router-link to="/browse" class="nav-tab" :class="{ 'is-active': isActive('/browse') }">
        <span class="nav-icon">
          <NavBrowseLine class="icon-line" :size="21" />
          <NavBrowseFill class="icon-fill" :size="21" />
        </span>
        <span class="nav-tab-label">浏览</span>
      </router-link>
      <button
        type="button"
        class="nav-tab tab-more"
        :class="{ 'is-active': moreActive }"
        :aria-expanded="open"
        aria-haspopup="true"
        @click="open = !open"
      >
        <span class="nav-icon">
          <NavMoreLine class="icon-more" :size="21" />
          <NavCloseLine class="icon-close" :size="21" />
        </span>
        <span class="nav-tab-label">更多</span>
      </button>
      <router-link to="/shorts" class="nav-tab" :class="{ 'is-active': isActive('/shorts') }">
        <span class="nav-icon">
          <NavShortsLine class="icon-line" :size="21" />
          <NavShortsFill class="icon-fill" :size="21" />
        </span>
        <span class="nav-tab-label">短视频</span>
      </router-link>
      <router-link to="/settings" class="nav-tab" :class="{ 'is-active': isActive('/settings') }">
        <span class="nav-icon">
          <NavPersonLine class="icon-line" :size="21" />
          <NavPersonFill class="icon-fill" :size="21" />
        </span>
        <span class="nav-tab-label">我的</span>
      </router-link>
    </nav>
    <div class="more-menu" :class="{ 'is-open': open }" role="menu" aria-label="更多页面">
      <router-link
        v-for="item in moreItems"
        :key="item.to"
        :to="item.to"
        class="more-item"
        :class="{ 'is-active': isActive(item.to) }"
        role="menuitem"
        @click="open = false"
      >
        <component :is="item.icon" :size="24" />
        <span class="more-item-label">{{ item.label }}</span>
      </router-link>
    </div>
  </div>
</template>

<style scoped>
.more-scrim {
  position: fixed;
  inset: 0;
  z-index: 99;
  background: rgba(0, 0, 0, 0.3);
  opacity: 0;
  pointer-events: none;
}
.more-scrim.is-open {
  opacity: 1;
  pointer-events: auto;
}
.bottom-nav {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 100;
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  height: calc(64px + env(safe-area-inset-bottom, 0px));
  padding: 0 8px env(safe-area-inset-bottom, 0px);
  background: var(--sa-header-bg);
  border-top: 1px solid var(--sa-border-subtle);
  backdrop-filter: saturate(1.8) blur(12px);
  -webkit-backdrop-filter: saturate(1.8) blur(12px);
}
.nav-tab {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  border: none;
  background: none;
  padding: 0;
  color: var(--sa-text-secondary);
  font-size: 10.5px;
  line-height: 1;
  text-decoration: none;
  cursor: pointer;
}
.nav-tab-label {
  font-size: 10.5px;
  line-height: 1;
}
.nav-tab.is-active {
  color: var(--sa-text-primary);
}
.nav-tab.is-active .nav-tab-label {
  font-weight: 600;
}
.nav-icon {
  position: relative;
  width: 44px;
  height: 26px;
  border-radius: 13px;
}
.nav-icon svg {
  position: absolute;
  inset: 0;
  margin: auto;
}
.nav-icon .icon-fill {
  opacity: 0;
  transform: scale(0.5);
}
.nav-icon .icon-close {
  opacity: 0;
  transform: scale(0.5) rotate(-90deg);
}
.nav-tab.is-active .nav-icon {
  background: var(--sa-subtle);
}
.nav-tab.is-active .icon-line {
  opacity: 0;
  transform: scale(0.5);
}
.nav-tab.is-active .icon-fill {
  opacity: 1;
  transform: scale(1);
}
.bottom-nav.is-open .tab-more {
  color: var(--sa-text-primary);
}
.bottom-nav.is-open .tab-more .nav-icon {
  background: var(--sa-subtle);
}
.bottom-nav.is-open .tab-more .icon-more {
  opacity: 0;
  transform: scale(0.5) rotate(90deg);
}
.bottom-nav.is-open .tab-more .icon-close {
  opacity: 1;
  transform: scale(1) rotate(0deg);
}
.more-menu {
  position: fixed;
  left: 50%;
  bottom: calc(64px + env(safe-area-inset-bottom, 0px) + 10px);
  z-index: 101;
  width: 248px;
  padding: 8px;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 2px;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 16px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.18);
  transform: translateX(-50%) translateY(10px) scale(0.92);
  transform-origin: bottom center;
  visibility: hidden;
  opacity: 0;
  pointer-events: none;
}
.more-menu::after {
  content: '';
  position: absolute;
  bottom: -6px;
  left: 50%;
  width: 12px;
  height: 12px;
  transform: translateX(-50%) rotate(45deg);
  background: var(--sa-elevated);
  border-right: 1px solid var(--sa-border-subtle);
  border-bottom: 1px solid var(--sa-border-subtle);
}
.more-menu.is-open {
  transform: translateX(-50%) translateY(0) scale(1);
  visibility: visible;
  opacity: 1;
  pointer-events: auto;
}
.more-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  padding: 10px 2px 8px;
  border-radius: 10px;
  color: var(--sa-text-secondary);
  font-size: 11px;
  line-height: 1;
  text-decoration: none;
}
.more-item svg {
  color: inherit;
  transition: transform 0.18s ease;
}
.more-item:hover {
  background: var(--sa-subtle);
}
.more-item:hover svg {
  transform: translateY(-1px);
}
.more-item.is-active {
  background: var(--sa-subtle);
  color: var(--sa-text-primary);
}
.more-item.is-active svg {
  color: var(--sa-text-primary);
}
@media (prefers-reduced-motion: no-preference) {
  .more-scrim {
    transition: opacity 0.18s ease;
  }
  .more-menu {
    transition: transform 0.18s ease, opacity 0.18s ease, visibility 0.18s ease;
  }
  .nav-tab {
    transition: color 0.15s ease;
  }
  .nav-icon {
    transition: background-color 0.18s ease;
  }
  .nav-icon svg {
    transition: opacity 0.16s ease, transform 0.16s ease;
  }
}
</style>
