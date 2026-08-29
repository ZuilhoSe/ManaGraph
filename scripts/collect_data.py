"""Collect Scryfall, Moxfield, or EDHREC data into SQLite/Chroma.

Examples:
  python scripts/collect_data.py --source scryfall --fixture fixture.jsonl.gz
  python scripts/collect_data.py --source moxfield --fixture moxfield.json --chroma
  python scripts/collect_data.py --source edhrec --url https://example/public.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
SRC_DIR = os.path.join(BASE_DIR, "src")
sys.path.insert(0, SRC_DIR)

from data_collection import (  # noqa: E402
    CollectionError,
    EDHRECAdapter,
    MOXFIELD_DECK_FALLBACK_URL,
    MOXFIELD_DECK_URL,
    MOXFIELD_SEARCH_URL,
    MoxfieldAdapter,
    collect_json,
    collect_edhrec_public,
    collect_moxfield_public,
    persist_source_chroma,
)
from scryfall_download import download_and_process_scryfall  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect public/fixture MTG data into normalized SQLite tables."
    )
    parser.add_argument("--source", choices=("scryfall", "moxfield", "edhrec"), required=True)
    parser.add_argument("--url", help="Configured public URL; no source-specific private endpoint is assumed.")
    parser.add_argument(
        "--fixture", "--file", dest="fixture",
        help="Local JSON fixture (or Scryfall .jsonl/.jsonl.gz); avoids network access.",
    )
    parser.add_argument("--db-path", default=os.path.join(BASE_DIR, "data", "managraph.db"))
    parser.add_argument("--chroma-path", default=os.path.join(BASE_DIR, "data", "chroma_db"))
    parser.add_argument("--cache-dir", default=os.path.join(BASE_DIR, "data", "source_cache"))
    parser.add_argument("--dataset-id", help="Stable dataset ID; otherwise content hash is used.")
    parser.add_argument("--sha256", help="Expected SHA-256 for the downloaded/local payload.")
    parser.add_argument("--license", help="License/usage note to store with provenance.")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--user-agent", default="ManaGraph-data-collector/1.0 (+https://github.com/)")
    parser.add_argument("--rate-limit", type=float, default=1.0, help="Seconds between network requests.")
    parser.add_argument("--retries", type=int, default=3, help="Retries for transient HTTP/network errors.")
    parser.add_argument("--no-robots", action="store_true", help="Only for a source with explicit permission.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report counts without SQLite writes.")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild only the additive source Chroma collection.")
    parser.add_argument("--chroma", action="store_true", help="Persist deck/recommendation docs in additive Chroma collections.")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Fixture record limit; public Moxfield decks or EDHREC commander pages.",
    )
    parser.add_argument("--page-size", type=int, default=100, help="Public listing page size (Moxfield: 1-100).")
    parser.add_argument("--max-pages", type=int, default=10, help="Maximum public listing pages.")
    parser.add_argument(
        "--max-commanders", type=int, default=50,
        help="Maximum EDHREC commander pages when --limit is omitted.",
    )
    parser.add_argument("--query", help="Moxfield public search text.")
    parser.add_argument("--format", dest="format_name", default="commander", help="Moxfield format filter.")
    parser.add_argument("--search-url", help="Moxfield public search endpoint override.")
    parser.add_argument("--deck-url-template", help="Moxfield deck endpoint template override.")
    parser.add_argument(
        "--deck-url-fallback-template",
        default=MOXFIELD_DECK_FALLBACK_URL,
        help="Moxfield public detail fallback after an HTTP 404 (empty disables it).",
    )
    parser.add_argument("--listing-url", help="EDHREC public listing endpoint override.")
    parser.add_argument("--top-url-template", help="EDHREC top-page template override.")
    parser.add_argument("--commander-url-template", help="EDHREC commander endpoint template override.")
    parser.add_argument("--no-details", action="store_true", help="Moxfield: store summaries without fetching deck details.")
    parser.add_argument("--no-commander-pages", action="store_true", help="EDHREC: store general listings only.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.limit is not None and args.limit < 0:
        _parser().error("--limit must be non-negative")
    if args.retries < 0 or args.max_pages < 1 or args.max_commanders < 0:
        _parser().error("--retries must be non-negative; page/commander limits must be non-negative")
    try:
        if args.source == "scryfall":
            if not args.fixture and args.url:
                # The Scryfall adapter obtains its bulk URL from the public
                # metadata endpoint, so --url is the metadata URL.
                result = download_and_process_scryfall(
                    db_path=args.db_path,
                    metadata_url=args.url,
                    cache_dir=args.cache_dir,
                    timeout=args.timeout,
                    user_agent=args.user_agent,
                    expected_sha256=args.sha256,
                    dry_run=args.dry_run,
                    min_interval=args.rate_limit,
                    retries=args.retries,
                )
            elif args.fixture:
                result = download_and_process_scryfall(
                    db_path=args.db_path,
                    local_file=args.fixture,
                    expected_sha256=args.sha256,
                    dry_run=args.dry_run,
                    min_interval=args.rate_limit,
                    retries=args.retries,
                )
            else:
                result = download_and_process_scryfall(
                    db_path=args.db_path,
                    cache_dir=args.cache_dir,
                    timeout=args.timeout,
                    user_agent=args.user_agent,
                    expected_sha256=args.sha256,
                    dry_run=args.dry_run,
                    min_interval=args.rate_limit,
                    retries=args.retries,
                )
            print(json.dumps(result, indent=2, default=str))
            return 0

        adapter = MoxfieldAdapter() if args.source == "moxfield" else EDHRECAdapter()
        if not args.url and not args.fixture:
            parsed_out = []
            if args.source == "moxfield":
                result = collect_moxfield_public(
                    db_path=args.db_path,
                    search_url=args.search_url or MOXFIELD_SEARCH_URL,
                    deck_url_template=args.deck_url_template or MOXFIELD_DECK_URL,
                    deck_url_fallback_template=args.deck_url_fallback_template,
                    cache_dir=args.cache_dir,
                    timeout=args.timeout,
                    user_agent=args.user_agent,
                    min_interval=args.rate_limit,
                    robots=not args.no_robots,
                    page_size=args.page_size,
                    max_pages=args.max_pages,
                    max_decks=args.limit,
                    query=args.query,
                    format_name=args.format_name,
                    fetch_details=not args.no_details,
                    dataset_id=args.dataset_id,
                    license=args.license,
                    dry_run=args.dry_run,
                    retries=args.retries,
                    parsed_out=parsed_out,
                )
            else:
                result = collect_edhrec_public(
                    db_path=args.db_path,
                    listing_url=args.listing_url or "https://json.edhrec.com/pages/commanders.json",
                    top_url_template=args.top_url_template or "https://json.edhrec.com/pages/top/commanders--{page}.json",
                    commander_url_template=(
                        args.commander_url_template
                        or "https://json.edhrec.com/pages/commanders/{slug}.json"
                    ),
                    cache_dir=args.cache_dir,
                    timeout=args.timeout,
                    user_agent=args.user_agent,
                    min_interval=args.rate_limit,
                    robots=not args.no_robots,
                    max_pages=args.max_pages,
                    max_commanders=args.limit if args.limit is not None else args.max_commanders,
                    fetch_commander_pages=not args.no_commander_pages,
                    dataset_id=args.dataset_id,
                    license=args.license,
                    dry_run=args.dry_run,
                    retries=args.retries,
                    parsed_out=parsed_out,
                )
            if args.chroma and not args.dry_run and parsed_out:
                result["chroma"] = persist_source_chroma(
                    parsed_out[0],
                    adapter,
                    chroma_path=args.chroma_path,
                    dataset_id=result["dataset_id"],
                    rebuild=args.rebuild,
                )
            print(json.dumps(result, indent=2, default=str))
            return 0
        # collect_json handles exactly-one input validation, caching, robots,
        # parsing, normalization persistence, and provenance.
        parsed_out = []
        result = collect_json(
            adapter,
            db_path=args.db_path,
            url=args.url,
            fixture=args.fixture,
            cache_dir=args.cache_dir,
            timeout=args.timeout,
            user_agent=args.user_agent,
            min_interval=args.rate_limit,
            robots=not args.no_robots,
            dataset_id=args.dataset_id,
            license=args.license,
            dry_run=args.dry_run,
            limit=args.limit,
            parsed_out=parsed_out,
        )
        if args.chroma and not args.dry_run:
            if parsed_out:
                result["chroma"] = persist_source_chroma(
                    parsed_out[0],
                    adapter,
                    chroma_path=args.chroma_path,
                    dataset_id=result["dataset_id"],
                    rebuild=args.rebuild,
                )
        print(json.dumps(result, indent=2, default=str))
        return 0
    except CollectionError as exc:
        print(f"Collection failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
