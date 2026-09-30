"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。

补录归并改造后，仓库额外提供两样东西：

- ``transaction()``：全局写锁 + 快照回滚。钻孔补录、归并、动作回写都要在一个
  事务里完成，任一步失败整体回滚，已确认坐标等字段不会被半成品写入覆盖。
- 幂等令牌（``replay_token`` / ``remember_token``）：同一份补录单网络重试时直接
  回放首次结果，不允许生成第二个孔。令牌状态同样在事务快照里，失败一并回滚。
"""
from __future__ import annotations

import copy
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 进程内所有写操作共用一把可重入锁：FastAPI 的同步接口跑在线程池里，
        # 并发补录同一孔时靠它把"判定-写入"压成一个临界区。
        self._lock = threading.RLock()
        self._tokens: dict[str, dict[str, Any]] = {}

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
        """在写锁内执行一段业务，异常时把全部表与令牌快照整体回滚。"""
        with self._lock:
            tables_snapshot = copy.deepcopy(self._tables)
            tokens_snapshot = copy.deepcopy(self._tokens)
            try:
                yield
            except BaseException:
                self._tables = tables_snapshot
                self._tokens = tokens_snapshot
                raise

    def replay_token(self, token: str) -> dict[str, Any] | None:
        """取同一补录单的首次结果；没见过返回 None。"""
        return self._tokens.get(token)

    def remember_token(self, token: str, result: dict[str, Any]) -> None:
        self._tokens[token] = result

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
