"""钻孔编录业务规则：单一事实源、补录归并、版本锁与三处回写都收在这里。

口径约定（列表、详情、孔位图、钻探日志待办共用同一版本）：

- 钻孔编号以现场确认值（``钻孔编号``）为准，是唯一的规范孔号；``别名`` 只用于
  查找匹配，永远不会回写到任何列表或日志里顶替规范孔号。
- 每条存活钻孔带一个单调递增的 ``version``。补录、动作、归并成功才推进版本；
  判定失败（版本号对不上、字段校验不过）整体事务回滚，旧版本原样保留。
- 存量冲突记录按"当时孔号"归并：被吸收记录进入 ``merged_into`` 归档，挂到它名下
  的钻探日志待办按迁移时的规范孔号重新指向存活孔；已经离开待办的历史日志不动，
  保留当时写下的孔号快照，绝不追溯改写。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "borehole"
DRILLING_LOG_MODULE = "drilling_log"
REQUIRED_FIELDS = ["钻孔编号", "勘探区", "孔口坐标"]
# 补录时允许顺带补齐的非关键业务字段
SUPPLEMENT_FIELDS = ["设计孔深", "终孔深度", "开孔日期", "终孔日期"]
STATUS_ORDER = ["待施工", "钻进中", "已终孔", "已封孔", "已废弃"]
ACTION_RULES = {"开始钻进": "钻进中", "登记终孔": "已终孔", "执行封孔": "已封孔"}
NEGATIVE_ACTIONS = []

# 钻探日志里这些状态仍属于"待办"，归并和版本推进时需要跟随当前孔；
# 其外状态的日志已经是历史记录，孔号字段作为快照永不追溯改写。
TODO_LOG_STATUSES = {"待填写", "退回补充"}

CANONICAL_FIELD = "钻孔编号"
ALIASES_FIELD = "别名"
VERSION_FIELD = "version"
MERGED_FIELD = "merged_into"
COORD_CONFIRMED_FIELD = "坐标已确认"
CODE_CONFIRMED_FIELD = "孔号已确认"


class VersionConflictError(Exception):
    """客户端带着过期版本提交时抛出，路由层翻译成 409。"""


class BoreholeService:
    def __init__(self) -> None:
        # 模块第一次被使用时，把示例数据补成带版本/别名的规范记录，
        # 并把完全同孔号的存量重复记录静默归并，处理线全程只有这一套数据。
        with store.transaction():
            self._normalize_meta()
            self._reconcile_duplicates()

    # ---------------------------------------------------------------- 读取

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._live_rows()
        if keyword:
            key = norm_code(keyword)
            rows = [
                row for row in rows
                if key in norm_code(row.get(CANONICAL_FIELD))
                or any(key in norm_code(alias) for alias in self._aliases(row))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [public_entry(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        """读详情：若该记录已在归并中被吸收，直接落到存活孔，并说明归并来源。"""
        target = store.find(MODULE, entry_id)
        if target is None:
            return None
        merged_into = target.get(MERGED_FIELD)
        if merged_into:
            survivor = store.find(MODULE, int(merged_into))
            if survivor is None:
                return None
            detail = public_entry(survivor)
            detail["归并自"] = sorted(self._merged_ids(survivor) | {entry_id})
            detail["原孔号"] = target.get(CANONICAL_FIELD)
            return detail
        detail = public_entry(target)
        detail["归并自"] = sorted(self._merged_ids(target))
        return detail

    def map_points(self) -> list[dict[str, Any]]:
        """孔位图与列表同读一份存活记录，坐标按同一版本摆放。"""
        points: list[dict[str, Any]] = []
        for row in self._live_rows():
            x, y = parse_coordinates(row.get("孔口坐标"))
            points.append({
                "id": row["id"],
                "钻孔编号": row.get(CANONICAL_FIELD),
                "勘探区": row.get("勘探区"),
                "孔口坐标": row.get("孔口坐标"),
                "x": x,
                "y": y,
                "status": row.get("status"),
                VERSION_FIELD: row.get(VERSION_FIELD, 1),
            })
        return points

    # ---------------------------------------------------------------- 补录

    def supplement_entry(
        self,
        values: dict[str, Any],
        *,
        token: str | None = None,
    ) -> tuple[dict[str, Any] | None, list[str], str | None]:
        """补录登记：命中既有孔就补齐字段，没命中才新建。

        返回 (记录, 缺失字段, 冲突说明)。同一 ``token`` 的重试直接回放首次结果，
        不允许重复建孔。
        """
        token = (token or "").strip() or None
        with store.transaction():
            if token is not None:
                replayed = store.replay_token(token)
                if replayed is not None:
                    return dict(replayed["entry"]), [], None

            code = str(values.get(CANONICAL_FIELD) or "").strip()
            area = str(values.get("勘探区") or "").strip()
            coord = str(values.get("孔口坐标") or "").strip()
            missing = [
                name for name, value in (
                    ("钻孔编号", code), ("勘探区", area), ("孔口坐标", coord),
                )
                if not value
            ]
            if missing:
                return None, missing, None

            expected_version = parse_version(values.get("expected_version"))
            target_id = parse_version(values.get("id"))
            if target_id is not None:
                # 从列表/详情对某条钻孔发起补录：按 id 命中；旧记录已归并时落到存活孔。
                direct = store.find(MODULE, target_id)
                if direct is None:
                    return None, [], f"钻孔 {target_id} 不存在或已归档"
                merged_into = direct.get(MERGED_FIELD)
                target = (
                    store.find(MODULE, int(merged_into))
                    if merged_into else direct
                )
                if target is None:
                    return None, [], f"钻孔 {target_id} 的归并目标已不存在"
            else:
                # 登记台补录：只按现场孔号或既有别名查找，查不到才新建。
                target = self._find_by_code_or_alias(code)

            if target is not None:
                if expected_version is not None and int(target.get(VERSION_FIELD, 1)) != expected_version:
                    conflict = (
                        f"钻孔已被其他补录更新到第 {target.get(VERSION_FIELD, 1)} 版，"
                        f"请按最新孔号 {target.get(CANONICAL_FIELD)} 重新确认后再提交"
                    )
                    raise VersionConflictError(conflict)
                changed = self._apply_supplement(target, code, area, coord, values)
                entry = public_entry(target)
            else:
                entry = self._create_entry(code, area, coord, values)
                changed = True

            result = {"entry": dict(entry), "changed": changed}
            if token is not None:
                store.remember_token(token, result)
            return entry, [], None

    def _create_entry(
        self,
        code: str,
        area: str,
        coord: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {
            "id": max(
                (int(row.get("id", 0)) for row in rows),
                default=0,
            ) + 1,
            CANONICAL_FIELD: code,
            "勘探区": area,
            "孔口坐标": coord,
            "status": STATUS_ORDER[0],
            "pending": True,
            "abnormal": False,
            ALIASES_FIELD: [],
            VERSION_FIELD: 1,
            CODE_CONFIRMED_FIELD: True,
            COORD_CONFIRMED_FIELD: True,
        }
        for field in SUPPLEMENT_FIELDS:
            value = str(values.get(field) or "").strip()
            if value:
                entry[field] = value
        rows.append(entry)
        return public_entry(entry)

    def _apply_supplement(
        self,
        entry: dict[str, Any],
        code: str,
        area: str,
        coord: str,
        values: dict[str, Any],
    ) -> bool:
        """把补录内容补到既有孔上，返回是否真的发生了变化（决定版本是否推进）。"""
        changed = False
        aliases = self._aliases(entry)
        code_confirmed = is_truthy(values.get(CODE_CONFIRMED_FIELD))
        same_code = norm_code(entry.get(CANONICAL_FIELD)) == norm_code(code)
        known_alias = norm_code(code) in {norm_code(alias) for alias in aliases}

        if not same_code:
            # 新写法要不要提升为规范孔号：现场明确确认，或原规范孔号尚未确认。
            promote = code_confirmed or not entry.get(CODE_CONFIRMED_FIELD)
            if known_alias and code_confirmed:
                # 现场确认"别名其实就是正号"：提升为规范孔号，旧正号退为别名。
                promote = True
            if not promote:
                # 现场已确认的规范孔号不被改写：新写法只进别名，供之后查找。
                if code not in aliases:
                    aliases.append(code)
                    entry[ALIASES_FIELD] = aliases
                    changed = True
            else:
                # 以本次现场确认值为规范孔号，旧孔号退为别名；
                # 被提升的写法本身从别名里摘掉，避免正号和别名重复。
                aliases = [
                    alias for alias in aliases
                    if norm_code(alias) != norm_code(code)
                ]
                old_code = str(entry.get(CANONICAL_FIELD) or "").strip()
                if old_code and old_code not in aliases and norm_code(old_code) != norm_code(code):
                    aliases.append(old_code)
                entry[CANONICAL_FIELD] = code
                entry[ALIASES_FIELD] = aliases
                entry[CODE_CONFIRMED_FIELD] = True
                changed = True
        elif code_confirmed and not entry.get(CODE_CONFIRMED_FIELD):
            entry[CODE_CONFIRMED_FIELD] = True
            changed = True

        coord_confirmed = is_truthy(values.get(COORD_CONFIRMED_FIELD))
        if norm_text(coord) == norm_text(entry.get("孔口坐标")):
            if coord_confirmed and not entry.get(COORD_CONFIRMED_FIELD):
                entry[COORD_CONFIRMED_FIELD] = True
                changed = True
        elif entry.get(COORD_CONFIRMED_FIELD):
            # 已确认坐标不允许被补录覆盖；新坐标与现坐标不一致时直接拒绝，
            # 事务回滚保证未成功的更新不会落库。
            raise ValueError(
                f"钻孔 {entry.get(CANONICAL_FIELD)} 的孔口坐标已经现场确认，"
                "如需更正请走坐标变更流程"
            )
        else:
            entry["孔口坐标"] = coord
            entry[COORD_CONFIRMED_FIELD] = True
            changed = True

        if area and norm_text(area) != norm_text(entry.get("勘探区")):
            entry["勘探区"] = area
            changed = True
        for field in SUPPLEMENT_FIELDS:
            value = str(values.get(field) or "").strip()
            if value and norm_text(value) != norm_text(entry.get(field)):
                entry[field] = value
                changed = True

        if changed:
            # 提升正号/补别名过程中可能引入与正号同名或互相重复的写法，统一去重。
            deduped = [
                alias for alias in self._aliases(entry)
                if norm_code(alias) != norm_code(entry.get(CANONICAL_FIELD))
            ]
            entry[ALIASES_FIELD] = list(dict.fromkeys(deduped))
            entry[VERSION_FIELD] = int(entry.get(VERSION_FIELD, 1)) + 1
            self._sync_todo_refs(entry)
        return changed

    # ---------------------------------------------------------------- 动作

    def run_action(
        self,
        entry_id: int,
        action: str,
        *,
        expected_version: int | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        with store.transaction():
            entry = self._resolve_live(entry_id)
            if entry is None:
                return None, f"钻孔 {entry_id} 不存在或已归档"
            if expected_version is not None and int(entry.get(VERSION_FIELD, 1)) != expected_version:
                raise VersionConflictError(
                    f"钻孔已更新到第 {entry.get(VERSION_FIELD, 1)} 版，请刷新后再执行动作"
                )
            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于钻孔编录可执行范围"
            target = ACTION_RULES[action]
            if target not in STATUS_ORDER:
                return None, f"目标状态「{target}」不在允许的状态序列里"
            entry["status"] = target
            entry["pending"] = target != STATUS_ORDER[-1]
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            # 动作推进一个版本，编录列表、孔位图、钻探日志待办随之整体挪到新版本。
            entry[VERSION_FIELD] = int(entry.get(VERSION_FIELD, 1)) + 1
            self._ensure_todo_for(entry)
            self._sync_todo_refs(entry)
            return public_entry(entry), f"钻孔已{action}"

    # ---------------------------------------------------------------- 归并

    def merge_entries(
        self,
        survivor_id: int,
        victim_ids: list[int],
        *,
        confirmed_code: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """按当时孔号把冲突的存量记录归并到一个存活孔。

        被吸收记录只做归档；挂在它们名下的钻探日志待办按迁移时孔号搬到存活孔；
        已经不是待办的历史日志保持原样。
        """
        victim_ids = [int(vid) for vid in victim_ids if int(vid) != int(survivor_id)]
        if not victim_ids:
            return None, "未指定需要归并的冲突钻孔"
        with store.transaction():
            survivor = self._resolve_live(survivor_id)
            if survivor is None:
                return None, f"钻孔 {survivor_id} 不存在或已归档"
            victims: list[dict[str, Any]] = []
            for vid in victim_ids:
                victim = self._resolve_live(vid)
                if victim is None:
                    return None, f"钻孔 {vid} 不存在或已归并，无法重复迁移"
                if victim is survivor:
                    continue
                victims.append(victim)

            confirmed_code = (confirmed_code or "").strip()
            if confirmed_code and norm_code(confirmed_code) != norm_code(survivor.get(CANONICAL_FIELD)):
                # 现场确认值是新的规范孔号：存活孔旧孔号退为别名。
                aliases = self._aliases(survivor)
                old_code = str(survivor.get(CANONICAL_FIELD) or "").strip()
                if old_code and old_code not in aliases:
                    aliases.append(old_code)
                survivor[CANONICAL_FIELD] = confirmed_code
                survivor[ALIASES_FIELD] = aliases
                survivor[CODE_CONFIRMED_FIELD] = True

            for victim in victims:
                self._absorb(survivor, victim)

            # 现场确认值可能与某个被吸收孔号同名，归并后统一去重。
            deduped = [
                alias for alias in self._aliases(survivor)
                if norm_code(alias) != norm_code(survivor.get(CANONICAL_FIELD))
            ]
            survivor[ALIASES_FIELD] = list(dict.fromkeys(deduped))

            survivor[VERSION_FIELD] = int(survivor.get(VERSION_FIELD, 1)) + 1
            self._sync_todo_refs(survivor)
            detail = public_entry(survivor)
            detail["归并自"] = sorted(self._merged_ids(survivor))
            return detail, "冲突钻孔已按现场孔号归并，钻探日志待办已同步迁移"

    def _absorb(self, survivor: dict[str, Any], victim: dict[str, Any]) -> None:
        """把一条冲突记录并入存活孔：补字段、收别名、迁待办、做归档。"""
        aliases = self._aliases(survivor)
        victim_code = str(victim.get(CANONICAL_FIELD) or "").strip()
        if victim_code and victim_code not in aliases and norm_code(victim_code) != norm_code(survivor.get(CANONICAL_FIELD)):
            aliases.append(victim_code)
        for alias in self._aliases(victim):
            if alias not in aliases and norm_code(alias) != norm_code(survivor.get(CANONICAL_FIELD)):
                aliases.append(alias)
        survivor[ALIASES_FIELD] = aliases

        # 被归并孔上更完整的业务字段补到存活孔；已确认坐标不被任何存量值覆盖。
        for field in ["勘探区", "孔口坐标", *SUPPLEMENT_FIELDS]:
            value = victim.get(field)
            if not value:
                continue
            if field == "孔口坐标" and survivor.get(COORD_CONFIRMED_FIELD):
                continue
            if not survivor.get(field):
                survivor[field] = value

        self._migrate_todos(survivor, victim)

        merged_ids = self._merged_ids(survivor)
        merged_ids.update(self._merged_ids(victim))
        merged_ids.add(int(victim["id"]))
        victim[MERGED_FIELD] = int(survivor["id"])
        victim["pending"] = False
        survivor["_merged_ids"] = sorted(merged_ids)

    def _reconcile_duplicates(self) -> None:
        """处理线入口：把完全同规范孔号的存量重复记录归并成一条。"""
        by_code: dict[str, dict[str, Any]] = {}
        for row in list(store.rows(MODULE)):
            if row.get(MERGED_FIELD):
                continue
            self._normalize_row(row)
            key = norm_code(row.get(CANONICAL_FIELD))
            if key in by_code:
                self._absorb(by_code[key], row)
                survivor = by_code[key]
                survivor[VERSION_FIELD] = int(survivor.get(VERSION_FIELD, 1)) + 1
                self._sync_todo_refs(survivor)
            else:
                by_code[key] = row

    def _normalize_meta(self) -> None:
        for row in store.rows(MODULE):
            self._normalize_row(row)

    def _normalize_row(self, row: dict[str, Any]) -> None:
        if not isinstance(row.get(ALIASES_FIELD), list):
            row[ALIASES_FIELD] = []
        if VERSION_FIELD not in row:
            row[VERSION_FIELD] = 1
        if MERGED_FIELD not in row:
            row[MERGED_FIELD] = None
        if COORD_CONFIRMED_FIELD not in row:
            # 示例数据坐标未经现场确认，允许首次补录直接纠正；之后再改要走流程。
            row[COORD_CONFIRMED_FIELD] = False
        if CODE_CONFIRMED_FIELD not in row:
            row[CODE_CONFIRMED_FIELD] = False

    # ---------------------------------------------------------------- 钻探日志待办

    def _todo_rows(self) -> list[dict[str, Any]]:
        return [
            row for row in store.rows(DRILLING_LOG_MODULE)
            if row.get("status") in TODO_LOG_STATUSES
        ]

    def _ensure_todo_for(self, entry: dict[str, Any]) -> None:
        """开始钻进后若该孔还没有待办日志，补一条待办；已有则不重复生成。"""
        canonical = str(entry.get(CANONICAL_FIELD) or "")
        for row in self._todo_rows():
            linked_id = row.get("钻孔id")
            if (linked_id is not None and int(linked_id) == int(entry["id"])):
                return
            if linked_id is None and norm_code(row.get("钻孔编号")) == norm_code(canonical):
                row["钻孔id"] = entry["id"]
                return
        rows = store.rows(DRILLING_LOG_MODULE)
        next_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        rows.append({
            "id": next_id,
            "status": "待填写",
            "pending": True,
            "abnormal": False,
            "日志编号": f"DRIL-TODO-{next_id:04d}",
            "钻孔编号": canonical,
            "钻孔id": entry["id"],
            "钻孔版本": int(entry.get(VERSION_FIELD, 1)),
            "钻进深度": "",
            "回次进尺": "",
            "岩层描述": "",
            "水位深度": "",
            "钻探人员": "",
            "日志状态": "待填写",
        })

    def _migrate_todos(self, survivor: dict[str, Any], victim: dict[str, Any]) -> None:
        """归并迁移：挂在被吸收孔（含其旧孔号/别名）下的待办搬到存活孔。"""
        victim_codes = {norm_code(victim.get(CANONICAL_FIELD))}
        victim_codes.update(norm_code(alias) for alias in self._aliases(victim))
        canonical = str(survivor.get(CANONICAL_FIELD) or "")
        for row in self._todo_rows():
            linked_id = row.get("钻孔id")
            hit = (
                (linked_id is not None and int(linked_id) == int(victim["id"]))
                or (linked_id is None and norm_code(row.get("钻孔编号")) in victim_codes)
            )
            if hit:
                row["钻孔id"] = survivor["id"]
                row["钻孔编号"] = canonical

    def _sync_todo_refs(self, entry: dict[str, Any]) -> None:
        """把属于该孔的待办统一摆到当前规范孔号与版本；历史日志不在此列。"""
        canonical = str(entry.get(CANONICAL_FIELD) or "")
        version = int(entry.get(VERSION_FIELD, 1))
        codes = {norm_code(canonical)}
        codes.update(norm_code(alias) for alias in self._aliases(entry))
        for row in self._todo_rows():
            linked_id = row.get("钻孔id")
            belongs = (
                (linked_id is not None and int(linked_id) == int(entry["id"]))
                or (
                    linked_id is None
                    and norm_code(row.get("钻孔编号")) in codes
                )
            )
            if not belongs:
                continue
            row["钻孔id"] = entry["id"]
            row["钻孔编号"] = canonical
            row["钻孔版本"] = version

    # ---------------------------------------------------------------- 工具

    def _live_rows(self) -> list[dict[str, Any]]:
        return [
            row for row in store.rows(MODULE)
            if not row.get(MERGED_FIELD)
        ]

    def _resolve_live(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        if row is None or row.get(MERGED_FIELD):
            return None
        return row

    def _find_by_code_or_alias(self, code: str) -> dict[str, Any] | None:
        """别名只用于查找：规范孔号或任一别名命中都视为同一孔。"""
        target = norm_code(code)
        for row in self._live_rows():
            if norm_code(row.get(CANONICAL_FIELD)) == target:
                return row
        for row in self._live_rows():
            if target in {norm_code(alias) for alias in self._aliases(row)}:
                return row
        return None

    @staticmethod
    def _aliases(row: dict[str, Any]) -> list[str]:
        aliases = row.get(ALIASES_FIELD)
        if not isinstance(aliases, list):
            aliases = []
            row[ALIASES_FIELD] = aliases
        return aliases

    @staticmethod
    def _merged_ids(row: dict[str, Any]) -> set[int]:
        ids = row.get("_merged_ids")
        return {int(value) for value in ids} if isinstance(ids, list) else set()


def norm_code(value: Any) -> str:
    """孔号匹配口径：去首尾空白、压缩内部空白、统一大写。"""
    return " ".join(str(value or "").strip().upper().split())


def norm_text(value: Any) -> str:
    return str(value or "").strip()


def parse_version(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_truthy(value: Any) -> bool:
    """解析前端复选框/字符串形式的确认标记。"""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "是", "确认"}


def public_entry(row: dict[str, Any]) -> dict[str, Any]:
    """剥掉下划线开头的内部记账字段后再对外返回。"""
    return {key: value for key, value in row.items() if not str(key).startswith("_")}


def parse_coordinates(raw: Any) -> tuple[float | None, float | None]:
    """把 'X,Y' / 'X，Y' 形式的孔口坐标解析成数值；解析不出时由前端按表格摆放。"""
    text = str(raw or "").replace("，", ",").replace("；", ",").replace(";", ",")
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) < 2:
        return None, None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None, None
