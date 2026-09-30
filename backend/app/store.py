"""内存数据仓库与串行写入事务。"""
from __future__ import annotations

import copy
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.seed import SEED_ROWS
from app.services.migration import migrate_borehole_aliases


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        self._write_lock = threading.RLock()
        self._transaction_depth = 0
        self._transaction_snapshot: dict[str, list[dict[str, Any]]] | None = None
        self._request_results: dict[str, dict[str, Any]] = {}
        migrate_borehole_aliases(self._tables)

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """串行化所有写入；最外层事务异常时回滚本事务已经修改的内存表。"""
        with self._write_lock:
            is_outer = self._transaction_depth == 0
            if is_outer:
                self._transaction_snapshot = copy.deepcopy(self._tables)
            self._transaction_depth += 1
            try:
                yield
            except Exception:
                if is_outer and self._transaction_snapshot is not None:
                    self._tables = copy.deepcopy(self._transaction_snapshot)
                raise
            finally:
                self._transaction_depth -= 1
                if is_outer:
                    self._transaction_snapshot = None

    def cached_result(self, request_id: str | None) -> dict[str, Any] | None:
        if not request_id:
            return None
        with self._write_lock:
            result = self._request_results.get(request_id)
            return copy.deepcopy(result) if result else None

    def remember_result(self, request_id: str | None, result: dict[str, Any]) -> None:
        if not request_id:
            return
        with self._write_lock:
            self._request_results[request_id] = copy.deepcopy(result)

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
