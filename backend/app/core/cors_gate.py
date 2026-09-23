"""跨站写请求放行开关（全局）。

CSRF 校验跑在 AuthGate 中间件里，无 DB 会话、每个写请求都执行，不能每请求查库。
这里用模块级 bool 缓存。

v3.2.18 起：设置页的「放行跨站写请求」开关已移除，**唯一来源是环境变量**
ALLOW_CROSS_ORIGIN_WRITES（启动时经 `app_settings.init_cross_origin_flag()` 灌入），
运行期不再变化 —— 没有 DB 分区、也没有写入即刷新的路径。
"""

_allow_cross_origin_writes = False


def set_allow_cross_origin_writes(allow: bool) -> None:
    global _allow_cross_origin_writes
    _allow_cross_origin_writes = bool(allow)


def cross_origin_writes_allowed() -> bool:
    return _allow_cross_origin_writes