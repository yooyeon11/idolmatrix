<script setup lang="ts">
/**
 * 舞台刊头（Stage Hero）——组合/艺人详情页共用的通顶头图。
 *
 * 与 GroupDetailView 的 `.gp-hero` 完全同一套版式参数（负 margin 顶到视口上沿、
 * 三层遮罩、Playfair 900 大字、Instrument Serif 斜体拉丁名），抽成组件是为了
 * 两个详情页**只有一处可改**：以后调字号/遮罩只要动这里。
 *
 * 用法：页面里 `<SaHeader overlay />` + `<StageHero ... />`，
 * StageHero 自己吃掉 header 高度（负 margin），无需页面额外处理。
 */
const emit = defineEmits<{ (e: 'eyebrow-click'): void }>()

withDefaults(
  defineProps<{
    /** 背景图（横幅优先，回退头像） */
    image?: string | null
    /** 大字主名 */
    name: string
    /** 拉丁 / 英文副名：Instrument Serif 斜体紫色 */
    latin?: string | null
    /** 韩文名：紧跟大字后的小字 */
    korean?: string | null
    /** 左上角等宽小标（如 ARTIST / MEMBERS） */
    kicker?: string | null
    /** 主名下方的一行说明（组合名 · 出道年等） */
    eyebrow?: string | null
    /** 说明行是否可点击（点了进资料库编辑页） */
    eyebrowClickable?: boolean
    /** 一句话简介，最多 3 行 */
    description?: string | null
    /** 标签胶囊 */
    tags?: string[]
    /** 数字统计（作品 / 时长 / 直拍 …） */
    stats?: { num: string | number; label: string }[]
  }>(),
  {
    image: null,
    latin: null,
    korean: null,
    kicker: null,
    eyebrow: null,
    eyebrowClickable: false,
    description: null,
    tags: () => [],
    stats: () => [],
  },
)
</script>

<template>
  <section class="stage-hero">
    <div class="stage-hero-media" aria-hidden="true">
      <img v-if="image" class="stage-hero-img" :src="image" alt="" />
      <div v-else class="stage-hero-fallback" />
      <div class="stage-mask-radial" />
      <div class="stage-mask-linear" />
    </div>

    <span class="stage-m-brand" aria-hidden="true">IDOLMATRIX.</span>

    <div class="stage-hero-center">
      <!-- 与组合详情页一致：kicker / eyebrow 紧贴大字上方，不贴在顶栏 -->
      <div v-if="kicker || eyebrow || $slots.top" class="stage-hero-heading">
        <p v-if="kicker" class="stage-kicker">{{ kicker }}</p>
        <div v-if="eyebrow || $slots.top" class="stage-eyebrow">
          <button
            v-if="eyebrow && eyebrowClickable"
            class="stage-eyebrow-btn"
            type="button"
            title="进入资料库编辑"
            @click="emit('eyebrow-click')"
          >{{ eyebrow }}</button>
          <span v-else-if="eyebrow">{{ eyebrow }}</span>
          <slot name="top" />
        </div>
      </div>

      <h1 class="stage-name">
        {{ name }}
        <span v-if="latin" class="stage-name-latin">{{ latin }}</span>
        <span v-if="korean" class="stage-name-kr">{{ korean }}</span>
      </h1>

      <div v-if="tags.length" class="stage-tags">
        <span v-for="t in tags" :key="t" class="stage-tag">{{ t }}</span>
      </div>

      <p v-if="description" class="stage-desc">{{ description }}</p>

      <div v-if="$slots.actions" class="stage-actions">
        <slot name="actions" />
      </div>

      <!-- 移动端专属版式（业主 2026-11-13 设计稿）：桌面完全不渲染 -->
      <div v-if="$slots.mobile" class="stage-mobile-layer">
        <slot name="mobile" />
      </div>
    </div>

    <div v-if="stats.length || $slots.bottom" class="stage-hero-bottom">
      <div v-if="stats.length" class="stage-stats">
        <div v-for="s in stats" :key="s.label" class="stage-stat">
          <div class="stage-stat-num">{{ s.num }}</div>
          <div class="stage-stat-label">{{ s.label }}</div>
        </div>
      </div>
      <slot name="bottom" />
    </div>
  </section>
</template>

<style scoped>
.stage-eyebrow-btn {
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}
.stage-eyebrow-btn:hover {
  opacity: 0.8;
}
.stage-hero {
  --ink: #ffffff;
  --ink-2: rgba(255, 255, 255, 0.74);
  --ink-3: rgba(255, 255, 255, 0.5);
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: clamp(16px, 3.5vh, 36px);
  margin-top: calc(-1 * var(--sa-header-height, 77px));
  min-height: 100svh;
  padding: calc(var(--sa-header-height, 77px) + clamp(8px, 1.4vw, 16px))
    var(--sa-gutter, 36px) 40px;
  overflow: hidden;
  isolation: isolate;
  background: #08080b;
  color: var(--ink);
}
.stage-hero-media {
  position: absolute;
  inset: 0;
  z-index: 0;
}
.stage-hero-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center;
  filter: brightness(0.62);
  transform: scale(1.05);
}
.stage-hero-fallback {
  position: absolute;
  inset: 0;
  background: radial-gradient(circle at 60% 30%, #2a2340, #0b0b10 70%);
}
.stage-mask-radial {
  position: absolute;
  inset: 0;
  background: radial-gradient(
    circle at 65% 38%,
    rgba(139, 92, 246, 0.14) 0%,
    rgba(8, 8, 11, 0.65) 55%,
    #08080b 95%
  );
}
.stage-mask-linear {
  position: absolute;
  inset: 0;
  background: linear-gradient(to top, #08080b 0%, rgba(8, 8, 11, 0.3) 50%, rgba(0, 0, 0, 0.5) 100%);
}

.stage-hero-top,
.stage-hero-center,
.stage-hero-bottom {
  position: relative;
  z-index: 2;
}
.stage-hero-center {
  max-width: 920px;
  margin-block: auto;
  padding-block: clamp(8px, 2vh, 24px);
}
/* 大字上方的抬头块：kicker + eyebrow，整体紧贴名字 */
.stage-hero-heading {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: clamp(10px, 1.6vh, 18px);
}
.stage-hero-heading .stage-kicker {
  margin: 0;
}
.stage-kicker {
  margin: 0 0 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: #c4b5fd;
}
.stage-eyebrow {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: 11.5px;
  color: var(--ink-2);
}

.stage-name {
  margin: 0;
  font-family: 'Playfair Display', 'Noto Serif SC', 'Songti SC', 'SimSun', serif;
  font-size: clamp(52px, 9.4vw, 132px);
  line-height: 0.9;
  letter-spacing: -0.045em;
  font-weight: 900;
  color: var(--ink);
  text-shadow: 0 18px 60px rgba(0, 0, 0, 0.55);
}
.stage-name-latin {
  margin-left: clamp(8px, 1vw, 18px);
  font-family: 'Instrument Serif', 'Playfair Display', serif;
  font-style: italic;
  font-weight: 400;
  font-size: clamp(26px, 4.2vw, 58px);
  letter-spacing: 0;
  color: #c4b5fd;
  vertical-align: 0.06em;
  white-space: nowrap;
}
.stage-name-kr {
  margin-left: clamp(8px, 1vw, 16px);
  font-family: 'Noto Serif SC', 'Songti SC', 'SimSun', serif;
  font-size: clamp(17px, 2.2vw, 38px);
  font-weight: 400;
  letter-spacing: 0;
  color: rgba(255, 255, 255, 0.58);
  vertical-align: 0.12em;
  white-space: nowrap;
}

.stage-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 16px;
}
.stage-tag {
  padding: 4px 12px;
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  font-size: 11px;
  color: rgba(255, 255, 255, 0.86);
}
.stage-desc {
  max-width: 620px;
  margin: 16px 0 0;
  font-size: 13px;
  line-height: 1.75;
  color: rgba(255, 255, 255, 0.86);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.stage-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 20px;
}

.stage-hero-bottom {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}
.stage-stats {
  display: flex;
  flex-wrap: wrap;
  gap: clamp(20px, 3vw, 44px);
}
.stage-stat-num {
  font-family: 'Playfair Display', 'Noto Serif SC', serif;
  font-size: clamp(22px, 2.4vw, 34px);
  font-weight: 900;
  line-height: 1;
  letter-spacing: -0.02em;
}
.stage-stat-label {
  margin-top: 6px;
  font-size: 10.5px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ink-3);
}

.stage-mobile-layer,
.stage-m-brand {
  display: none;
}

@media (max-width: 768px) {
  .stage-m-brand {
    display: block;
    position: absolute;
    top: 20px;
    left: var(--sa-gutter, 16px);
    z-index: 3;
    font-size: 16px;
    font-weight: 900;
    letter-spacing: 1.5px;
    color: #fff;
  }
  .stage-hero {
    /* 设计稿：固定 520px 的刊头，不再是通屏 100svh */
    min-height: 520px;
    height: 520px;
    justify-content: flex-end;
    padding: 20px var(--sa-gutter, 16px) 20px;
    background: #000;
  }
  .stage-hero-img {
    filter: brightness(0.85);
    object-position: center top;
  }
  .stage-mask-radial {
    background: linear-gradient(
      180deg,
      rgba(0, 0, 0, 0.2) 0%,
      rgba(0, 0, 0, 0.05) 40%,
      rgba(0, 0, 0, 0.7) 70%,
      #000 100%
    );
  }
  .stage-mask-linear {
    display: none;
  }
  .stage-hero-center {
    margin-block: 0;
    padding-block: 0;
    max-width: none;
  }
  /* 桌面那套刊头在移动端全部让位给 #mobile 插槽 */
  .stage-hero-heading,
  .stage-name,
  .stage-tags,
  .stage-desc,
  .stage-actions,
  .stage-hero-bottom {
    display: none;
  }
  .stage-mobile-layer {
    display: block;
    width: 100%;
  }
}
</style>
