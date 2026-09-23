// 本地图标：用内联 SVG 实现，避免引入额外图标依赖。
// 路径取自 Material Design Icons (Apache 2.0)。
import { h, type VNode } from 'vue'

interface IconProps {
  size?: number | string
  color?: string
}

function makeIcon(path: string, pathAttrs: Record<string, unknown> = {}): (props?: IconProps) => VNode {
  return (props: IconProps = {}) => {
    const size = props.size ?? '1em'
    const color = props.color ?? 'currentColor'
    return h(
      'svg',
      {
        xmlns: 'http://www.w3.org/2000/svg',
        viewBox: '0 0 24 24',
        width: size,
        height: size,
        fill: color,
        'aria-hidden': true,
      },
      [h('path', { d: path, ...pathAttrs })],
    )
  }
}

interface ShapeSpec {
  tag: string
  attrs?: Record<string, unknown>
}

function makeStrokeIcon(shapes: ShapeSpec[]): (props?: IconProps) => VNode {
  return (props: IconProps = {}) => {
    const size = props.size ?? '1em'
    const color = props.color ?? 'currentColor'
    return h(
      'svg',
      {
        xmlns: 'http://www.w3.org/2000/svg',
        viewBox: '0 0 24 24',
        width: size,
        height: size,
        fill: 'none',
        stroke: color,
        'stroke-width': 1.8,
        'stroke-linecap': 'round',
        'stroke-linejoin': 'round',
        'aria-hidden': true,
      },
      shapes.map((s) => h(s.tag, s.attrs)),
    )
  }
}

// 仪表盘（四格）
export const DashboardOutlined = makeIcon(
  'M3 3h8v8H3V3zm10 0h8v8h-8V3zM3 13h8v8H3v-8zm10 0h8v8h-8v-8z',
)
// 待整理（收件箱）
export const InboxOutlined = makeIcon(
  'M19 3H4.99c-1.11 0-1.98.89-1.98 2L3 19c0 1.1.88 2 1.99 2H19c1.1 0 2-.9 2-2V5c0-1.11-.9-2-2-2zm0 12h-4c0 1.66-1.35 3-3 3s-3-1.34-3-3H4.99V5H19v10z',
)
// 视频库
export const VideoLibraryOutlined = makeIcon(
  'M4 6H2v14c0 1.1.9 2 2 2h14v-2H4V6zm16-4H8c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-6 11V5l5.5 4-5.5 4z',
)
// 组合（多人）
export const GroupOutlined = makeIcon(
  'M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z',
)
// 艺人（单人）
export const PersonOutlined = makeIcon(
  'M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z',
)
// 歌曲（音符）
export const MusicNoteOutlined = makeIcon(
  'M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z',
)
// 设置（齿轮）
export const SettingsOutlined = makeIcon(
  'M19.14 12.94c.04-.31.06-.62.06-.94 0-.32-.02-.63-.06-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.07.47.12.61l2.03 1.58c-.04.31-.06.62-.06.94 0 .32.02.63.06.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.21.06-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z',
)
// 电影（胶片）
export const MovieOutlined = makeIcon(
  'M4 3h16a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zm2 2v2h2V5H6zm0 4v2h2V9H6zm0 4v2h2v-2H6zm0 4v2h2v-2H6zm12-12v14H10V5h8zm-2 2v4h-4V7h4zm0 6v4h-4v-4h4z',
)
// 搜索
export const SearchOutlined = makeIcon(
  'M15.5 14h-.79l-.28-.27a6.5 6.5 0 1 0-.7.7l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0A4.5 4.5 0 1 1 14 9.5 4.5 4.5 0 0 1 9.5 14z',
)
// 卡片视图（九宫格）
export const AppOutlined = makeIcon(
  'M3 3v8h8V3H3zm6 6H5V5h4v4zm-6 4v8h8v-8H3zm6 6H5v-4h4v4zm4-16v8h8V3h-8zm6 6h-4V5h4v4zm-6 4v8h8v-8h-8zm6 6h-4v-4h4v4z',
)
// 列表视图
export const ListOutlined = makeIcon(
  'M3 5h18v2H3V5zm0 6h18v2H3v-2zm0 6h18v2H3v-2z',
)
export const BarChartOutlined = makeIcon(
  'M5 9.2h3V19H5V9.2zM10.6 5h2.8v14h-2.8V5zm5.6 8H19v6h-2.8v-6z',
)
// 播放
export const PlayArrowOutlined = makeIcon(
  'M8 5v14l11-7L8 5z',
)
// 外部链接
export const OpenInNewOutlined = makeIcon(
  'M19 19H5V5h7V3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7h-2v7zM14 3v2h3.59l-9.83 9.83 1.41 1.41L19 6.41V10h2V3h-7z',
)
// 刷新
export const ShuffleOutlined = makeIcon(
  'M10.59 9.17 5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4h-5.5zm.33 9.41-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04-3.13-3.13z',
)
export const RefreshOutlined = makeIcon(
  'M17.65 6.35A7.95 7.95 0 0 0 12 4a8 8 0 1 0 7.73 10h-2.08A6 6 0 1 1 12 6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z',
)
// 加号
// 加载态：配合 CSS 自转动画使用（见 settings-shared.css 的 .sa-btn--icon.is-busy）
export const LoadingOutlined = makeIcon(
  'M12 6v3l4-4-4-4v3c-4.42 0-8 3.58-8 8 0 1.57.46 3.03 1.24 4.26L6.7 14.8c-.45-.83-.7-1.79-.7-2.8 0-3.31 2.69-6 6-6zm6.76 1.74L17.3 9.2c.44.84.7 1.79.7 2.8 0 3.31-2.69 6-6 6v-3l-4 4 4 4v-3c4.42 0 8-3.58 8-8 0-1.57-.46-3.03-1.24-4.26z',
)

export const AddOutlined = makeIcon('M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z')
// 对勾
export const CheckOutlined = makeIcon(
  'M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z',
)
// 编辑
export const EditOutlined = makeIcon(
  'M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34a.996.996 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z',
)
// 删除
export const DeleteOutlined = makeIcon(
  'M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z',
)
// 文件夹
export const FolderOutlined = makeIcon(
  'M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z',
)
// 图片
export const ImageOutlined = makeIcon(
  'M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z',
)
// 收藏（书签）
export const BookmarkOutlined = makeIcon(
  'M17 3H7c-1.1 0-2 .9-2 2v16l7-3 7 3V5c0-1.1-.9-2-2-2zm0 15-5-2.18L7 18V5h10v13z',
)
export const BookmarkFilled = makeIcon(
  'M17 3H7c-1.1 0-2 .9-2 2v16l7-3 7 3V5c0-1.1-.9-2-2-2z',
)
// 关闭（X）
export const CloseOutlined = makeIcon('M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z')
// 警告
export const WarningAmberOutlined = makeIcon(
  'M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z',
)
// 首页（房子）
export const HomeOutlined = makeIcon(
  'M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z',
)
// 返回（左箭头）
export const ArrowLeftOutlined = makeIcon(
  'M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z',
)
// 向下（下拉箭头）
export const DownOutlined = makeIcon(
  'M7.41 8.59 12 13.17l4.59-4.58L18 10l-6 6-6-6 1.41-1.41z',
)
// 排序（长短横线 + 下箭头）：筛选工具栏图标
export const SortOutlined = makeStrokeIcon([
  { tag: 'path', attrs: { d: 'M4 6.6h11' } },
  { tag: 'path', attrs: { d: 'M4 12h7' } },
  { tag: 'path', attrs: { d: 'M4 17.4h4' } },
  { tag: 'path', attrs: { d: 'M17.5 9.6v8.6' } },
  { tag: 'path', attrs: { d: 'M14.6 15.3 17.5 18.2 20.4 15.3' } },
])
// 分辨率（显示器）：筛选工具栏图标
export const ResolutionOutlined = makeStrokeIcon([
  { tag: 'rect', attrs: { x: 2.8, y: 4.4, width: 18.4, height: 12.4, rx: 2.2 } },
  { tag: 'path', attrs: { d: 'M9.6 20h4.8' } },
  { tag: 'path', attrs: { d: 'M12 16.8V20' } },
])
// 向右（列表行进入指示）
export const ChevronLeftOutlined = makeIcon(
  'M15.41 7.41 14 6l-6 6 6 6 1.41-1.41L10.83 12z',
)
export const ChevronRightOutlined = makeIcon(
  'M10 6 8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z',
)
// 链接（复制链接）
export const LinkOutlined = makeIcon(
  'M3.9 12c0-1.71 1.39-3.1 3.1-3.1h4V7H7c-2.76 0-5 2.24-5 5s2.24 5 5 5h4v-1.9H7c-1.71 0-3.1-1.39-3.1-3.1zM8 13h8v-2H8v2zm9-6h-4v1.9h4c1.71 0 3.1 1.39 3.1 3.1s-1.39 3.1-3.1 3.1h-4V17h4c2.76 0 5-2.24 5-5s-2.24-5-5-5z',
)
// AI（机器人）
export const RobotOutlined = makeIcon(
  'M12 2a2 2 0 0 1 2 2c0 .74-.4 1.39-1 1.73V7h1a7 7 0 0 1 7 7h1a1 1 0 0 1 1 1v3a1 1 0 0 1-1 1h-1v1a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-1H2a1 1 0 0 1-1-1v-3a1 1 0 0 1 1-1h1a7 7 0 0 1 7-7h1V5.73c-.6-.34-1-.99-1-1.73a2 2 0 0 1 2-2zM7.5 13A2.5 2.5 0 0 0 5 15.5 2.5 2.5 0 0 0 7.5 18a2.5 2.5 0 0 0 2.5-2.5A2.5 2.5 0 0 0 7.5 13zm9 0a2.5 2.5 0 0 0-2.5 2.5 2.5 2.5 0 0 0 2.5 2.5 2.5 2.5 0 0 0 2.5-2.5 2.5 2.5 0 0 0-2.5-2.5z',
)
// 信息（圆环 i）
export const InfoOutlined = makeIcon(
  'M11 7h2v2h-2V7zm0 4h2v6h-2v-6zm1-9C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8z',
)
// 数据库（圆柱）
export const DatabaseOutlined = makeIcon(
  'M12 3C7.58 3 4 4.79 4 7s3.58 4 8 4 8-1.79 8-4-3.58-4-8-4zM4 9.13V12c0 2.21 3.58 4 8 4s8-1.79 8-4V9.13C18.41 10.32 15.36 11 12 11S5.59 10.32 4 9.13zM4 14.13V17c0 2.21 3.58 4 8 4s8-1.79 8-4v-2.87C18.41 15.32 15.36 16 12 16s-5.59-.68-8-1.87z',
)
// 专辑（唱片）
export const AlbumOutlined = makeIcon(
  'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm0-12.5c-1.93 0-3.5 1.57-3.5 3.5s1.57 3.5 3.5 3.5 3.5-1.57 3.5-3.5-1.57-3.5-3.5-3.5z',
)
// 太阳（浅色主题）
export const SunOutlined = makeIcon(
  'M12 7c-2.76 0-5 2.24-5 5s2.24 5 5 5 5-2.24 5-5-2.24-5-5-5zM2 13h2c.55 0 1-.45 1-1s-.45-1-1-1H2c-.55 0-1 .45-1 1s.45 1 1 1zm18 0h2c.55 0 1-.45 1-1s-.45-1-1-1h-2c-.55 0-1 .45-1 1s.45 1 1 1zM11 2v2c0 .55.45 1 1 1s1-.45 1-1V2c0-.55-.45-1-1-1s-1 .45-1 1zm0 18v2c0 .55.45 1 1 1s1-.45 1-1v-2c0-.55-.45-1-1-1s-1 .45-1 1zM5.99 4.58a.996.996 0 0 0-1.41 0 .996.996 0 0 0 0 1.41l1.06 1.06c.39.39 1.03.39 1.41 0s.39-1.03 0-1.41L5.99 4.58zm12.37 12.37a.996.996 0 0 0-1.41 0 .996.996 0 0 0 0 1.41l1.06 1.06c.39.39 1.03.39 1.41 0a.996.996 0 0 0 0-1.41l-1.06-1.06zm1.06-10.96a.996.996 0 0 0 0-1.41.996.996 0 0 0-1.41 0l-1.06 1.06c-.39.39-.39 1.03 0 1.41s1.03.39 1.41 0l1.06-1.06zM7.05 18.36a.996.996 0 0 0 0-1.41.996.996 0 0 0-1.41 0l-1.06 1.06c-.39.39-.39 1.03 0 1.41s1.03.39 1.41 0l1.06-1.06z',
)
// 月亮（深色主题）
export const MoonOutlined = makeIcon(
  'M9.37 5.51A7.35 7.35 0 0 0 9.1 8.1c0 4.08 3.32 7.4 7.4 7.4.68 0 1.35-.09 1.99-.27A7.014 7.014 0 0 1 12 19c-3.87 0-7-3.13-7-7 0-2.91 1.78-5.41 4.37-6.49zM12 3a9 9 0 1 0 9 9c0-.46-.04-.92-.1-1.36a5.389 5.389 0 0 1-4.4 2.26 5.403 5.403 0 0 1-3.14-9.8c-.44-.06-.9-.1-1.36-.1z',
)
// 星星（Logo 图标）
export const StarOutlined = makeIcon(
  'M12 17.27 18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z',
)
// 上传人（频道/订阅）
export const UploadOutlined = makeIcon(
  'M20 8H4V6h16v2zm-2-6H6v2h12V2zm4 10v8c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2v-8c0-1.1.9-2 2-2h16c1.1 0 2 .9 2 2zm-6 4-6-3.27v6.53L16 16z',
)

// 锁（字段锁定）：实心=已锁，开口=未锁
export const LockOutlined = makeIcon(
  'M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z',
)
export const LockOpenOutlined = makeIcon(
  'M12 17c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm6-9h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6h1.9c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2z',
)

// 底部导航双态图标：未激活细线轮廓，激活实心
export const NavHomeLine = makeStrokeIcon([
  { tag: 'path', attrs: { d: 'M4 10.8 12 4l8 6.8V20h-5.5v-5h-5v5H4z' } },
])
export const NavHomeFill = makeIcon('M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z')
export const NavBrowseLine = makeStrokeIcon([
  { tag: 'rect', attrs: { x: 3, y: 5.5, width: 18, height: 13.5, rx: 2.5 } },
  { tag: 'path', attrs: { d: 'M10.6 9.4v5.2l4.4-2.6z' } },
])
export const NavBrowseFill = makeIcon(
  'M5.5 5.5h13A2.5 2.5 0 0 1 21 8v9a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17V8a2.5 2.5 0 0 1 2.5-2.5zM10.5 9.4v5.2l4.5-2.6z',
  { 'fill-rule': 'evenodd' },
)
export const NavMoreLine = makeStrokeIcon([
  { tag: 'rect', attrs: { x: 3.8, y: 3.8, width: 6.9, height: 6.9, rx: 1.8 } },
  { tag: 'rect', attrs: { x: 13.3, y: 3.8, width: 6.9, height: 6.9, rx: 1.8 } },
  { tag: 'rect', attrs: { x: 3.8, y: 13.3, width: 6.9, height: 6.9, rx: 1.8 } },
  { tag: 'rect', attrs: { x: 13.3, y: 13.3, width: 6.9, height: 6.9, rx: 1.8 } },
])
export const NavShortsLine = makeStrokeIcon([
  { tag: 'rect', attrs: { x: 3, y: 4.5, width: 18, height: 15, rx: 2.5 } },
  { tag: 'path', attrs: { d: 'M7.4 6.2v11.6' } },
  { tag: 'path', attrs: { d: 'M11.5 9.7v4.6l4.1-2.3z' } },
])
export const NavShortsFill = makeIcon(
  'M5.5 4.5h13A2.5 2.5 0 0 1 21 7v10a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17V7a2.5 2.5 0 0 1 2.5-2.5zM11.2 9.5v5l4.4-2.5z',
  { 'fill-rule': 'evenodd' },
)
export const NavPersonLine = makeStrokeIcon([
  { tag: 'circle', attrs: { cx: 12, cy: 7.8, r: 3.4 } },
  { tag: 'path', attrs: { d: 'M4.8 19.6c.7-3.4 3.7-5.1 7.2-5.1s6.5 1.7 7.2 5.1' } },
])
export const NavPersonFill = makeIcon(
  'M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z',
)
export const NavCloseLine = makeStrokeIcon([
  { tag: 'path', attrs: { d: 'M6.5 6.5l11 11' } },
  { tag: 'path', attrs: { d: 'M17.5 6.5l-11 11' } },
])

// 日历（照片工具栏「日期筛选」图标按钮）
export const CalendarOutlined = makeIcon(
  'M19 4h-1V2h-2v2H8V2H6v2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2zm0 16H5V10h14v10zm0-12H5V6h14v2z',
)

// 看图器：下载 / 沉浸模式（全屏）双态
export const DownloadOutlined = makeIcon(
  'M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z',
)
export const FullscreenOutlined = makeIcon(
  'M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z',
)
export const FullscreenExitOutlined = makeIcon(
  'M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z',
)

// 刊头社交：单色品牌剪影，24×24
export const InstagramIcon = makeIcon(
  'M7.8 2h8.4C19.4 2 22 4.6 22 7.8v8.4c0 3.2-2.6 5.8-5.8 5.8H7.8C4.6 22 2 19.4 2 16.2V7.8C2 4.6 4.6 2 7.8 2m-.2 2C5.61 4 4 5.61 4 7.6v8.8C4 18.39 5.61 20 7.6 20h8.8c1.99 0 3.6-1.61 3.6-3.6V7.6C20 5.61 18.39 4 16.4 4H7.6m9.65 1.5A1.25 1.25 0 1 1 16 6.75a1.25 1.25 0 0 1 1.25-1.25M12 7a5 5 0 1 1 0 10 5 5 0 0 1 0-10m0 2a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
)
export const YoutubeIcon = makeIcon(
  'M10 15.5v-7L16 12l-6 3.5M21.56 7.17c.13.47.22 1.1.28 1.9.07.8.1 1.49.1 2.09L22 12c0 2.19-.16 3.8-.44 4.83-.25.9-.83 1.48-1.73 1.73-.47.13-1.33.22-2.65.28-1.3.07-2.49.1-3.59.1L12 19c-4.19 0-6.8-.16-7.83-.44-.9-.25-1.48-.83-1.73-1.73-.13-.47-.22-1.1-.28-1.9C2.09 14.13 2.06 13.44 2.06 12.84L2 12c0-2.19.16-3.8.44-4.83.25-.9.83-1.48 1.73-1.73.47-.13 1.33-.22 2.65-.28C7.12 5.09 8.31 5.06 9.41 5.06L12 5c4.19 0 6.8.16 7.83.44.9.25 1.48.83 1.73 1.73z',
)
export const TiktokIcon = makeIcon(
  'M19.59 6.69a4.83 4.83 0 0 1-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 0 1-5.69.38 2.89 2.89 0 0 1 2.88-2.88c.28 0 .56.04.81.13v-3.4a6.34 6.34 0 0 0-6.22 10.4A6.34 6.34 0 0 0 18.72 16.7V8.73a8.18 8.18 0 0 0 4.78 1.52V6.79a4.89 4.89 0 0 1-3.91-.1z',
)
export const XIcon = makeIcon(
  'M18.24 2.25h3.31l-7.23 8.26 8.51 11.24h-6.67l-4.71-6.23-5.4 6.23H2.74l7.73-8.83L2.14 2.25h7.56l4.26 5.69 4.28-5.69zm-1.16 17.52h1.83L7.08 3.66H5.12l11.96 16.11z',
)
export const GlobeIcon = makeStrokeIcon([
  { tag: 'circle', attrs: { cx: 12, cy: 12, r: 9 } },
  { tag: 'ellipse', attrs: { cx: 12, cy: 12, rx: 4, ry: 9 } },
  { tag: 'path', attrs: { d: 'M3 12h18' } },
])
export const BilibiliIcon = makeIcon(
  'M5.5 6.5 3.8 4.4h2.2L8.3 6.5h7.4l2.3-2.1h2.2l-1.7 2.1H20a2 2 0 0 1 2 2v9.2a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8.5a2 2 0 0 1 2-2h1.5zM8.2 11.2v4.4l5.4-2.2-5.4-2.2z',
)
export const WeiboIcon = makeIcon(
  'M10.2 8.6c3.3-.2 6.6 2.2 7.4 5.4.7 2.7-.7 5.4-3.2 6.6-3.2 1.5-7.2.3-8.8-2.7-1.5-2.8-.4-6.4 2.6-8 0 0-1.3.6-1.8 2.1 2.1-2.3 5.6-2.3 7.3.1-1.8-1.6-4.5-1.2-5.8.9-1.6 2.5-.4 5.3 2.6 5.7 2.6.3 4.9-1.9 4.6-4.3-.3-2.1-2.4-3.6-4.9-3.8zm8.5-1.8c.9 1.6.7 3.5-.4 4.9l-1.1-.7c.8-1 .9-2.3.3-3.4-.6-1.1-1.8-1.7-3-1.6l.1-1.3c1.8-.1 3.5.8 4.1 2.1zM16.4 3.2c1.5.1 2.9.8 3.9 1.9 1.1 1.2 1.6 2.8 1.5 4.4l-1.3-.2c.1-1.2-.3-2.4-1.1-3.3-.8-.9-1.9-1.4-3.1-1.5l.1-1.3z',
)
export const FacebookIcon = makeIcon(
  'M22 12.07C22 6.5 17.52 2 12 2S2 6.5 2 12.07C2 17.1 5.66 21.24 10.44 22v-7.02H7.9v-2.91h2.54V9.84c0-2.5 1.49-3.89 3.78-3.89 1.09 0 2.24.2 2.24.2v2.47h-1.26c-1.24 0-1.63.77-1.63 1.56v1.87h2.78l-.44 2.91h-2.34V22C18.34 21.24 22 17.1 22 12.07z',
)
