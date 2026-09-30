"""钻探日志业务规则：待办跟随当前钻孔版本，审核后冻结历史快照。"""
from __future__ import annotations

from typing import Any

from app.services.borehole import (
    aliases_for,
    normalize_text,
)
from app.store import store

MODULE = "drilling_log"
BOREHOLE_MODULE = "borehole"
REQUIRED_FIELDS = ["日志编号", "钻孔编号", "钻进深度"]
STATUS_ORDER = ["待填写", "已填写", "已审核", "退回补充"]
ACTION_RULES = {"填写日志": "已填写", "提交审核": "已审核", "退回补充": "退回补充"}
NEGATIVE_ACTIONS = []
OPEN_STATUSES = {"待填写", "退回补充"}


def resolve_borehole(identifier: Any) -> dict[str, Any] | None:
    text = normalize_text(identifier)
    if not text:
        return None
    rows = store.rows(BOREHOLE_MODULE)
    try:
        entry_id = int(text)
    except ValueError:
        entry_id = None
    for row in rows:
        if entry_id is not None and int(row.get("id", 0)) == entry_id and not row.get("hidden"):
            return row
    for row in rows:
        if not row.get("hidden") and (text == normalize_text(row.get("钻孔编号")) or text in aliases_for(row)):
            return row
    return None


def public_entry(row: dict[str, Any]) -> dict[str, Any]:
    entry = dict(row)
    status = normalize_text(entry.get("status")) or normalize_text(entry.get("日志状态")) or STATUS_ORDER[0]
    entry["status"] = status
    entry["日志状态"] = status
    entry["pending"] = status in OPEN_STATUSES

    is_open = entry["pending"]
    display_number = entry.get("当前钻孔编号") if is_open else entry.get("归档钻孔编号")
    entry["钻孔编号"] = normalize_text(display_number or entry.get("钻孔编号"))
    borehole_id = entry.get("borehole_id")
    if borehole_id is not None:
        borehole = store.find(BOREHOLE_MODULE, int(borehole_id))
        if borehole is not None and is_open:
            entry["当前钻孔编号"] = normalize_text(borehole.get("钻孔编号"))
            entry["钻孔版本"] = int(borehole.get("version") or 1)
        elif borehole is not None:
            entry["归档钻孔编号"] = entry.get("归档钻孔编号") or normalize_text(borehole.get("钻孔编号"))
            entry["钻孔版本"] = int(entry.get("钻孔版本") or borehole.get("version") or 1)
    return entry


class DrillingLogService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        pending: bool | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        text = normalize_text(keyword)
        if text:
            rows = [
                row
                for row in rows
                if text in normalize_text(row.get("日志编号"))
                or text in normalize_text(row.get("当前钻孔编号"))
                or text in normalize_text(row.get("钻孔编号"))
            ]
        if status:
            rows = [row for row in rows if normalize_text(row.get("status")) == status]
        if pending is not None:
            rows = [
                row
                for row in rows
                if (normalize_text(row.get("status")) in OPEN_STATUSES) is pending
            ]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [public_entry(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return public_entry(row) if row else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not normalize_text(values.get(field))]
        if missing:
            return None, missing
        with store.transaction():
            rows = store.rows(MODULE)
            borehole = resolve_borehole(values.get("borehole_id") or values.get("钻孔编号"))
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
            entry["status"] = STATUS_ORDER[0]
            entry["日志状态"] = STATUS_ORDER[0]
            entry["pending"] = True
            entry["abnormal"] = False
            if borehole is not None:
                canonical = normalize_text(borehole.get("钻孔编号"))
                entry["钻孔编号"] = canonical
                entry["当前钻孔编号"] = canonical
                entry["borehole_id"] = borehole.get("id")
                entry["钻孔版本"] = int(borehole.get("version") or 1)
            rows.append(entry)
            return public_entry(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        with store.transaction():
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"钻探记录 {entry_id} 不存在或已归档"
            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于钻探日志可执行范围"
            previous_status = normalize_text(entry.get("status"))
            target = ACTION_RULES[action]
            entry["status"] = target
            entry["日志状态"] = target
            entry["pending"] = target in OPEN_STATUSES
            entry["abnormal"] = action in NEGATIVE_ACTIONS

            if previous_status in OPEN_STATUSES and target not in OPEN_STATUSES:
                borehole_id = entry.get("borehole_id")
                borehole = store.find(BOREHOLE_MODULE, int(borehole_id)) if borehole_id is not None else None
                entry["归档钻孔编号"] = (
                    normalize_text(borehole.get("钻孔编号")) if borehole is not None else normalize_text(entry.get("当前钻孔编号") or entry.get("钻孔编号"))
                )
                entry["钻孔版本"] = int(borehole.get("version") or entry.get("钻孔版本") or 1) if borehole is not None else entry.get("钻孔版本")
            elif target in OPEN_STATUSES and borehole_reference_exists(entry):
                borehole = resolve_borehole(entry.get("borehole_id") or entry.get("钻孔编号"))
                if borehole is not None:
                    canonical = normalize_text(borehole.get("钻孔编号"))
                    entry["borehole_id"] = borehole.get("id")
                    entry["当前钻孔编号"] = canonical
                    entry["钻孔编号"] = canonical
                    entry["钻孔版本"] = int(borehole.get("version") or 1)
                    entry.pop("归档钻孔编号", None)
            return public_entry(entry), f"钻探记录已{action}"


def borehole_reference_exists(entry: dict[str, Any]) -> bool:
    return entry.get("borehole_id") is not None or bool(normalize_text(entry.get("当前钻孔编号")))
