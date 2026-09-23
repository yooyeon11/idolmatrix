<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <div class="nf-page">
    <div class="nf-card">
      <div class="nf-code">404</div>
      <div class="nf-text">页面不存在或已被移除</div>
      <button class="nf-btn" @click="router.push('/')">返回首页</button>
    </div>
  </div>
</template>

<style scoped>
.nf-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--sa-bg);
  color: var(--sa-text-primary);
}
.nf-card {
  text-align: center;
}
.nf-code {
  font-size: 48px;
  font-weight: 700;
  color: var(--sa-accent);
  letter-spacing: -0.02em;
}
.nf-text {
  font-size: 14px;
  color: var(--sa-text-secondary);
  margin: 12px 0 24px;
}
.nf-btn {
  padding: 8px 18px;
  border-radius: 9999px;
  font-size: 13px;
  font-weight: 500;
  color: #fff;
  background: var(--sa-accent);
  border: none;
  cursor: pointer;
  transition: background 0.15s;
}
.nf-btn:hover {
  background: var(--sa-accent-hover);
}
</style>
