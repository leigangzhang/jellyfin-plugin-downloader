"""`tests/backend_download/` 分组：提交闸门（`/api/submit` 载荷、rc=5 清理、单源默认）。

分组名不叫 `download`：`unittest discover -s tests` 会把 `tests/` 放进 `sys.path`，
叫 `tests/download/` 会遮蔽 `backend/download/`（tests/AGENTS.md 的规则）。
"""
