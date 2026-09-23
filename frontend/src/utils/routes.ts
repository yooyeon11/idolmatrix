/** 前端页面路径：对外一律用实体 uid，不再暴露内部数字 id。 */

export function videoPath(uid: string): string {
  return `/videos/${encodeURIComponent(uid)}`
}

export function artistPath(uid: string): string {
  return `/artists/${encodeURIComponent(uid)}`
}

export function songPath(uid: string): string {
  return `/songs/${encodeURIComponent(uid)}`
}

export function groupPath(uid: string): string {
  return `/groups/${encodeURIComponent(uid)}`
}

export function collectionPath(uid: string): string {
  return `/collections/${encodeURIComponent(uid)}`
}

export function videoCollectionPath(uid: string): string {
  return `/video-collections/${encodeURIComponent(uid)}`
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function isEntityUid(value: string): boolean {
  return UUID_RE.test(value.trim())
}

export function isNumericId(value: string): boolean {
  return /^\d+$/.test(value.trim())
}

/** 路由参数是 uid 走 by-uid，旧书签里的数字 id 仍可打开。 */
export async function fetchByRouteParam<T>(
  param: string,
  getByUid: (uid: string) => Promise<T>,
  getById: (id: number) => Promise<T>,
): Promise<T> {
  const p = param.trim()
  if (isEntityUid(p)) return getByUid(p)
  if (isNumericId(p)) return getById(Number(p))
  throw new Error('无效的地址')
}
