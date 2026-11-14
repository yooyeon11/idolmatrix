/**
 * 在线版（只读镜像）构建开关。
 *
 * `VITE_MIRROR_MODE=1` 打包出来的前端会隐藏「设置」入口、拒绝进入设置路由，
 * 因为公网镜像站的设置项只给站长在本地看（路径、AI 配置、下载器等）。
 * 本地构建不带这个变量，行为完全不变。
 */
export const MIRROR_MODE = import.meta.env.VITE_MIRROR_MODE === '1'
