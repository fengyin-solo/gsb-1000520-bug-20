"""钻孔合并、版本锁、事务与历史日志保护的内存服务测试。"""
from __future__ import annotations

import copy
import importlib
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_services() -> tuple[object, object]:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]

    seed = types.ModuleType("app.seed")
    seed.SEED_ROWS = {
        "borehole": [
            {
                "id": 1,
                "status": "钻进中",
                "pending": True,
                "钻孔编号": "OLD-1",
                "勘探区": "北区",
                "孔口坐标": "10,20",
                "钻孔别名": ["FIELD-1"],
            },
            {
                "id": 2,
                "status": "待施工",
                "pending": True,
                "钻孔编号": "FIELD-1",
                "勘探区": "北区",
                "孔口坐标": "11,21",
                "坐标已确认": True,
                "现场确认钻孔编号": "FIELD-1",
            },
        ],
        "drilling_log": [
            {
                "id": 1,
                "status": "待填写",
                "pending": True,
                "日志编号": "TODO-1",
                "钻孔编号": "OLD-1",
                "钻进深度": 0,
            },
            {
                "id": 2,
                "status": "已审核",
                "pending": False,
                "日志编号": "HIST-1",
                "钻孔编号": "OLD-1",
                "钻进深度": 18,
            },
        ],
    }
    sys.modules["app.seed"] = seed
    importlib.import_module("app")
    store_module = importlib.import_module("app.store")
    borehole_module = importlib.import_module("app.services.borehole")
    log_module = importlib.import_module("app.services.drilling_log")
    store_module.store._tables = copy.deepcopy(seed.SEED_ROWS)
    store_module.store._request_results = {}
    borehole_module.store._tables = store_module.store._tables
    log_module.store._tables = store_module.store._tables
    from app.services.migration import migrate_borehole_aliases
    migrate_borehole_aliases(store_module.store._tables)
    return borehole_module.BoreholeService(), log_module.DrillingLogService()


def test_existing_conflicting_boreholes_merge_to_one_current_version() -> None:
    boreholes, logs = load_services()
    from app.store import store

    rows, total = boreholes.list_entries()
    assert total == 1
    assert rows[0]["钻孔编号"] == "FIELD-1"
    assert rows[0]["孔口坐标"] == "11,21"
    assert "OLD-1" in rows[0]["钻孔别名"]

    old_detail = boreholes.get_entry(1)
    assert old_detail is not None and old_detail["id"] == rows[0]["id"] == 2
    assert store.find("borehole", 1)["merged_into"] == 2

    todo = logs.get_entry(1)
    history = logs.get_entry(2)
    assert todo["钻孔编号"] == "FIELD-1"
    assert todo["钻孔版本"] == 1
    assert history["钻孔编号"] == "OLD-1"
    assert "钻孔版本" not in history


def test_alias_only_finds_record_and_confirmed_number_becomes_canonical() -> None:
    boreholes, logs = load_services()
    result = boreholes.save_entry(
        {
            "钻孔编号": "OLD-1",
            "现场确认钻孔编号": "FIELD-1",
            "勘探区": "北区A",
            "孔口坐标": "12,22",
            "坐标已确认": True,
        },
        expected_version=1,
    )
    assert result.entry is not None
    assert result.entry["钻孔编号"] == "FIELD-1"
    assert result.entry["勘探区"] == "北区A"
    assert "OLD-1" in result.entry["钻孔别名"]
    assert result.entry["version"] == 2


def test_stale_version_and_unconfirmed_coordinate_cannot_overwrite() -> None:
    boreholes, _ = load_services()
    boreholes.save_entry(
        {
            "钻孔编号": "FIELD-1",
            "勘探区": "北区",
            "孔口坐标": "12,22",
            "坐标已确认": True,
        },
        expected_version=1,
    )
    stale = boreholes.save_entry(
        {"钻孔编号": "FIELD-1", "勘探区": "北区", "孔口坐标": "99,99"},
        expected_version=1,
    )
    assert stale.conflict is True
    assert stale.entry is not None
    assert stale.entry["孔口坐标"] == "12,22"
    assert stale.entry["version"] == 2

    unsafe = boreholes.save_entry(
        {"钻孔编号": "FIELD-1", "孔口坐标": "100,100"},
        expected_version=2,
    )
    assert unsafe.conflict is True
    assert unsafe.entry is not None
    assert unsafe.entry["孔口坐标"] == "12,22"
    assert unsafe.entry["version"] == 2


def test_action_updates_list_map_and_open_todo_but_keeps_history() -> None:
    boreholes, logs = load_services()
    entry, message = boreholes.run_action(2, "开始钻进", 1)
    assert entry is not None and "已开始钻进" in message
    assert entry["version"] == 2

    listed, _ = boreholes.list_entries()
    mapped = boreholes.map_entries()
    assert listed[0]["version"] == mapped[0]["version"] == 2

    todos, total = logs.list_entries(pending=True)
    assert total == 1
    assert todos[0]["钻孔编号"] == "FIELD-1"
    assert todos[0]["钻孔版本"] == 2
    assert logs.get_entry(2)["钻孔编号"] == "OLD-1"


def test_concurrent_creation_serializes_and_resubmission_does_not_duplicate() -> None:
    boreholes, _ = load_services()
    payload = {"钻孔编号": "ZK-CONC", "勘探区": "南区", "孔口坐标": "30,40"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: boreholes.save_entry(payload, request_id="same-request"), range(2)))
    assert all(result.entry is not None for result in results)
    assert {result.entry["id"] for result in results} == {3}
    rows, total = boreholes.list_entries()
    assert total == 2
    assert len([row for row in rows if row["钻孔编号"] == "ZK-CONC"]) == 1
    assert results[1].duplicated is True


def test_failed_transaction_does_not_change_confirmed_coordinate() -> None:
    boreholes, _ = load_services()
    original = boreholes.get_entry(2)
    assert original is not None
    result = boreholes.save_entry(
        {"钻孔编号": "FIELD-1", "勘探区": "被失败更新改动", "孔口坐标": "100,100"},
        expected_version=1,
    )
    assert result.conflict is True
    current = boreholes.get_entry(2)
    assert current is not None
    assert current["勘探区"] == "北区"
    assert current["孔口坐标"] == "11,21"
    assert current["version"] == 1


if __name__ == "__main__":
    tests = [
        test_existing_conflicting_boreholes_merge_to_one_current_version,
        test_alias_only_finds_record_and_confirmed_number_becomes_canonical,
        test_stale_version_and_unconfirmed_coordinate_cannot_overwrite,
        test_action_updates_list_map_and_open_todo_but_keeps_history,
        test_concurrent_creation_serializes_and_resubmission_does_not_duplicate,
        test_failed_transaction_does_not_change_confirmed_coordinate,
    ]
    for test in tests:
        test()
    print(f"ok - {len(tests)} borehole consistency tests")
