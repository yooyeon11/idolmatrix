<script setup lang="ts">
// 资料库分区标签栏：五个列表页 + 两个工作台共用，新增分区只改这里。
</script>

<template>
  <nav class="db-tabs" aria-label="资料库分区">
    <router-link to="/db" exact-active-class="on" class="db-tab">🩺 数据体检</router-link>
    <router-link to="/db/list" exact-active-class="on" class="db-tab">🗂 组合</router-link>
    <router-link to="/db/artists" exact-active-class="on" class="db-tab">🎤 艺人</router-link>
    <router-link to="/db/albums" exact-active-class="on" class="db-tab">💿 专辑</router-link>
    <router-link to="/db/songs" exact-active-class="on" class="db-tab">🎵 歌曲</router-link>
    <router-link to="/db/recycle" exact-active-class="on" class="db-tab">🗑 回收站</router-link>
  </nav>
</template>

<style scoped>
.db-tabs {
  display: flex;
  gap: 6px;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 12px;
  padding: 4px;
  width: fit-content;
  max-width: 100%;
  overflow-x: auto;
  scrollbar-width: none;
}
.db-tabs::-webkit-scrollbar {
  display: none;
}
.db-tab {
  padding: 8px 20px;
  border-radius: 9px;
  font-size: 13px;
  font-weight: 600;
  color: var(--sa-text-secondary);
  text-decoration: none;
  white-space: nowrap;
}
.db-tab.on {
  background: var(--sa-text-primary);
  color: var(--sa-bg);
}
</style>
