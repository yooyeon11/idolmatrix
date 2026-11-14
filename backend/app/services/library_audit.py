"""全库一致性审计引擎（机位 / 场次 / 简介 / 曲目 / 日期 / 同名）。

**为什么放在项目里**（业主 2026-10-18）：
每次核对都靠 agent 现场读代码 + 探接口 + 抽样，token 消耗高且口径易漂。
把规则固化成这里的纯函数后，核对变成「跑一条命令 → 只读一行汇总」，口径单一、可回归。

## 口径来源（不重写，全部 import）
- 机位 / 场次：`app.core.vocab`（`resolve_camera_angle` / `build_session_label`）
- 类型归一：`app.services.video_meta.normalize_video_types`

## 设计要点
- **接受任意记录对象**：SQLAlchemy `MusicVideo` 或 dict 都行（`_g()` 兼容两者），
  这样 CLI 走 DB、单测走字典、API 走 ORM 都能复用。
- **只提案、不写库**：`Finding` 带 `kind`（auto / suggest / manual / optional / info），
  由调用方决定是否 `--apply`。
- **可回归**：同样的输入必得同样的输出，便于测试与版本比对。

用法：
    from app.services.library_audit import audit_records, render_markdown, summarize
    findings = audit_records(records)
    print(summarize(findings))
    open('audit.md','w').write(render_markdown(findings, records))
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Optional, Sequence

from app.core import vocab as V
from app.services.video_meta import (
    normalize_video_types,
    resolve_camera_angle,
    title_camera_tail,
)

# ── 常量 ──────────────────────────────────────────────────────────────
# 非默认机位 → 简介里应写明的中文/英文（对齐 vocab.CAMERA_LABELS）
CAM_NEEDS_MENTION = {
    "FaceCam": ("面部", "Face Cam", "FaceCam", "얼빡", "페이스캠"),
    "FullCam": ("全景", "Full Cam", "FullCam", "전체캠", "풀캠"),
    # `BroadcastCam`（放送机位）是**官方舞台的常态**，与 `FanCam` 一样属默认机位 →
    # 不要求写进简介（2026-10-23 口径；写了是噪音，且会让存量回归基线全红）。
    "Tower Cam": ("塔式", "Tower Cam", "TowerCam", "타워캠"),
    "K-Choreo": ("编舞", "Choreography", "K-Choreo", "안무"),
    "OneTake": ("一镜到底", "One Take", "OneTake", "원테이크"),
    "MultiCam": ("多机位", "Multi Cam", "MultiCam", "멀티캠"),
    "SkyCam": ("航拍", "Sky Cam", "항공캠"),
}
HANGUL = re.compile(r"[\uac00-\ud7a3]")
SONG_IN_DESC = re.compile(r"(?:表演歌曲|翻唱歌曲|发布歌曲)《?([^。》]+?)》?( 的|。|$)")

KINDS = ("auto", "suggest", "manual", "optional", "info")


@dataclass
class Finding:
    """一条待处理项（字段级 before/after）。"""

    code: str            # 规则码，如 CAM / SES / DATE1
    group: str           # 分组：机位 / 场次 / 日期 / 简介 / 曲目 / 同名
    kind: str            # auto / suggest / manual / optional / info
    field_name: str      # 字段名（给人看）
    old: str
    new: str
    reason: str
    video_id: Optional[int] = None
    file_name: str = ""

    def as_dict(self) -> dict:
        return {
            "code": self.code, "group": self.group, "kind": self.kind,
            "field": self.field_name, "old": self.old, "new": self.new,
            "reason": self.reason, "id": self.video_id, "file_name": self.file_name,
        }


def _g(obj: Any, key: str, default=None):
    """兼容 ORM 对象与 dict 的取值。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_date(value) -> Optional[date]:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _file_name(rec: Any) -> str:
    """视频文件名：优先 file_path 的末段，回退 name。"""
    fp = (_g(rec, "file_path") or "").replace("\\", "/")
    return fp.rsplit("/", 1)[-1] if fp else (_g(rec, "name") or "")


def _song_names(rec: Any) -> list[str]:
    songs = _g(rec, "songs") or []
    out = []
    for s in songs:
        nm = _g(s, "name") if isinstance(s, (dict,)) or hasattr(s, "name") else None
        if nm:
            out.append(nm)
    return out


def _song_ids(rec: Any) -> list[int]:
    ids = _g(rec, "song_ids")
    if ids:
        return list(ids)
    return [s.get("id") if isinstance(s, dict) else getattr(s, "id", None)
            for s in (_g(rec, "songs") or [])]


def audit_records(
    records: Iterable[Any],
    *,
    dirty_song_ids: Optional[set[int]] = None,
    name_counts: Optional[Counter] = None,
) -> list[Finding]:
    """对一批记录做全量核对，返回按记录分组的 Finding 列表。

    `dirty_song_ids` / `name_counts` 可由调用方预计算（避免每条都全表扫）。
    """
    records = list(records)
    name_counts = name_counts or Counter(_g(r, "name") for r in records)
    out: list[Finding] = []

    for rec in records:
        vid = _g(rec, "id")
        fname = _file_name(rec)
        nm = _g(rec, "name") or ""
        event = (_g(rec, "event_name") or "").strip()
        desc = _g(rec, "chinese_description") or ""
        perf = _as_date(_g(rec, "performance_date"))
        pub = _as_date(_g(rec, "published_date"))
        uploader = _g(rec, "original_uploader") or ""
        vtypes = list(_g(rec, "video_types") or [])

        def add(code, group, kind, fld, old, new, reason):
            out.append(Finding(code=code, group=group, kind=kind, field_name=fld,
                               old=str(old or ""), new=str(new or ""), reason=reason,
                               video_id=vid, file_name=fname))

        # ---- 机位 ----
        # ★ 业主 2026-10-23：**机位只对官方舞台（PerformanceVideo）填**；
        #   其他类型（含 SpecialStage 特别舞台 / Fancam）一律应留空。
        cur_cam = (_g(rec, "camera_angle") or "").strip()
        if (vtypes or [""])[0] != "PerformanceVideo":
            if cur_cam:
                add("CAM", "机位", "manual", "camera_angle", cur_cam, "",
                    f"非官方舞台（{'＋'.join(vtypes) or '（空）'}）不应填机位 —— "
                    f"按口径应**清空**（旧版服务端会把空值重新按尾标推回，需新版镜像）")
        else:
            cam, unknown = resolve_camera_angle(nm, None)
            if unknown:
                add("CAM", "机位", "manual", "camera_angle", cur_cam,
                    "", f"尾标「{unknown}」含机位特征但不在词表 —— 需扩词表或选「其他」")
            elif cam and cam != cur_cam:
                add("CAM", "机位", "auto", "camera_angle", cur_cam, cam,
                    f"name 尾标「{title_camera_tail(nm)}」→ 词表归一为 {cam}")
            elif not cam and not cur_cam:
                add("CAM", "机位", "auto", "camera_angle", "", "BroadcastCam",
                    f"官方舞台尾标「{title_camera_tail(nm) or '（空）'}」无机位词 "
                    f"→ 按口径记「放送机位」（BroadcastCam）")

        # ---- 场次 ----
        cur_ses = (_g(rec, "session_label") or "").strip()
        ses = V.build_session_label(perf, pub, event, uploader)
        if vtypes == ["OfficialMV"] and not cur_ses:
            # 业主 2026-10-18 批复：MV 不用填场次（本无「场次」概念）。
            # 系统入库时会按「活动名缺失→回退频道名」自动生成，故这里只提示不提案。
            if ses:
                add("SES", "场次", "info", "session_label", "", "",
                    f"MV 无场次概念；但系统会自动回退频道名生成「{ses}」——"
                    f"业主口径是**不用填**，如需保持空请手工清空")
        elif ses and ses != cur_ses:
            why = []
            why.append(f"日期取 {'performance_date' if perf else 'published_date'}")
            why.append(f"活动名「{event}」" if event else f"活动名空 → 回退频道名「{uploader}」")
            add("SES", "场次", "auto", "session_label", cur_ses, ses, "；".join(why))
        elif not ses:
            add("SES", "场次", "manual", "session_label", cur_ses, "",
                "日期/活动名/频道名 均缺失 —— 无法生成场次")

        # ---- 日期 ----
        if not perf:
            cand = None
            m = re.match(r"^(\d{2})(\d{2})(\d{2})", nm)
            if m:
                cand = date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if cand and (not pub or cand <= pub):
                add("DATE1", "日期", "auto", "performance_date", "", cand.isoformat(),
                    "取 name 的 YYMMDD 段（≤上传日）")
            else:
                add("DATE1", "日期", "manual", "performance_date", "", "",
                    "演出日为空且 name 无可用日期")
        elif pub and perf > pub:
            add("DATE2", "日期", "suggest", "performance_date", perf.isoformat(),
                min(perf, pub).isoformat(),
                f"演出日不得晚于上传日 {pub.isoformat()}（需核实真实演出日）")

        # ---- 简介 ----
        if not desc:
            add("DESC0", "简介", "manual", "chinese_description", "", "",
                "中文简介为空 —— 需按句式补写")
        else:
            if not re.search(r"于 \d{4} 年", desc):
                add("DESC1", "简介", "optional", "chinese_description", desc, "",
                    "旧式长句（可选升级为现行句式，会丢原文细节）")
            m2 = SONG_IN_DESC.search(desc)
            if m2 and HANGUL.search(m2.group(1)):
                add("DESC2", "简介", "manual", "chinese_description", desc, "",
                    f"曲名「{m2.group(1)[:24]}」含韩文 —— 应改官方英文名（需人工定英文名）")
            if "OfficialMV" in vtypes and "表演" in desc:
                add("DESC3", "简介", "manual", "chinese_description", desc, "",
                    "MV 类应用「发布」而非「表演」")
            # 只用**已存值**：非官方舞台本来就不填机位（业主 2026-10-23），
            # 且 `cam` 只在 PerformanceVideo 分支里赋值，跨轮会残留。
            eff_cam = cur_cam
            if eff_cam in CAM_NEEDS_MENTION and not any(
                    x.lower() in desc.lower() for x in CAM_NEEDS_MENTION[eff_cam]):
                add("DESC4", "简介", "manual", "chinese_description", desc, "",
                    f"机位 {eff_cam}（{V.camera_label(eff_cam)}）未写进简介")

        # ---- 曲目 ----
        sids = _song_ids(rec)
        if not sids and vtypes != ["SpecialVideo"]:
            add("SONG0", "曲目", "manual", "songs", "", "",
                "无关联曲目（且类型非 SpecialVideo）")
        if dirty_song_ids:
            for sid in sids:
                if sid in dirty_song_ids:
                    add("SONG1", "曲目", "manual", f"songs[{sid}].release_artist", "", "",
                        "引用曲目原唱（release_artist）缺失")

        # ---- 同名 ----
        if name_counts.get(nm, 0) > 1:
            add("DUP", "同名", "info", "name", nm, "",
                f"全库有 {name_counts[nm]} 条完全同名 —— 需确认是否允许并存")

    return out


def summarize(findings: Sequence[Finding]) -> dict:
    """机器可读汇总（给 CLI 一行输出、给 API 返回）。"""
    by_kind = Counter(f.kind for f in findings)
    by_group = Counter(f.group for f in findings)
    by_code = Counter(f.code for f in findings)
    return {
        "items": len({(f.video_id) for f in findings}),
        "findings": len(findings),
        "by_kind": {k: by_kind.get(k, 0) for k in KINDS},
        "by_group": dict(by_group),
        "by_code": dict(by_code),
    }


def render_markdown(
    findings: Sequence[Finding],
    *,
    title: str = "全库一致性检查清单",
    include_optional: bool = False,
    max_items: Optional[int] = None,
) -> str:
    """渲染**多行分块**清单（业主偏好：按视频分块，不用宽表格）。

    主表含 auto / suggest / manual / info；`optional`（旧式长句）与 `info`（同名）
    默认不带明细行，只在末尾给计数，避免清单被上千条淹没。
    """
    keep = [f for f in findings
            if include_optional or f.kind not in ("optional",)]
    groups = defaultdict(list)
    for f in keep:
        groups[(f.video_id, f.file_name)].append(f)

    lines = [f"# {title}", ""]
    s = summarize(findings)
    lines.append("> 条目 **%d** ｜ 待处理 **%d** 项（✅ %d ｜ 🔶 %d ｜ ✋ %d ｜ ⚠️ %d ｜ ℹ️ %d）"
                 % (s["items"], s["findings"], s["by_kind"]["auto"], s["by_kind"]["suggest"],
                    s["by_kind"]["manual"], s["by_kind"]["optional"], s["by_kind"]["info"]))
    lines.append("")

    badge = {"auto": "✅ 可直接改", "suggest": "🔶 建议待确认", "manual": "✋ 需人工",
             "optional": "⚠️ 可选", "info": "ℹ️ 仅提示"}
    n = 0
    for (vid, fname), fs in groups.items():
        fs = [f for f in fs if include_optional or f.kind != "optional"]
        if not fs or (len(fs) == 1 and fs[0].kind == "info" and not include_optional):
            continue
        n += 1
        if max_items and n > max_items:
            break
        lines.append(f"### {n:02d} · `{fname}`")
        lines.append("")
        for f in fs:
            lines.append(f"**`{f.field_name}`** ｜ {f.code} ｜ {badge[f.kind]}")
            lines.append("")
            lines.append(f"- 原值：{('`%s`' % f.old) if f.old else '（空）'}")
            if f.new:
                lines.append(f"- 建议改为：**`{f.new}`**")
            lines.append(f"- 依据：{f.reason}")
            lines.append("")
        lines.append("---")
        lines.append("")
    if s["by_kind"]["optional"]:
        lines.append(f"> ⚠️ 另有 **{s['by_kind']['optional']}** 条旧式简介（可选升级，未列明细）")
    if s["by_kind"]["info"]:
        lines.append(f"> ℹ️ 另有 **{s['by_kind']['info']}** 条同名条目（仅提示，未列明细）")
    return "\n".join(lines)


def render_chunks(
    findings: Sequence[Finding],
    *,
    size: int = 30,
    include_optional: bool = False,
    title: str = "一致性检查清单",
) -> tuple[list[tuple[str, str]], dict]:
    """按**视频**分块渲染（业主 2026-10-18：每 30 个视频一份清单，便于分批调阅）。

    分块对象 = **有发现的视频**（合规视频不占篇幅，只在汇总里计数），
    按 video_id 升序每 `size` 条一块。

    返回 `([(文件名, 内容), ...], 汇总dict)`。
    """
    badge = {"auto": "✅ 可直接改", "suggest": "🔶 建议待确认", "manual": "✋ 需人工",
             "optional": "⚠️ 可选", "info": "ℹ️ 仅提示"}
    groups: dict[tuple, list[Finding]] = defaultdict(list)
    for f in findings:
        groups[(f.video_id or 0, f.file_name)].append(f)
    keys = sorted(groups.keys(), key=lambda k: (k[0], k[1]))
    total_chunks = max(1, (len(keys) + size - 1) // size)
    s = summarize(findings)

    files: list[tuple[str, str]] = []
    index_rows: list[str] = []
    for ci in range(total_chunks):
        part = keys[ci * size:(ci + 1) * size]
        if not part:
            continue
        sub = [f for k in part for f in groups[k]
               if include_optional or f.kind != "optional"]
        cnt = Counter(f.kind for f in sub)
        codes = Counter(f.code for f in sub)
        lo, hi = part[0][0], part[-1][0]
        lines = [
            f"# {title} · 第 {ci + 1:03d} / {total_chunks:03d} 批",
            "",
            f"> 本批 **{len(part)}** 个视频（`#{lo}`–`#{hi}`）｜ 待处理 **{len(sub)}** 项"
            f"（✅ {cnt.get('auto', 0)} ｜ 🔶 {cnt.get('suggest', 0)} ｜ ✋ {cnt.get('manual', 0)}"
            f" ｜ ⚠️ {cnt.get('optional', 0)} ｜ ℹ️ {cnt.get('info', 0)}）",
            f"> 规则分布： {' '.join(f'{k}:{v}' for k, v in codes.most_common())}",
            "",
            "> 读法：每个字段一行「原值 → 建议改为」；`✅` 可直接改，`🔶` 需你点头，`✋` 推不出值。",
            "",
        ]
        for i, k in enumerate(part, 1):
            fs = [f for f in groups[k] if include_optional or f.kind != "optional"]
            if not fs:
                continue
            lines.append(f"### {i:02d} · `{k[1]}`")
            lines.append("")
            for f in fs:
                lines.append(f"**`{f.field_name}`** ｜ {f.code} ｜ {badge[f.kind]}")
                lines.append("")
                lines.append(f"- 原值：{('`%s`' % f.old) if f.old else '（空）'}")
                if f.new:
                    lines.append(f"- 建议改为：**`{f.new}`**")
                lines.append(f"- 依据：{f.reason}")
                lines.append("")
            lines.append("---")
            lines.append("")
        name = f"chunk-{ci + 1:03d}.md"
        files.append((name, "\n".join(lines)))
        index_rows.append(f"| [{ci + 1:03d}]({name}) | `#{lo}`–`#{hi}` | {len(part)} | "
                          f"{len(sub)} | {cnt.get('auto', 0)} | {cnt.get('manual', 0)} |")

    # 汇总页
    total_recs = len({(f.video_id, f.file_name) for f in findings})
    summary = {
        "chunks": len(files),
        "chunk_size": size,
        "records_with_findings": total_recs,
        **s,
    }
    lines = [
        f"# {title} · 总表",
        "",
        f"> 全库 **{s['items']}** 个视频有发现 ｜ 分 **{len(files)}** 批（每批 {size} 个）",
        f"> 待处理 **{s['findings']}** 项（✅ {s['by_kind']['auto']} ｜ 🔶 {s['by_kind']['suggest']}"
        f" ｜ ✋ {s['by_kind']['manual']} ｜ ⚠️ {s['by_kind']['optional']} ｜ ℹ️ {s['by_kind']['info']}）",
        "",
        "## 规则分布",
        "",
        "| 规则 | 说明 | 命中 |",
        "|---|---|---|",
    ]
    CODE_DESC = {
        "CAM": "机位（camera_angle）", "SES": "场次（session_label）",
        "DATE1": "演出日为空", "DATE2": "演出日晚于上传日",
        "DESC0": "中文简介为空", "DESC1": "简介旧式长句（可选）", "DESC2": "简介曲名含韩文",
        "DESC3": "MV 简介用了「表演」", "DESC4": "机位未写进简介",
        "SONG0": "无关联曲目", "SONG1": "引用脏曲目实体", "DUP": "完全同名",
    }
    for k, v in sorted(s["by_code"].items(), key=lambda x: -x[1]):
        lines.append(f"| `{k}` | {CODE_DESC.get(k, '')} | **{v}** |")
    lines += ["", "## 分批索引", "",
              "| 批 | id 区间 | 视频数 | 待处理 | ✅ | ✋ |", "|---|---|---|---|---|---|"]
    lines += index_rows
    files.insert(0, ("README.md", "\n".join(lines)))
    return files, summary


def find_by_scope(records: Sequence[Any], scope: str) -> list[Any]:
    """按范围筛选：`all` / `path:前缀` / `id:起-止` / `group:团体id`。"""
    scope = (scope or "all").strip()
    if scope in ("all", "", "*"):
        return list(records)
    kind, _, val = scope.partition(":")
    if kind == "path":
        return [r for r in records if (_g(r, "file_path") or "").startswith(val)]
    if kind == "id":
        lo, _, hi = val.partition("-")
        lo_i, hi_i = int(lo), int(hi or lo)
        return [r for r in records if lo_i <= int(_g(r, "id") or 0) <= hi_i]
    if kind == "group":
        gid = int(val)
        return [r for r in records
                if gid in [(_g(g, "id") if not isinstance(g, dict) else g.get("id"))
                           for g in (_g(r, "groups") or [])]]
    raise ValueError(f"无法识别的 scope：{scope}")
