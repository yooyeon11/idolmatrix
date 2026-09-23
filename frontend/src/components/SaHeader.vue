<script setup lang="ts">
import { useRouter } from 'vue-router'
import { SettingsOutlined, StarOutlined } from '@/components/icons'
import BottomNav from '@/components/BottomNav.vue'
import ThemeToggle from '@/components/ThemeToggle.vue'

const router = useRouter()

function onLogoClick() {
  router.push('/')
}

function goSettings() {
  router.push('/settings')
}
</script>

<template>
  <header class="sa-header">
    <div class="sa-container header-inner">
      <div class="sa-brand">
        <button class="sa-logo" aria-label="打开导航" @click="onLogoClick">
          <StarOutlined class="logo-dot" :size="16" />
        </button>
        <button class="sa-logo-text" aria-label="返回首页" @click="router.push('/')">
          idolMatrix
        </button>
      </div>

      <nav class="sa-nav">
        <router-link
          to="/browse"
          class="sa-nav-link"
          active-class="sa-nav-link--active"
        >
          浏览
        </router-link>
        <router-link
          to="/groups"
          class="sa-nav-link"
          active-class="sa-nav-link--active"
        >
          组合
        </router-link>
        <router-link
          to="/artists"
          class="sa-nav-link"
          active-class="sa-nav-link--active"
        >
          艺人
        </router-link>
        <router-link
          to="/shorts"
          class="sa-nav-link"
          active-class="sa-nav-link--active"
        >
          短视频
        </router-link>
        <router-link
          to="/songs"
          class="sa-nav-link"
          active-class="sa-nav-link--active"
        >
          歌曲
        </router-link>
        <router-link
          to="/collections"
          class="sa-nav-link"
          active-class="sa-nav-link--active"
        >
          收藏
        </router-link>
        <router-link
          to="/stats"
          class="sa-nav-link"
          active-class="sa-nav-link--active"
        >
          统计
        </router-link>
        <router-link
          to="/uploaders"
          class="sa-nav-link"
          active-class="sa-nav-link--active"
        >
          博主
        </router-link>
      </nav>

      <slot name="back" />

      <div class="sa-actions">
        <ThemeToggle />
        <button class="sa-new-btn" aria-label="设置" title="设置" @click="goSettings">
          <SettingsOutlined :size="16" />
        </button>
      </div>
    </div>
  </header>

  <BottomNav />
</template>

<style scoped>
.sa-header {
  position: sticky;
  top: 0;
  z-index: 50;
  height: 48px;
  border-bottom: 1px solid var(--sa-border-subtle);
  background: var(--sa-header-bg);
  backdrop-filter: saturate(1.8) blur(12px);
  -webkit-backdrop-filter: saturate(1.8) blur(12px);
}
.sa-container {
  width: 100%;
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 32px;
}
@media (max-width: 768px) {
  .sa-container {
    padding: 0 16px;
  }
}
.header-inner {
  display: flex;
  align-items: center;
  height: 100%;
  gap: 24px;
}
.sa-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.sa-logo {
  display: flex;
  align-items: center;
  background: none;
  border: none;
  color: var(--sa-text-primary);
  cursor: pointer;
  flex-shrink: 0;
  padding: 0;
}
.sa-logo-text {
  background: none;
  border: none;
  color: var(--sa-text-primary);
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.02em;
  cursor: pointer;
  padding: 0;
}
.logo-dot {
  display: inline-flex;
  color: var(--sa-accent);
}
.sa-nav {
  display: flex;
  align-items: center;
  gap: 2px;
  height: 100%;
  margin: 0 auto;
}
.sa-nav-link {
  display: inline-flex;
  align-items: center;
  padding: 0 12px;
  height: 100%;
  font-size: 14px;
  font-weight: 500;
  color: var(--sa-text-secondary);
  text-decoration: none;
  position: relative;
  transition: color 0.15s;
}
.sa-nav-link:hover {
  color: var(--sa-text-primary);
}
.sa-nav-link--active {
  color: var(--sa-text-primary);
}
.sa-nav-link--active::after {
  content: '';
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 0;
  height: 2px;
  border-radius: 2px 2px 0 0;
  background: var(--sa-text-primary);
}
.sa-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.sa-new-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: none;
  background: none;
  color: var(--sa-text-secondary);
  cursor: pointer;
  transition: color 0.15s;
}
.sa-new-btn:hover {
  color: var(--sa-accent);
}

@media (max-width: 768px) {
  .sa-header {
    display: none;
  }
}
</style>
