import http from './index'
import type {
  MtPhotosAlbum,
  MtPhotosFolder,
  MtPhotosPingResult,
  PhotoBrowseResult,
  PhotoCollection,
  PhotoFeed,
  PhotoFeedFilter,
  PhotoFilterOptions,
  PhotoItem,
  PhotoOwnerType,
  PhotoProvider,
  PhotoScanResult,
  PhotoSection,
  PhotoSource,
} from '@/types/models'

export const photosApi = {
  sources(ownerType: PhotoOwnerType, ownerId: number) {
    return http
      .get<PhotoSource[]>('/photos/sources', {
        params: { owner_type: ownerType, owner_id: ownerId },
      })
      .then((r) => r.data)
  },
  bind(payload: {
    owner_type: PhotoOwnerType
    owner_id: number
    section: PhotoSection
    folder_path?: string
    recursive?: boolean
    provider?: PhotoProvider
    external_id?: string
    mt_kind?: 'album' | 'folder'
  }) {
    return http
      .post<PhotoSource>('/photos/sources', payload, { timeout: 120_000 })
      .then((r) => r.data)
  },
  mtAlbums() {
    return http.get<MtPhotosAlbum[]>('/photos/mtphotos/albums').then((r) => r.data)
  },
  mtFolders(parentId?: number | null) {
    return http
      .get<MtPhotosFolder[]>('/photos/mtphotos/folders', {
        params: parentId ? { parent_id: parentId } : {},
      })
      .then((r) => r.data)
  },
  browse(path?: string | null) {
    return http
      .get<PhotoBrowseResult>('/photos/browse', {
        params: path ? { path } : {},
      })
      .then((r) => r.data)
  },
  mtPing(payload: { base_url?: string; api_key?: string } = {}) {
    return http
      .post<MtPhotosPingResult>('/photos/mtphotos/ping', payload, { timeout: 20_000 })
      .then((r) => r.data)
  },
  unbind(sourceId: number) {
    return http.delete(`/photos/sources/${sourceId}`).then((r) => r.data)
  },
  scan(sourceId: number) {
    return http
      .post<PhotoScanResult>(`/photos/sources/${sourceId}/scan`, null, {
        timeout: 120_000,
      })
      .then((r) => r.data)
  },
  upload(ownerType: PhotoOwnerType, ownerId: number, files: File[]) {
    const body = new FormData()
    for (const file of files) body.append('files', file)
    return http
      .post<{ created: number; photos: PhotoItem[] }>('/photos/upload', body, {
        params: { owner_type: ownerType, owner_id: ownerId },
        timeout: 180_000,
      })
      .then((r) => r.data)
  },
  feed(params: {
    owner_type: PhotoOwnerType
    owner_id: number
    section: PhotoSection
    page?: number
    page_size?: number
  } & PhotoFeedFilter) {
    const query: Record<string, unknown> = { ...params }
    if (Array.isArray(params.tags)) query.tags = params.tags.join(',')
    return http.get<PhotoFeed>('/photos/feed', { params: query }).then((r) => r.data)
  },
  feedFilterOptions(params: {
    owner_type: PhotoOwnerType
    owner_id: number
    section: PhotoSection
  }) {
    return http
      .get<PhotoFilterOptions>('/photos/feed/filter-options', { params })
      .then((r) => r.data)
  },
  fileUrl(photoId: number) {
    return `/api/photos/${photoId}/file`
  },
  thumbUrl(photoId: number) {
    return `/api/photos/${photoId}/thumb`
  },
  cropPortrait(
    photoId: number,
    payload: { kind: 'avatar' | 'banner'; x: number; y: number; width: number; height: number },
  ) {
    return http
      .post<{ kind: string; path: string; owner_type: string; owner_id: number }>(
        `/photos/${photoId}/portrait`,
        payload,
        { timeout: 120_000 },
      )
      .then((r) => r.data)
  },
  collections() {
    return http.get<PhotoCollection[]>('/photos/collections').then((r) => r.data)
  },
  createCollection(payload: { name: string; description?: string | null }) {
    return http.post<PhotoCollection>('/photos/collections', payload).then((r) => r.data)
  },
  getCollection(id: number) {
    return http.get<PhotoCollection>(`/photos/collections/${id}`).then((r) => r.data)
  },
  getCollectionByUid(uid: string) {
    return http
      .get<PhotoCollection>(`/photos/collections/by-uid/${encodeURIComponent(uid)}`)
      .then((r) => r.data)
  },
  updateCollection(id: number, payload: { name?: string; description?: string | null }) {
    return http.patch<PhotoCollection>(`/photos/collections/${id}`, payload).then((r) => r.data)
  },
  removeCollection(id: number) {
    return http.delete(`/photos/collections/${id}`).then((r) => r.data)
  },
  collectionPhotos(
    id: number,
    params: { page?: number; page_size?: number } & PhotoFeedFilter = {},
  ) {
    const query: Record<string, unknown> = { ...params }
    if (Array.isArray(params.tags)) query.tags = params.tags.join(',')
    return http
      .get<PhotoFeed>(`/photos/collections/${id}/photos`, { params: query })
      .then((r) => r.data)
  },
  collectionFilterOptions(id: number) {
    return http
      .get<PhotoFilterOptions>(`/photos/collections/${id}/photos/filter-options`)
      .then((r) => r.data)
  },
  remove(photoId: number) {
    return http
      .delete<{ deleted: number; files_deleted: number }>(`/photos/${photoId}`)
      .then((r) => r.data)
  },
  removePost(photoId: number) {
    return http
      .delete<{ deleted: number; files_deleted: number }>(`/photos/${photoId}/post`)
      .then((r) => r.data)
  },
  updateAnalysis(
    photoId: number,
    payload: { caption_zh: string; tags: string[] },
  ) {
    return http
      .patch<{ photo: PhotoItem; analysis: PhotoItem['analysis'] }>(
        `/photos/${photoId}/analysis`,
        payload,
      )
      .then((r) => r.data)
  },
  addToCollection(collectionId: number, payload: { photo_id?: number; photo_uid?: string }) {
    return http
      .post<PhotoCollection>(`/photos/collections/${collectionId}/photos`, payload)
      .then((r) => r.data)
  },
  removeFromCollection(collectionId: number, photoId: number) {
    return http
      .delete<PhotoCollection>(`/photos/collections/${collectionId}/photos/${photoId}`)
      .then((r) => r.data)
  },
  memberships(photoIds: number[]) {
    if (!photoIds.length) {
      return Promise.resolve({ memberships: {} as Record<string, number[]> })
    }
    return http
      .get<{ memberships: Record<string, number[]> }>('/photos/collections/memberships', {
        params: { photo_ids: photoIds.join(',') },
      })
      .then((r) => r.data)
  },
}
