"""热门榜单后端包：TMDb 取源 / TTL 缓存 / 关注清单。

三个模块各管一件事，互相只见接口：

* `trending_source` 只碰 HTTP（可注入 opener），返回值永远结构化、不抛异常；
* `trending_cache` 只碰磁盘与 TTL，键为 (language, 榜单 id 列表)，取不到时回退上次结果；
* `trending_watchlist` 只碰自己那份 JSON，按 tmdb_id 去重。

再导出只是给调用方（`console_server/http_api.py` 的两条热门路由）一个入口。
数据根一律取 `media_download_lib.DATA_ROOT`（`JMD_DATA_DIR` 可覆盖），包内不拼
任何机器相关路径。
"""

from .trending_cache import CACHE_TTL_SECS, cache_path, load_lists
from .trending_source import (
    DEFAULT_LANGUAGE,
    LISTS,
    LIST_ORDER,
    fetch_list,
    fetch_lists,
    normalize_item,
)
from .trending_watchlist import (
    add_watch,
    read_watchlist,
    watchlist_ids,
    watchlist_path,
)

__all__ = [
    "CACHE_TTL_SECS",
    "DEFAULT_LANGUAGE",
    "LISTS",
    "LIST_ORDER",
    "add_watch",
    "cache_path",
    "fetch_list",
    "fetch_lists",
    "load_lists",
    "normalize_item",
    "read_watchlist",
    "watchlist_ids",
    "watchlist_path",
]
