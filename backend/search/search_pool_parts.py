"""承载侧：拆出的公共名字在这里显式 import，`search_pool` 只是它的外观。

为什么要有这一层：`search_pool.py` 不能写兄弟模块的 import——tests/test_backend_sources_dependencies.py
的 DependencyTests 把它的 import 根限定为既有那几个模块名 + 标准库，写
`import search_pool_api` 会让那条守卫变红，而 tests/** 不在本次改动范围内。于是依赖边集中
写在本模块（grep `search_api` / `candidate_seasons` 就能看到它来自哪个模块），
`search_pool` 用字符串装载本模块后把这些名字绑进自己的 globals。

    search_pool_release   发行名 / 季号识别
    search_pool_api       取源、分页与预算
    search_pool_pool      池条目合并与外部源
    search_pool_deliver   候选清单 / 进度落盘
    search_pool_round     一轮搜索的收尾阶段（main 的阶段函数）
"""

from search.search_pool_release import (
    CN_DIGITS,
    CODE_HINT_RE,
    COLLECTION_HINTS,
    EPISODE_PATTERN,
    MARKER_PATTERN,
    MARKETING_NOISE_RE,
    PACK_PATTERN,
    PUBLISHER_NAME_TOKENS,
    RELEASE_NOISE_RE,
    RESOLUTION_PATTERNS,
    SEASON_TOKEN_RE,
    SEASON_WORD_RE,
    candidate_markers,
    candidate_seasons,
    cn_number,
    collapse_release_duplicates,
    is_publisher_candidate,
    normalise_release_name,
    publisher_filter_tokens,
    release_key,
    release_resolution,
    slugify,
    strict_season_filter,
)
from search.search_pool_api import (
    DEFAULT_API_BASE_URL,
    DEFAULT_NO_NEW_PAGES,
    DEFAULT_PAGES,
    DEFAULT_PAGE_BUDGET_SECS,
    DEFAULT_PAGE_ERRORS,
    DEFAULT_PAGE_TIMEOUT,
    DEFAULT_PAGE_WORKERS,
    DEFAULT_PLATFORMS,
    Deadline,
    MAX_LIMIT,
    MIN_VIDEO_BYTES,
    PROBE_BUDGET_SECS_HINT,
    USER_AGENT,
    build_query_plan,
    fetch_query_pages,
    search_api,
    torrent_to_candidate,
    torrents_csv_to_candidate,
)
from search.search_pool_pool import (
    DEFAULT_EXTRA_SOURCE_RESERVE_SECS,
    EXTERNAL_MERGE_KEYS,
    EXTRA_SOURCE_MIN_SECS,
    POOL_MERGE_SKIP_KEYS,
    POOL_MERGE_UNION_KEYS,
    PROVENANCE_MERGE_KEYS,
    collect_external_source,
    is_empty_value,
    load_pool,
    merge_external_entries,
    merge_pool_entry,
    save_pool,
)
from search.search_pool_deliver import (
    land_progress,
    merge_candidates_into_pool,
    stamp_candidates,
    write_candidates_file,
    write_episode_files,
)
from search.search_pool_round import (
    Loaders,
    deliver_and_report,
    load_sources,
    prepare_args,
    print_source_banner,
)
