import http from './index'
import type { MusicVideo, Pagination, VideoCollection } from '@/types/models'

export const videoCollectionsApi = {
  list() {
    return http.get<VideoCollection[]>('/video-collections').then((r) => r.data)
  },
  create(payload: { name: string; description?: string | null }) {
    return http.post<VideoCollection>('/video-collections', payload).then((r) => r.data)
  },
  get(id: number) {
    return http.get<VideoCollection>(`/video-collections/${id}`).then((r) => r.data)
  },
  getByUid(uid: string) {
    return http
      .get<VideoCollection>(`/video-collections/by-uid/${encodeURIComponent(uid)}`)
      .then((r) => r.data)
  },
  update(id: number, payload: { name?: string; description?: string | null }) {
    return http.patch<VideoCollection>(`/video-collections/${id}`, payload).then((r) => r.data)
  },
  remove(id: number) {
    return http.delete(`/video-collections/${id}`).then((r) => r.data)
  },
  videos(id: number, params: { page?: number; page_size?: number } = {}) {
    return http
      .get<Pagination<MusicVideo>>(`/video-collections/${id}/videos`, { params })
      .then((r) => r.data)
  },
  add(collectionId: number, payload: { video_id?: number; video_uid?: string }) {
    return http
      .post<VideoCollection>(`/video-collections/${collectionId}/videos`, payload)
      .then((r) => r.data)
  },
  removeVideo(collectionId: number, videoId: number) {
    return http
      .delete<VideoCollection>(`/video-collections/${collectionId}/videos/${videoId}`)
      .then((r) => r.data)
  },
  memberships(videoIds: number[]) {
    if (!videoIds.length) {
      return Promise.resolve({ memberships: {} as Record<string, number[]> })
    }
    return http
      .get<{ memberships: Record<string, number[]> }>('/video-collections/memberships', {
        params: { video_ids: videoIds.join(',') },
      })
      .then((r) => r.data)
  },
}
