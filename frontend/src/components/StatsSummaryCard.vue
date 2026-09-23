<script setup lang="ts">
import { ref } from 'vue'

/**
 * 年度报告终章「封底卡」：年份 + 主角（组合 + 艺人双主角或单个）+ 三个核心数字。
 * 居中排版，适合截图保存。
 */
const props = defineProps<{
  year: string
  /** Top1 组合名 */
  topName: string
  /** 主角色标签，默认「年度组合」 */
  topLabel?: string
  /** Top1 头像地址（空或加载失败时回退首字母） */
  avatarUrl?: string
  /** 年度艺人（可选，双主角并排展示） */
  secondName?: string
  secondAvatarUrl?: string
  /** 三个核心数字 */
  stats: { label: string; value: string }[]
}>()

const avatarBroken = ref(false)
const secondBroken = ref(false)

const hasSecond = () => !!props.secondName
</script>

<template>
  <div class="summary-card">
    <div class="sc-kicker">年度影像报告</div>
    <div class="sc-year">{{ year }}</div>
    <p class="sc-line">这一年，属于</p>
    <div class="sc-top" :class="{ 'sc-top--dual': hasSecond() }">
      <div class="sc-figure">
        <span class="sc-avatar">
          <img
            v-if="avatarUrl && !avatarBroken"
            :src="avatarUrl"
            alt=""
            @error="avatarBroken = true"
          />
          <span v-else class="sc-initial">{{ (topName || '?').trim().charAt(0).toUpperCase() }}</span>
        </span>
        <b class="sc-name">{{ topName }}</b>
        <span class="sc-role">{{ topLabel || '年度组合' }}</span>
      </div>
      <div v-if="hasSecond()" class="sc-figure">
        <span class="sc-avatar">
          <img
            v-if="secondAvatarUrl && !secondBroken"
            :src="secondAvatarUrl"
            alt=""
            @error="secondBroken = true"
          />
          <span v-else class="sc-initial">{{ (secondName || '?').trim().charAt(0).toUpperCase() }}</span>
        </span>
        <b class="sc-name">{{ secondName }}</b>
        <span class="sc-role">年度艺人</span>
      </div>
    </div>
    <div class="sc-stats">
      <div v-for="s in props.stats" :key="s.label" class="sc-stat">
        <b>{{ s.value }}</b>
        <span>{{ s.label }}</span>
      </div>
    </div>
    <p class="sc-foot">每一个数字，都是你亲手存下的</p>
  </div>
</template>

<style scoped>
.summary-card {
  max-width: 560px;
  margin: 0 auto;
  padding: 44px 40px 32px;
  border-radius: 24px;
  text-align: center;
  background:
    radial-gradient(420px 200px at 50% 0%, color-mix(in srgb, var(--sa-accent) 8%, transparent), transparent 70%),
    var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  box-shadow: 0 12px 40px rgba(15, 23, 42, 0.06);
}
.sc-kicker {
  font-size: 11px;
  letter-spacing: 0.32em;
  text-transform: uppercase;
  color: var(--sa-accent);
  font-weight: 700;
}
.sc-year {
  margin-top: 10px;
  font-size: clamp(3.4rem, 8vw, 5rem);
  font-weight: 800;
  line-height: 1;
  letter-spacing: -0.05em;
  font-variant-numeric: tabular-nums;
  color: var(--sa-text-primary);
}
.sc-line {
  margin: 14px 0 0;
  font-size: 13px;
  color: var(--sa-text-tertiary);
}
.sc-top {
  margin-top: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
}
/* 双主角：组合 + 艺人并排 */
.sc-top--dual {
  gap: clamp(28px, 10vw, 72px);
}
.sc-figure {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.sc-figure .sc-name {
  font-size: clamp(1.1rem, 2.6vw, 1.4rem);
}
.sc-role {
  font-size: 11px;
  color: var(--sa-text-tertiary);
}
.sc-avatar {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  overflow: hidden;
  background: var(--sa-subtle);
  display: grid;
  place-items: center;
  border: 2px solid var(--sa-accent-border);
  flex: none;
}
.sc-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.sc-initial {
  font-size: 1.4rem;
  font-weight: 800;
  color: var(--sa-accent);
}
.sc-name {
  font-size: clamp(1.3rem, 3vw, 1.7rem);
  font-weight: 800;
  letter-spacing: -0.02em;
  overflow-wrap: anywhere;
}
.sc-stats {
  margin-top: 26px;
  padding-top: 22px;
  border-top: 1px dashed var(--sa-border-subtle);
  display: flex;
  justify-content: center;
  gap: clamp(20px, 6vw, 56px);
}
.sc-stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.sc-stat b {
  font-size: 1.5rem;
  font-weight: 800;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}
.sc-stat span {
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.sc-foot {
  margin: 22px 0 0;
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
@media (max-width: 768px) {
  .summary-card {
    padding: 32px 20px 24px;
    border-radius: 18px;
  }
  .sc-stats {
    gap: 20px;
  }
}
</style>
