"""钻孔编录业务规则：以现场确认孔号为主键，合并补录与待办同步。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.store import store

MODULE = "borehole"
LOG_MODULE = "drilling_log"
REQUIRED_FIELDS = ["钻孔编号", "勘探区", "孔口坐标"]
STATUS_ORDER = ["待施工", "钻进中", "已终孔", "已封孔", "已废弃"]
ACTION_RULES = {"开始钻进": "钻进中", "登记终孔": "已终孔", "执行封孔": "已封孔"}
NEGATIVE_ACTIONS = []
OPEN_LOG_STATUSES = {"待填写", "退回补充"}


class VersionConflict(RuntimeError):
    """客户端基于旧版本提交，必须重新读取后再决定。"""


@dataclass(frozen=True)
class SaveResult:
    entry: dict[str, Any] | None
    message: str
    duplicated: bool = False
    conflict: bool = False


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_aliases(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = str(value).replace("；", ",").replace(";", ",").split(",")
    result: list[str] = []
    for item in values:
        text = normalize_text(item)
        if text and text not in result:
            result.append(text)
    return result


def parse_coordinates(value: Any) -> tuple[float | None, float | None, str]:
    """返回 x/y；无法解析时保留原始坐标给表格和详情显示。"""
    if isinstance(value, dict):
        raw_x = value.get("x", value.get("X", value.get("坐标X")))
        raw_y = value.get("y", value.get("Y", value.get("坐标Y")))
        raw = f"{raw_x},{raw_y}"
    else:
        raw = normalize_text(value)
        raw_x = raw_y = ""
        if raw:
            for separator in [",", "，", "/", "|"]:
                if separator in raw:
                    raw_x, raw_y = raw.split(separator, 1)
                    break
            else:
                parts = raw.split()
                if len(parts) >= 2:
                    raw_x, raw_y = parts[0], parts[1]
    try:
        return float(normalize_text(raw_x)), float(normalize_text(raw_y)), raw
    except (TypeError, ValueError):
        return None, None, raw


def aliases_for(row: dict[str, Any]) -> list[str]:
    return normalize_aliases(row.get("钻孔别名") or row.get("别名"))


def names_for(row: dict[str, Any]) -> set[str]:
    names = {normalize_text(row.get("钻孔编号")), *aliases_for(row)}
    names.discard("")
    return names


def public_entry(row: dict[str, Any]) -> dict[str, Any]:
    """列表、详情、孔位图和动作响应共用的同一投影。"""
    entry = dict(row)
    number = normalize_text(entry.get("钻孔编号"))
    entry["钻孔编号"] = number
    entry["钻孔别名"] = aliases_for(entry)
    entry["version"] = int(entry.get("version") or 1)
    entry.setdefault("坐标已确认", False)
    x, y, raw = parse_coordinates(entry.get("孔口坐标"))
    entry["孔口坐标"] = raw if raw != "None,None" else normalize_text(row.get("孔口坐标"))
    entry["坐标X"] = x
    entry["坐标Y"] = y
    status = normalize_text(entry.get("status")) or normalize_text(entry.get("钻孔状态")) or STATUS_ORDER[0]
    entry["status"] = status
    entry["钻孔状态"] = status
    entry.pop("hidden", None)
    entry.pop("现场确认钻孔编号", None)
    return entry


def find_by_identifier(identifier: str | int | None) -> dict[str, Any] | None:
    text = normalize_text(identifier)
    if not text:
        return None
    rows = store.rows(MODULE)
    try:
        entry_id = int(text)
    except ValueError:
        entry_id = None

    for row in rows:
        if entry_id is not None and int(row.get("id", 0)) == entry_id:
            target_id = row.get("merged_into", row.get("id"))
            target = store.find(MODULE, int(target_id)) if target_id is not None else None
            return target if target is not None and not target.get("hidden") else row
    for row in rows:
        if not row.get("hidden") and text in names_for(row):
            return row
    for row in rows:
        if text in names_for(row):
            target_id = row.get("merged_into", row.get("id"))
            target = store.find(MODULE, int(target_id)) if target_id is not None else None
            if target is not None and not target.get("hidden"):
                return target
    return None


def require_version(row: dict[str, Any], expected: Any) -> None:
    if expected in (None, ""):
        return
    try:
        expected_version = int(expected)
    except (TypeError, ValueError) as exc:
        raise VersionConflict("版本号格式不正确，请重新打开钻孔详情") from exc
    if expected_version != int(row.get("version") or 1):
        raise VersionConflict("钻孔已被其他人更新，请刷新后基于最新版本提交")


def next_id(rows: list[dict[str, Any]]) -> int:
    source_ids = [int(row.get("id", 0)) for row in rows]
    for row in rows:
        source_ids.extend(int(item) for item in row.get("merged_source_ids", []) if str(item).isdigit())
    return max(source_ids, default=0) + 1


def sync_open_logs(borehole: dict[str, Any]) -> int:
    """只同步仍待处理的钻探日志；历史日志保留当时孔号。"""
    canonical = normalize_text(borehole.get("钻孔编号"))
    version = int(borehole.get("version") or 1)
    changed = 0
    for log in store.rows(LOG_MODULE):
        status = normalize_text(log.get("status")) or normalize_text(log.get("日志状态"))
        is_open = bool(log.get("pending")) or status in OPEN_LOG_STATUSES
        if not is_open:
            continue
        linked_id = log.get("borehole_id")
        current = normalize_text(log.get("当前钻孔编号")) or normalize_text(log.get("钻孔编号"))
        if linked_id == borehole.get("id") or current in names_for(borehole):
            log["borehole_id"] = borehole.get("id")
            log["当前钻孔编号"] = canonical
            log["钻孔版本"] = version
            changed += 1
    return changed


def ensure_open_todo(borehole: dict[str, Any]) -> dict[str, Any]:
    """开始钻进时保证钻探日志待办只有一条。"""
    for log in store.rows(LOG_MODULE):
        status = normalize_text(log.get("status")) or normalize_text(log.get("日志状态"))
        is_open = bool(log.get("pending")) or status in OPEN_LOG_STATUSES
        current = normalize_text(log.get("当前钻孔编号")) or normalize_text(log.get("钻孔编号"))
        if is_open and (log.get("borehole_id") == borehole.get("id") or current in names_for(borehole)):
            return log

    rows = store.rows(LOG_MODULE)
    todo = {
        "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
        "日志编号": f"TODO-{borehole.get('id')}",
        "钻孔编号": normalize_text(borehole.get("钻孔编号")),
        "当前钻孔编号": normalize_text(borehole.get("钻孔编号")),
        "borehole_id": borehole.get("id"),
        "钻孔版本": int(borehole.get("version") or 1),
        "钻进深度": 0,
        "回次进尺": 0,
        "岩层描述": "待填写",
        "水位深度": "",
        "钻探人员": "",
        "status": "待填写",
        "日志状态": "待填写",
        "pending": True,
        "abnormal": False,
    }
    rows.append(todo)
    return todo


class BoreholeService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        area: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [row for row in store.rows(MODULE) if not row.get("hidden")]
        text = normalize_text(keyword)
        if text:
            rows = [row for row in rows if text in normalize_text(row.get("钻孔编号")) or any(text in alias for alias in aliases_for(row))]
        if area:
            rows = [row for row in rows if area in normalize_text(row.get("勘探区"))]
        if status:
            rows = [row for row in rows if normalize_text(row.get("status")) == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [public_entry(row) for row in rows[start:start + size]], total

    def map_entries(self, area: str | None = None) -> list[dict[str, Any]]:
        items, _ = self.list_entries(area=area, page=1, size=10000)
        return items

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        if row is None:
            return None
        target_id = row.get("merged_into", row.get("id"))
        target = store.find(MODULE, int(target_id)) if target_id is not None else None
        if target is not None and target is not row:
            row = target
        return public_entry(row)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        result = self.save_entry(values)
        return (result.entry, [] if result.entry else [result.message])

    def save_entry(
        self,
        values: dict[str, Any],
        *,
        expected_version: Any = None,
        request_id: str | None = None,
    ) -> SaveResult:
        cached = store.cached_result(request_id)
        if cached is not None:
            return SaveResult(
                cached.get("entry"),
                str(cached.get("message") or ""),
                bool(cached.get("duplicated")) or bool(cached.get("duplicate_on_replay")),
                bool(cached.get("conflict")),
            )

        try:
            with store.transaction():
                cached_after_lock = store.cached_result(request_id)
                if cached_after_lock is not None:
                    return SaveResult(
                        cached_after_lock.get("entry"),
                        str(cached_after_lock.get("message") or ""),
                        bool(cached_after_lock.get("duplicated")),
                        bool(cached_after_lock.get("conflict")),
                    )
                rows = store.rows(MODULE)
                submitted_number = normalize_text(values.get("现场确认钻孔编号")) or normalize_text(values.get("钻孔编号"))
                lookup = normalize_text(values.get("钻孔编号")) or submitted_number
                submitted_aliases = normalize_aliases(values.get("钻孔别名") or values.get("别名"))
                lookup_names = {lookup, submitted_number, *submitted_aliases}
                lookup_names.discard("")

                existing = next((row for row in rows if not row.get("hidden") and lookup_names & names_for(row)), None)
                if existing is None and values.get("id") not in (None, ""):
                    candidate = store.find(MODULE, int(values["id"]))
                    if candidate is not None and not candidate.get("hidden"):
                        existing = candidate
                    elif candidate is not None and candidate.get("merged_into") is not None:
                        existing = store.find(MODULE, int(candidate["merged_into"]))

                area = normalize_text(values.get("勘探区"))
                coordinate = normalize_text(values.get("孔口坐标"))
                confirm_coordinate = bool(values.get("坐标已确认") or values.get("confirm_coordinate"))

                if existing is not None:
                    try:
                        require_version(existing, expected_version)
                    except VersionConflict as exc:
                        return SaveResult(public_entry(existing), str(exc), conflict=True)

                if existing is None:
                    missing = [name for name in REQUIRED_FIELDS if not normalize_text(values.get(name))]
                    if missing:
                        return SaveResult(None, "、".join(missing))

                    duplicate = next(
                        (row for row in rows if not row.get("hidden") and normalize_text(row.get("钻孔编号")) == submitted_number),
                        None,
                    )
                    if duplicate is not None:
                        entry = public_entry(duplicate)
                        result = SaveResult(entry, "钻孔已存在，未重复创建", True)
                        store.remember_result(request_id, {"entry": entry, "message": result.message, "duplicated": True})
                        return result

                    entry = {
                        "id": next_id(rows),
                        "钻孔编号": submitted_number,
                        "勘探区": area,
                        "孔口坐标": coordinate,
                        "钻孔别名": [alias for alias in submitted_aliases if alias != submitted_number],
                        "status": STATUS_ORDER[0],
                        "钻孔状态": STATUS_ORDER[0],
                        "pending": True,
                        "abnormal": False,
                        "version": 1,
                        "坐标已确认": confirm_coordinate,
                    }
                    rows.append(entry)
                    sync_open_logs(entry)
                    public = public_entry(entry)
                    result = SaveResult(public, "钻孔已登记")
                    store.remember_result(request_id, {"entry": public, "message": result.message, "duplicate_on_replay": True})
                    return result

                canonical = normalize_text(existing.get("钻孔编号"))
                current_names = names_for(existing)
                same_number = submitted_number in current_names
                same_area = not area or area == normalize_text(existing.get("勘探区"))
                same_coordinate = not coordinate or coordinate == normalize_text(existing.get("孔口坐标"))
                same_aliases = all(alias in aliases_for(existing) for alias in submitted_aliases)
                if same_number and same_area and same_coordinate and same_aliases:
                    public = public_entry(existing)
                    result = SaveResult(public, "钻孔已存在，未重复创建或升版", True)
                    store.remember_result(request_id, {"entry": public, "message": result.message, "duplicated": True})
                    return result

                old_canonical = canonical
                aliases = aliases_for(existing)
                if coordinate and existing.get("坐标已确认"):
                    old_coordinate = normalize_text(existing.get("孔口坐标"))
                    if old_coordinate and coordinate != old_coordinate and not confirm_coordinate:
                        return SaveResult(
                            public_entry(existing),
                            "已确认坐标不能被未确认补录覆盖",
                            conflict=True,
                        )
                if submitted_number and submitted_number != old_canonical:
                    if values.get("现场确认钻孔编号"):
                        aliases = [alias for alias in aliases if alias != submitted_number]
                        existing["钻孔编号"] = submitted_number
                        canonical = submitted_number
                    elif submitted_number not in aliases:
                        return SaveResult(public_entry(existing), "孔号应以现场确认值为准；别名不会覆盖主孔号", conflict=True)
                canonical = normalize_text(existing.get("钻孔编号"))

                if area:
                    existing["勘探区"] = area
                if coordinate:
                    existing["孔口坐标"] = coordinate
                existing["坐标已确认"] = bool(existing.get("坐标已确认") or confirm_coordinate)

                for alias in [lookup, old_canonical, *submitted_aliases]:
                    if alias and alias != canonical and alias not in aliases:
                        aliases.append(alias)
                existing["钻孔别名"] = aliases

                existing["version"] = int(existing.get("version") or 1) + 1
                synced = sync_open_logs(existing)
                public = public_entry(existing)
                result = SaveResult(public, f"补录已合并，已同步 {synced} 条钻探待办")
                store.remember_result(request_id, {"entry": public, "message": result.message})
                return result
        except (TypeError, ValueError) as exc:
            return SaveResult(None, f"补录数据格式不正确：{exc}")

    def run_action(self, entry_id: int, action: str, expected_version: Any = None) -> tuple[dict[str, Any] | None, str]:
        with store.transaction():
            row = store.find(MODULE, entry_id)
            if row is None or row.get("hidden"):
                return None, f"钻孔 {entry_id} 不存在或已归档"
            target_id = row.get("merged_into")
            if target_id is not None and int(target_id) != int(row.get("id", entry_id)):
                target = store.find(MODULE, int(target_id))
                row = target if target is not None else row
            try:
                require_version(row, expected_version)
            except VersionConflict as exc:
                return public_entry(row), str(exc)
            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于钻孔编录可执行范围"
            target = ACTION_RULES[action]
            row["status"] = target
            row["钻孔状态"] = target
            row["pending"] = target not in {STATUS_ORDER[-1], "已封孔"}
            row["abnormal"] = action in NEGATIVE_ACTIONS
            row["version"] = int(row.get("version") or 1) + 1
            if action == "开始钻进":
                ensure_open_todo(row)
            sync_open_logs(row)
            return public_entry(row), f"钻孔已{action}"
