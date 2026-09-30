"""钻孔编录接口：维护钻孔，覆盖补录归并、状态动作、孔位图与冲突归并。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.borehole import BoreholeService, VersionConflictError, parse_version

router = APIRouter(prefix="/api/borehole", tags=["钻孔编录"])

service = BoreholeService()

LIST_FIELDS = ["钻孔编号", "勘探区", "孔口坐标", "设计孔深", "终孔深度", "开孔日期", "终孔日期", "钻孔状态"]
STATUSES = ["待施工", "钻进中", "已终孔", "已封孔", "已废弃"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按钻孔编号或别名检索"),
    status: str | None = Query(default=None, description="待施工、钻进中、已终孔、已封孔、已废弃"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按钻孔编号与状态过滤钻孔编录列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/map")
def borehole_map() -> dict[str, Any]:
    """孔位图数据：与编录列表读同一份存活钻孔，保证三处摆放同一版本。"""
    return {"items": service.map_points()}


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出钻孔编录清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "borehole", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条钻孔明细；已归并的旧孔号会落到存活孔，不再打开旧详情。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"钻孔 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def supplement_entry(payload: EntryPayload) -> ActionResult:
    """补录钻孔：按现场确认孔号命中既有孔就补齐，否则才新建。

    同一条补录单带相同 token 重试时回放首次结果，不允许生成重复孔；
    带着过期版本提交会返回 409，要求按最新数据重新确认。
    """
    values = dict(payload.values)
    token = values.pop("token", None) or payload.remark
    try:
        entry, missing, conflict = service.supplement_entry(values, token=str(token) if token else None)
    except VersionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        # 已确认坐标拒绝覆盖等业务拒绝：事务已回滚，现有数据原样保留。
        return ActionResult(ok=False, message=str(error))
    if conflict:
        raise HTTPException(status_code=409, detail=conflict)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="补录已合并到当前钻孔版本", entry=entry)


@router.post("/merge", response_model=ActionResult)
def merge_entries(payload: EntryPayload) -> ActionResult:
    """把冲突的存量钻孔按当时孔号归并到一个存活孔，待办同步迁移、历史日志不动。"""
    values = payload.values
    try:
        survivor_id = int(values.get("survivor_id"))
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="缺少有效的 survivor_id（保留孔）")
    raw_victims = values.get("victim_ids") or []
    if not isinstance(raw_victims, list):
        return ActionResult(ok=False, message="victim_ids 必须是待归并钻孔编号数组")
    try:
        victim_ids = [int(value) for value in raw_victims]
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="victim_ids 中存在无效钻孔编号")
    confirmed_code = values.get("confirmed_code")
    try:
        entry, message = service.merge_entries(
            survivor_id,
            victim_ids,
            confirmed_code=str(confirmed_code) if confirmed_code else None,
        )
    except VersionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条钻孔执行开始钻进、登记终孔、执行封孔；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    expected_version = parse_version(payload.values.get("expected_version"))
    try:
        entry, message = service.run_action(
            entry_id,
            action,
            expected_version=expected_version,
        )
    except VersionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
