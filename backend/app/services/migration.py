"""钻孔存量记录合并迁移。

现场补录时可能已经存在按旧孔号、别名建立的重复记录。这里只做当前主数据的
归并：旧行保留到历史数据中，不回写历史钻探日志，避免把已经形成的事实追溯改掉。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def _aliases(row: dict[str, Any]) -> list[str]:
    raw = row.get("钻孔别名") or row.get("别名") or []
    if isinstance(raw, str):
        values = [item.strip() for item in raw.replace("；", ",").replace(";", ",").split(",")]
    elif isinstance(raw, (list, tuple, set)):
        values = [_text(item) for item in raw]
    else:
        values = []
    return [item for item in values if item]


def merge_borehole_rows(
    borehole_rows: list[dict[str, Any]],
    related_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """把同一组孔号/别名的多条钻孔归并为一条主记录。

    related_rows 仅用于按待办的当前引用归位；没有 `pending=True` 或没有当前
    canonical 引用字段的历史行会原样保留。返回列表隐藏旧行但不删除，旧详情仍能
    按 merged_into 找到主记录。
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in borehole_rows:
        names = [_text(row.get("钻孔编号"))] + _aliases(row)
        names = [name for name in dict.fromkeys(names) if name]
        group_key = next((key for name in names for key, members in groups.items() if any(name in {_text(member.get("钻孔编号")), *_aliases(member)} for member in members)), "")
        if not group_key:
            group_key = names[0] if names else f"__id__{row.get('id')}"
        groups[group_key].append(row)

    related_rows = related_rows or []
    visible: list[dict[str, Any]] = []
    for members in groups.values():
        if len(members) <= 1:
            visible.append(members[0])
            continue
        members.sort(key=lambda item: int(item.get("id", 0)))

        confirmed_number_row = next((item for item in members if _text(item.get("现场确认钻孔编号"))), None)
        confirmed_coordinate_row = next(
            (item for item in members if item.get("坐标已确认") and _text(item.get("孔口坐标"))),
            None,
        )
        survivor = confirmed_number_row or confirmed_coordinate_row or members[0]
        canonical_number = _text(survivor.get("现场确认钻孔编号")) or _text(survivor.get("钻孔编号"))

        aliases: list[str] = []
        source_ids: list[int] = []
        for member in members:
            source_ids.append(int(member.get("id", 0)))
            for value in [
                member.get("钻孔编号"),
                member.get("现场确认钻孔编号"),
                *_aliases(member),
            ]:
                name = _text(value)
                if name and name != canonical_number and name not in aliases:
                    aliases.append(name)

        survivor["钻孔编号"] = canonical_number
        survivor.pop("现场确认钻孔编号", None)
        survivor["钻孔别名"] = aliases
        survivor["version"] = int(survivor.get("version") or 1)
        survivor["merged_into"] = survivor.get("id")
        survivor["merged_source_ids"] = source_ids
        if not survivor.get("孔口坐标"):
            filled = next((_text(item.get("孔口坐标")) for item in members if _text(item.get("孔口坐标"))), "")
            if filled:
                survivor["孔口坐标"] = filled
                survivor["坐标已确认"] = False
        if confirmed_coordinate_row is not None and survivor is not confirmed_coordinate_row:
            survivor["孔口坐标"] = confirmed_coordinate_row.get("孔口坐标")
            survivor["坐标已确认"] = True
        survivor.setdefault("坐标已确认", bool(confirmed_coordinate_row))
        visible.append(survivor)

        current_aliases = set(aliases) | {canonical_number}
        for related in related_rows:
            if not related.get("pending"):
                continue
            current_number = _text(related.get("当前钻孔编号")) or _text(related.get("钻孔编号"))
            if current_number in current_aliases:
                related["当前钻孔编号"] = canonical_number
                related["borehole_id"] = survivor.get("id")
                related["钻孔版本"] = survivor["version"]

        for member in members:
            if member is survivor:
                continue
            member["merged_into"] = survivor.get("id")
            member["canonical_钻孔编号"] = canonical_number
            member["hidden"] = True

    visible.sort(key=lambda item: int(item.get("id", 0)))
    return visible


def migrate_borehole_aliases(tables: dict[str, list[dict[str, Any]]]) -> None:
    """启动时归并冲突存量；旧行仍保留在内存表里供旧详情跳转。"""
    borehole_rows = tables.setdefault("borehole", [])
    if borehole_rows:
        merge_borehole_rows(borehole_rows, tables.get("drilling_log", []))
