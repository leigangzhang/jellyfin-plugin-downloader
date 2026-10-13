"""The check engine, split by subject: shared helpers, one module per check family, and the registry."""

from . import (capabilities, core, criteria, evidence, links, mirrors, policies, publication, records, registry, seals,
               skills, tiers)
from .capabilities import check_capability_registry
from .cli import main
from .core import (DEFAULT_CONFIG, SKIP_DIRECTORIES, changed_paths, corpus, count_lines, count_words, die,
                   iter_files, load_config, matches, read_manifest, section_body, select_checks)
from .evidence import check_evidence_record
from .links import check_link_target
from .mirrors import check_source_mirror, fenced_blocks, first_difference, normalize_block
from .policies import check_budget, check_forbidden_regex, check_required_sections, section_variants
from .criteria import DEFAULT_CRITERION_ID, check_criteria_traced
from .publication import check_publish_manifest
from .records import check_note_class
from .seals import SEAL_DIGEST, SEAL_DATE, check_sealed_manifest, sealed_digest
from .registry import RUNNERS, SUPPORTED_KINDS, run_checks, self_test, write_fixtures
from .skills import check_skill_trigger, front_matter
from .tiers import check_tier_manifest, home_owns, homes_of, literal_prefix, load_tier_switch

__all__ = [name for name in dir() if not name.startswith("_")]
