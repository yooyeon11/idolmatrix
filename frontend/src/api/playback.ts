import http from './index'
import type { PlaybackSession, PlaybackSessionWindow, TranscodeSessionInfo } from '@/types/models'

export interface CreatePlaybackSessionPayload {
  music_video_id: number
  requested_quality?: string
  start_seconds?: number
  audio_stream_id?: number
  subtitle_stream_id?: number
  client?: Record<string, unknown>
}

export const playbackApi = {
  /** 创建播放会话：服务端决策 Direct Play / Direct Stream / Transcode，返回播放 URL */
  create(payload: CreatePlaybackSessionPayload) {
    return http.post<PlaybackSession>('/playback/sessions', payload).then((r) => r.data)
  },
  get(sessionId: number) {
    return http.get<PlaybackSession>(`/playback/sessions/${sessionId}`).then((r) => r.data)
  },
  heartbeat(sessionId: number) {
    return http.post<PlaybackSession>(`/playback/sessions/${sessionId}/heartbeat`).then((r) => r.data)
  },
  stop(sessionId: number, opts?: { force?: boolean }) {
    const force = opts?.force !== false
    return http
      .post(`/playback/sessions/${sessionId}/stop`, null, { params: { force } })
      .then((r) => r.data)
  },
  transcodeInfo(sessionId: number) {
    return http.get<TranscodeSessionInfo>(`/playback/sessions/${sessionId}/transcode`).then((r) => r.data)
  },
  /** 本会话「已可播范围」：available_until = 已转出上界（**片源绝对秒**）。
   * 转码中随 FFmpeg 产出增长，按 2s 级轮询；finished 后可停止轮询。
   * 播放器据此判断 seek 是原地跳还是重建会话（见 VideoPlayer.canSeekInPlace）。 */
  sessionWindow(sessionId: number) {
    return http
      .get<PlaybackSessionWindow>(`/playback/sessions/${sessionId}/window`)
      .then((r) => r.data)
  },
}
