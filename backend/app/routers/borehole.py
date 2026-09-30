"""钻孔编录接口：维护钻孔，覆盖补录归并、孔位图与状态动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.borehole import BoreholeService

router = APIRouter(prefix="/api/borehole", tags=["钻孔编录"])

service = BoreholeService()

LIST_FIELDS = ["钻孔编号", "勘探区", "孔口坐标", "设计孔深", "终孔深度", "开孔日期", "终孔日期", "钻孔状态"]
STATUSES = ["待施工", "钻进中", "已终孔", "已封孔", "已废弃"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按主孔号或别名检索"),
    status: str | None = Query(default=None, description="待施工、钻进中、已终孔、已封孔、已废弃"),
    area: str | None = Query(default=None, description="按勘探区检索"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """列表只返回归并后的当前钻孔，不暴露冲突旧行。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, area=area, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/map")
def borehole_map(area: str | None = Query(default=None, description="按勘探区筛选")) -> dict[str, Any]:
    """孔位图与列表共用同一份版本化投影。"""
    items = service.map_entries(area=area)
    return {"items": items, "total": len(items), "version_field": "version"}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出钻孔编录清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "borehole", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """旧孔号详情会定位到归并后的主记录，避免继续打开一份旧详情。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"钻孔 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记或补录钻孔；别名仅参与查找，现场确认孔号才会成为主孔号。"""
    result = service.save_entry(
        payload.values,
        expected_version=payload.expected_version,
        request_id=payload.request_id,
    )
    if result.entry is None:
        return ActionResult(ok=False, message=f"缺少必填字段：{result.message}")
    return ActionResult(
        ok=not result.conflict,
        message=result.message,
        entry=result.entry,
        duplicated=result.duplicated,
        conflict=result.conflict,
        version=int(result.entry.get("version") or 1),
    )


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """动作、编录列表、孔位图和钻探待办在同一事务内使用新版本。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.expected_version)
    if entry is None:
        return ActionResult(ok=False, message=message)
    conflict = "刷新" in message or "版本" in message
    return ActionResult(
        ok=not conflict,
        message=message,
        entry=entry,
        conflict=conflict,
        version=int(entry.get("version") or 1),
    )
