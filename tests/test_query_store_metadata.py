"""Tests for query store operations in MetadataStore.

Defines expected behavior for the query store layer:
  - save_query persists a named query with all metadata fields
  - save_query generates a URL-safe slug from title (returned)
  - save_query raises an error on duplicate slug
  - list_queries returns queries filtered by created_by, with pagination
  - get_query_by_slug returns the stored query
  - get_query_by_slug raises an error for unknown slugs
"""

import pytest
import pytest_asyncio
import tempfile
import os
from datetime import datetime, timezone

from ponddb.metadata_store import MetadataStore


@pytest_asyncio.fixture
async def store(tmp_path):
    """Fresh MetadataStore backed by a temp SQLite file."""
    db_path = str(tmp_path / "test.db")
    s = MetadataStore(db_path)
    await s.initialize()
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# save_query — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_query_returns_slug(store: MetadataStore) -> None:
    slug = await store.save_query(
        title="My First Query",
        description="A test query",
        sql="SELECT 1",
        created_by="user1",
    )
    assert isinstance(slug, str)
    assert len(slug) > 0


@pytest.mark.asyncio
async def test_save_query_slug_is_url_safe(store: MetadataStore) -> None:
    slug = await store.save_query(
        title="Complex Query With Spaces & Symbols!",
        description="",
        sql="SELECT 2",
        created_by="user1",
    )
    # URL-safe: only lowercase letters, digits, hyphens
    assert slug == slug.lower()
    for char in slug:
        assert char.isalnum() or char == "-", f"Non-URL-safe char: {char!r}"


@pytest.mark.asyncio
async def test_save_query_slug_derived_from_title(store: MetadataStore) -> None:
    slug = await store.save_query(
        title="Monthly Revenue Report",
        description="",
        sql="SELECT 3",
        created_by="user1",
    )
    assert "monthly" in slug
    assert "revenue" in slug
    assert "report" in slug


@pytest.mark.asyncio
async def test_save_query_stores_all_fields(store: MetadataStore) -> None:
    slug = await store.save_query(
        title="Revenue Query",
        description="Gets revenue totals",
        sql="SELECT sum(amount) FROM sales",
        created_by="alice",
    )
    result = await store.get_query_by_slug(slug)
    assert result["title"] == "Revenue Query"
    assert result["description"] == "Gets revenue totals"
    assert result["sql"] == "SELECT sum(amount) FROM sales"
    assert result["created_by"] == "alice"
    assert result["slug"] == slug


@pytest.mark.asyncio
async def test_save_query_records_created_at(store: MetadataStore) -> None:
    before = datetime.now(timezone.utc)
    slug = await store.save_query(
        title="Timestamped Query",
        description="",
        sql="SELECT now()",
        created_by="bob",
    )
    after = datetime.now(timezone.utc)
    result = await store.get_query_by_slug(slug)
    # created_at should be a string parseable as ISO datetime
    created_at = datetime.fromisoformat(result["created_at"])
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    assert before <= created_at <= after


@pytest.mark.asyncio
async def test_save_query_description_can_be_empty(store: MetadataStore) -> None:
    slug = await store.save_query(
        title="No Description Query",
        description="",
        sql="SELECT 42",
        created_by="user1",
    )
    result = await store.get_query_by_slug(slug)
    assert result["description"] == ""


# ---------------------------------------------------------------------------
# save_query — duplicate slug
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_query_duplicate_slug_raises_error(store: MetadataStore) -> None:
    await store.save_query(
        title="My Query",
        description="",
        sql="SELECT 1",
        created_by="user1",
    )
    with pytest.raises(Exception):  # DuplicateQueryError or similar
        await store.save_query(
            title="My Query",  # same title → same slug
            description="different desc",
            sql="SELECT 2",
            created_by="user1",
        )


# ---------------------------------------------------------------------------
# get_query_by_slug
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_query_by_slug_missing_raises_error(store: MetadataStore) -> None:
    with pytest.raises(Exception):  # QueryNotFoundError or KeyError or similar
        await store.get_query_by_slug("slug-that-does-not-exist")


@pytest.mark.asyncio
async def test_get_query_by_slug_returns_correct_query(store: MetadataStore) -> None:
    slug1 = await store.save_query(
        title="Query Alpha",
        description="alpha",
        sql="SELECT 1",
        created_by="user1",
    )
    slug2 = await store.save_query(
        title="Query Beta",
        description="beta",
        sql="SELECT 2",
        created_by="user1",
    )
    result = await store.get_query_by_slug(slug2)
    assert result["title"] == "Query Beta"
    assert result["sql"] == "SELECT 2"


# ---------------------------------------------------------------------------
# list_queries — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_queries_returns_all_for_user(store: MetadataStore) -> None:
    await store.save_query(title="Q1", description="", sql="SELECT 1", created_by="alice")
    await store.save_query(title="Q2", description="", sql="SELECT 2", created_by="alice")
    await store.save_query(title="Q3", description="", sql="SELECT 3", created_by="bob")

    results = await store.list_queries(created_by="alice")
    assert len(results) == 2
    titles = {r["title"] for r in results}
    assert titles == {"Q1", "Q2"}


@pytest.mark.asyncio
async def test_list_queries_returns_empty_for_unknown_user(store: MetadataStore) -> None:
    await store.save_query(title="Q1", description="", sql="SELECT 1", created_by="alice")
    results = await store.list_queries(created_by="nobody")
    assert results == []


@pytest.mark.asyncio
async def test_list_queries_default_limit_is_20(store: MetadataStore) -> None:
    for i in range(25):
        await store.save_query(
            title=f"Query Number {i:03d}",
            description="",
            sql=f"SELECT {i}",
            created_by="alice",
        )
    results = await store.list_queries(created_by="alice")
    assert len(results) == 20


@pytest.mark.asyncio
async def test_list_queries_limit_param(store: MetadataStore) -> None:
    for i in range(10):
        await store.save_query(
            title=f"Query Limit {i:03d}",
            description="",
            sql=f"SELECT {i}",
            created_by="alice",
        )
    results = await store.list_queries(created_by="alice", limit=5)
    assert len(results) == 5


@pytest.mark.asyncio
async def test_list_queries_offset_param(store: MetadataStore) -> None:
    for i in range(10):
        await store.save_query(
            title=f"Query Offset {i:03d}",
            description="",
            sql=f"SELECT {i}",
            created_by="alice",
        )
    first_page = await store.list_queries(created_by="alice", limit=5, offset=0)
    second_page = await store.list_queries(created_by="alice", limit=5, offset=5)
    all_results = await store.list_queries(created_by="alice", limit=100, offset=0)

    first_slugs = {r["slug"] for r in first_page}
    second_slugs = {r["slug"] for r in second_page}
    assert first_slugs.isdisjoint(second_slugs)
    assert len(first_slugs | second_slugs) == 10
    assert len(all_results) == 10


@pytest.mark.asyncio
async def test_list_queries_returns_expected_fields(store: MetadataStore) -> None:
    await store.save_query(
        title="Field Check Query",
        description="Check fields",
        sql="SELECT 99",
        created_by="charlie",
    )
    results = await store.list_queries(created_by="charlie")
    assert len(results) == 1
    q = results[0]
    assert "slug" in q
    assert "title" in q
    assert "description" in q
    assert "sql" in q
    assert "created_by" in q
    assert "created_at" in q
