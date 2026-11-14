<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { SettingsOutlined } from '@/components/icons'
import BottomNav from '@/components/BottomNav.vue'
import ThemeToggle from '@/components/ThemeToggle.vue'
import { MIRROR_MODE } from '@/utils/mirror'

const props = defineProps<{
  /** 叠在整幅背景图上（组合详情页第一屏）：透明 + 白字，滚动后自动恢复实底 */
  overlay?: boolean
}>()

const router = useRouter()
const scrolled = ref(false)
function onScroll() {
  scrolled.value = window.scrollY > 8
}
onMounted(() => {
  onScroll()
  window.addEventListener('scroll', onScroll, { passive: true })
})
onBeforeUnmount(() => window.removeEventListener('scroll', onScroll))
const overlayOn = () => !!props.overlay && !scrolled.value

// 导航标签与首页顶栏完全一致（同一套入口、同一套顺序）
const navLinks = [
  { label: '浏览', to: '/browse' },
  { label: '组合', to: '/groups' },
  { label: '艺人', to: '/artists' },
  { label: '短视频', to: '/shorts' },
  { label: '歌曲', to: '/songs' },
  { label: '收藏', to: '/collections' },
  { label: '统计', to: '/stats' },
  { label: '博主', to: '/uploaders' },
]

function goSettings() {
  router.push('/settings')
}
</script>

<template>
  <header
    class="sa-header"
    :class="{ 'sa-header--overlay': overlayOn(), 'sa-header--scrolled': scrolled }"
  >
    <div class="header-inner">
      <!-- 品牌烙印：与首页封面左上角 IDOLMATRIX. 完全同一位置（top:32 left:36，22px 衬线刊头），路由切换不位移 -->
      <button class="sa-brand" type="button" aria-label="返回首页" @click="router.push('/')">
        IDOLMATRIX.
      </button>

      <!-- 导航：水平居中；外层只占位（pointer-events 穿透），内层 hug 内容供滑动动画定位 -->
      <nav class="sa-nav" aria-label="站内导航">
        <div class="sa-nav-group" data-sa-nav>
          <router-link
            v-for="link in navLinks"
            :key="link.to"
            class="sa-nav-link"
            :to="link.to"
            active-class="sa-nav-link--active"
          >
            {{ link.label }}
          </router-link>
        </div>
      </nav>

      <div class="sa-actions" data-sa-nav-actions>
        <ThemeToggle />
        <button v-if="!MIRROR_MODE" class="sa-icon-btn" type="button" aria-label="设置" title="设置" @click="goSettings">
          <SettingsOutlined :size="15" />
        </button>
      </div>
    </div>
  </header>

  <BottomNav />
</template>

<style scoped>
/* 高度 = 首页顶栏的分隔线位置（28 行顶 + 34 行高 + 14 内边距 + 1 边框 = 77），
   这样顶栏底边与首页 `.feed-nav` 的分隔线同一条线，切换页面时整条顶栏不位移。 */
.sa-header {
  position: sticky;
  top: 0;
  z-index: 50;
  height: var(--sa-header-height, 77px);
  /* 彻底版：常态完全透明，只有一层模糊。
     页面在顶部时顶栏与内容底色一致 → 根本看不出边界；
     内容滚到顶栏下方时被模糊掉，毛玻璃才「浮」出来。
     1px 透明边框只用于占位，几何仍是 77px，切页不位移。 */
  border-bottom: 1px solid transparent;
  background: transparent;
  backdrop-filter: saturate(1.8) blur(14px);
  -webkit-backdrop-filter: saturate(1.8) blur(14px);
  transition: background-color 0.25s ease;
}
/* 滚动后才给一层很薄的底色，保证文字压在海报上仍然可读 */
.sa-header--scrolled {
  background: var(--sa-header-scrolled-bg, rgba(255, 255, 255, 0.72));
}
.header-inner {
  position: relative;
  height: 100%;
}

/* 覆盖在整幅背景图上的状态：去底、去边框、去模糊，文字转白；
   滚动后（scrolled）自动退回普通实底状态，保证可读性 */
.sa-header--overlay {
  --sa-text-primary: #ffffff;
  --sa-text-secondary: rgba(255, 255, 255, 0.78);
  --sa-text-tertiary: rgba(255, 255, 255, 0.55);
  --sa-border-subtle: rgba(255, 255, 255, 0.14);
  --sa-subtle: rgba(255, 255, 255, 0.1);
  color: #ffffff;
  background: transparent;
  border-bottom-color: transparent;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}
/* 覆盖在通顶大图上时：不加底色、不加模糊，滑动后由 --scrolled 接管 */
.sa-header--overlay {
  background: transparent;
}

/* 品牌烙印：绝对定位锁定在首页同款坐标，任何页面/动画中都不动 */
.sa-brand {
  position: absolute;
  top: 32px;
  left: 36px;
  padding: 0;
  border: none;
  background: none;
  font-family: 'Playfair Display', 'Noto Serif SC', 'Songti SC', 'SimSun', serif;
  font-size: 22px;
  font-weight: 900;
  letter-spacing: -0.02em;
  line-height: 1.6;
  color: var(--sa-text-primary);
  white-space: nowrap;
  cursor: pointer;
  transition: opacity 0.15s ease;
}
.sa-brand:hover {
  opacity: 0.68;
}

/* 导航行：几何完全照搬首页 `.feed-nav`（行顶 28、行高 34 = 操作区 34px 高），
   链接靠 align-items:center 落在 35.4px —— 与首页链接的 y 逐像素一致，
   所以首页↔其他页切换时标签只在 x 方向滑动（动画见 composables/useNavFlip）。 */
.sa-nav {
  position: absolute;
  top: 28px;
  left: 0;
  right: 0;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}
.sa-nav-group {
  display: flex;
  align-items: center;
  gap: 22px;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.6;
  pointer-events: auto;
}
.sa-nav-link {
  color: var(--sa-text-secondary);
  text-decoration: none;
  white-space: nowrap;
  transition: color 0.15s ease;
}
.sa-nav-link:hover,
.sa-nav-link--active {
  color: var(--sa-text-primary);
}

/* 操作区：与首页 `.feed-nav-actions` 同坐标（行顶 28 / 右边距 44），高 34 由 ThemeToggle 决定 */
.sa-actions {
  position: absolute;
  top: 28px;
  right: 44px;
  height: 34px;
  display: flex;
  align-items: center;
  gap: 2px;
}
.sa-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 8px;
  background: none;
  color: var(--sa-text-secondary);
  cursor: pointer;
  transition:
    color 0.15s ease,
    background-color 0.15s ease;
}
.sa-icon-btn:hover {
  color: var(--sa-text-primary);
  background: var(--sa-subtle);
}

/* 移动端沿用底栏导航（与首页一致的全局底栏），顶栏隐藏避免与底栏重复 */
@media (max-width: 768px) {
  .sa-header {
    display: none;
  }
}
</style>
