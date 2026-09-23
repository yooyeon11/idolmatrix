import http from './index'

/** 字段锁稀疏键值：键为 DB 字段名或保留区块键（avatar / memberships），true=锁定 */
export type EntityLockMap = Record<string, boolean>

export const entityLocksApi = {
  get(entityType: string, entityId: number) {
    return http
      .get<{ locks: EntityLockMap }>(`/entity-locks/${entityType}/${entityId}`)
      .then((r) => r.data.locks ?? {})
  },
  put(entityType: string, entityId: number, locks: EntityLockMap) {
    return http
      .put<{ locks: EntityLockMap }>(`/entity-locks/${entityType}/${entityId}`, { locks })
      .then((r) => r.data.locks ?? {})
  },
}
