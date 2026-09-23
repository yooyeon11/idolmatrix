/**
 * xgplayer-hls 3.0.26 运行时补丁（幂等，build 前自动应用）。
 *
 * ⚠ 已废弃（v3.5.0 起）：HLS 内核已切换为 xgplayer-hls.js（hls.js），
 * xgplayer-hls 不再是依赖；hls.js 原生解析 master CODECS 四码，本补丁无用武之地。
 * 保留脚本仅为「依赖回退 xgplayer-hls 时」可复用：若包不存在则优雅跳过。
 *
 * 原问题：其 master playlist CODECS 分类正则表里视频侧是 `/^av1$/` —— 只认裸
 * "av1"，认不出标准四码 `av01.0.13.08`。于是 AV1 源即使清单带 CODECS 也解析
 * 不出 videoCodec，建 SourceBuffer 兜底 avc1.42e01e，AV1 fMP4 分片被 Chrome
 * 拒收（CHUNK_DEMUXER_ERROR_APPEND_FAILED）。
 * 补丁：`/^av1$/` → `/^av01/`，让 av01.P.LL(T).DD 四码被识别并原样传给
 * `MediaSource.addSourceBuffer("video/mp4;codecs=av01...")`。
 * vp09 本来就被 `/^vp0?[89]/` 覆盖，无需改。
 *
 * 后端配套：playback_manager 给 hls_fmp4 直出会话包一层带 CODECS 的 master
 * playlist（媒体清单里写 CODECS 无效）。
 */
import { readFileSync, writeFileSync, existsSync } from 'node:fs'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)

let pkgPath
try {
  pkgPath = require.resolve('xgplayer-hls/package.json')
} catch {
  console.log('[patch-xgplayer-hls] 跳过：xgplayer-hls 不在依赖中（HLS 内核已切换为 xgplayer-hls.js / hls.js）')
  process.exit(0)
}
const path = pkgPath.replace(/package\.json$/, '')

const BROKEN_MIN = 'video:[/^avc/,/^hev/,/^hvc/,/^vp0?[89]/,/^av1$/]'
const FIXED_MIN = 'video:[/^avc/,/^hev/,/^hvc/,/^vp0?[89]/,/^av01/]'
// es/ 源码形态（vite 从 package.json 的 module 字段走这份！多行、带空格）
const esFile = `${path}es/hls/manifest-loader/parser/utils.js`
const BROKEN_ES = 'video: [/^avc/, /^hev/, /^hvc/, /^vp0?[89]/, /^av1$/]'
const FIXED_ES = 'video: [/^avc/, /^hev/, /^hvc/, /^vp0?[89]/, /^av01/]'

let patched = 0
let skipped = 0

// 1) dist/index.min.js（main 字段；webpack/umd 场景）
const minFile = `${path}dist/index.min.js`
if (existsSync(minFile)) {
  let content = readFileSync(minFile, 'utf8')
  if (content.includes(FIXED_MIN)) {
    skipped += 1
  } else if (content.includes(BROKEN_MIN)) {
    writeFileSync(minFile, content.replace(BROKEN_MIN, FIXED_MIN))
    patched += 1
  } else {
    console.warn('[patch-xgplayer-hls] 警告：index.min.js 未找到目标正则表（官方包可能已更新）')
  }
}

// 2) es/hls/manifest-loader/parser/utils.js（module 字段；vite 实际用的是这份）
if (existsSync(esFile)) {
  let content = readFileSync(esFile, 'utf8')
  if (content.includes(FIXED_ES)) {
    skipped += 1
  } else if (content.includes(BROKEN_ES)) {
    writeFileSync(esFile, content.replace(BROKEN_ES, FIXED_ES))
    patched += 1
  } else {
    // 兼容空格差异的宽松替换
    if (/\/\^av1\$\/\]/.test(content)) {
      writeFileSync(esFile, content.replace('/^av1$/]', '/^av01/]'))
      patched += 1
    } else {
      console.warn('[patch-xgplayer-hls] 警告：es/utils.js 未找到目标正则（官方包可能已更新）')
    }
  }
}

console.log(`[patch-xgplayer-hls] patched=${patched} already=${skipped}（/^av1$/ → /^av01/，AV1 CODECS 支持）`)
