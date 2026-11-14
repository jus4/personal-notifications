from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.product_source import ProductSource
from app.models.source import Source
from app.services.products import ProductRepository


def _add_source(
    session: Session,
    *,
    slug: str,
    kind: str = "jobs",
    adapter: str = "duunitori_json",
    url: str | None = "https://example.test/source",
    enabled: bool = True,
    config: dict | None = None,
) -> Source:
    source = Source(
        slug=slug,
        kind=kind,
        adapter=adapter,
        url=url,
        enabled=enabled,
        config=config,
    )
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


def test_sources_page_requires_authentication(client: TestClient) -> None:
    response = client.get("/sources", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_sources_post_requires_authentication(client: TestClient) -> None:
    response = client.post(
        "/sources",
        data={
            "slug": "duunitori",
            "kind": "jobs",
            "adapter": "duunitori_json",
            "url": "https://example.test/jobs",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_sources_page_lists_job_and_product_sources(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    _add_source(
        session,
        slug="duunitori",
        adapter="duunitori_json",
        config={"http": {"impersonate": "none"}},
    )
    _add_source(
        session,
        slug="hardloop",
        kind="products",
        adapter="hardloop_html",
        url=None,
    )

    response = authenticated_client.get("/sources")

    assert response.status_code == 200
    assert "Sources" in response.text
    assert "duunitori" in response.text
    assert "hardloop" in response.text
    assert "duunitori_json" in response.text
    assert "hardloop_html" in response.text
    assert "config" in response.text
    assert 'href="/sources?edit=' in response.text


def test_sources_page_renders_adapter_select_groups(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.get("/sources")

    assert response.status_code == 200
    assert 'id="adapter"' in response.text
    assert "<select" in response.text
    assert "<optgroup" in response.text
    assert 'label="Jobs"' in response.text
    assert 'label="Products"' in response.text
    assert 'value="duunitori_json"' in response.text
    assert 'value="hardloop_html"' in response.text
    assert "adapter-options" not in response.text


def test_edit_page_selects_existing_adapter(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    source = _add_source(
        session,
        slug="hardloop",
        kind="products",
        adapter="hardloop_html",
        url=None,
    )

    response = authenticated_client.get(f"/sources?edit={source.id}")

    assert response.status_code == 200
    assert 'value="hardloop_html" selected' in response.text


def test_edit_page_keeps_unregistered_adapter(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    source = _add_source(
        session,
        slug="legacy",
        adapter="legacy_adapter",
    )

    response = authenticated_client.get(f"/sources?edit={source.id}")

    assert response.status_code == 200
    assert "legacy_adapter" in response.text
    assert "unregistered" in response.text


def test_create_job_source(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    response = authenticated_client.post(
        "/sources",
        data={
            "slug": "jobly",
            "kind": "jobs",
            "adapter": "jobly_html",
            "url": "https://example.test/jobs",
            "enabled": "true",
            "config_json": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/sources?saved=jobly"

    session.expire_all()
    source = session.exec(select(Source).where(Source.slug == "jobly")).one()
    assert source.kind == "jobs"
    assert source.adapter == "jobly_html"
    assert source.url == "https://example.test/jobs"
    assert source.enabled is True
    assert source.config is None


def test_create_product_source_with_config(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    response = authenticated_client.post(
        "/sources",
        data={
            "slug": "hardloop",
            "kind": "products",
            "adapter": "hardloop_html",
            "url": "",
            "enabled": "true",
            "config_json": '{"http": {"impersonate": "chrome"}}',
        },
        follow_redirects=False,
    )

    assert response.status_code == 303

    session.expire_all()
    source = session.exec(select(Source).where(Source.slug == "hardloop")).one()
    assert source.kind == "products"
    assert source.url is None
    assert source.config == {"http": {"impersonate": "chrome"}}


def test_edit_source_updates_fields(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    source = _add_source(session, slug="old-slug")

    response = authenticated_client.post(
        "/sources",
        data={
            "source_id": str(source.id),
            "slug": "new-slug",
            "kind": "products",
            "adapter": "hardloop_html",
            "url": "https://example.test/shop",
            "config_json": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303

    session.expire_all()
    updated = session.get(Source, source.id)
    assert updated is not None
    assert updated.slug == "new-slug"
    assert updated.kind == "products"
    assert updated.adapter == "hardloop_html"
    assert updated.url == "https://example.test/shop"
    assert updated.enabled is False
    assert updated.config is None


def test_edit_page_prefills_config(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    source = _add_source(
        session,
        slug="epictv",
        kind="products",
        adapter="epictv_html",
        url=None,
        config={"http": {"impersonate": "chrome"}},
    )

    response = authenticated_client.get(f"/sources?edit={source.id}")

    assert response.status_code == 200
    assert "Edit source" in response.text
    assert "epictv" in response.text
    assert "impersonate" in response.text


def test_duplicate_slug_returns_error(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    _add_source(session, slug="duunitori")

    response = authenticated_client.post(
        "/sources",
        data={
            "slug": "duunitori",
            "kind": "jobs",
            "adapter": "duunitori_json",
            "url": "https://example.test/other",
            "enabled": "true",
        },
    )

    assert response.status_code == 400
    assert "already exists" in response.text
    assert len(session.exec(select(Source)).all()) == 1


def test_invalid_adapter_returns_error(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    response = authenticated_client.post(
        "/sources",
        data={
            "slug": "unknown",
            "kind": "jobs",
            "adapter": "not-a-real-adapter",
            "url": "https://example.test/jobs",
            "enabled": "true",
        },
    )

    assert response.status_code == 400
    assert "Unknown jobs adapter" in response.text
    assert session.exec(select(Source)).all() == []


def test_job_source_requires_url(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    response = authenticated_client.post(
        "/sources",
        data={
            "slug": "no-url",
            "kind": "jobs",
            "adapter": "duunitori_json",
            "url": "",
            "enabled": "true",
        },
    )

    assert response.status_code == 400
    assert "URL is required for job sources" in response.text
    assert session.exec(select(Source)).all() == []


def test_invalid_config_returns_error(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    response = authenticated_client.post(
        "/sources",
        data={
            "slug": "bad-config",
            "kind": "products",
            "adapter": "hardloop_html",
            "url": "",
            "enabled": "true",
            "config_json": '{"http": {"unknown": true}}',
        },
    )

    assert response.status_code == 400
    assert session.exec(select(Source)).all() == []


def test_delete_source(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    source = _add_source(session, slug="delete-me")
    source_id = source.id

    response = authenticated_client.post(
        f"/sources/{source_id}/delete",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/sources?deleted=delete-me"
    session.expire_all()
    assert session.get(Source, source_id) is None


def test_delete_source_with_product_links_is_blocked(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    source = _add_source(
        session,
        slug="shop",
        kind="products",
        adapter="hardloop_html",
        url=None,
    )
    repository = ProductRepository(session)
    product = repository.create_product(title="Solution")
    repository.add_product_source(
        product_id=product.id or 0,
        source_id=source.id or 0,
        url="https://example.test/product",
    )

    response = authenticated_client.post(
        f"/sources/{source.id}/delete",
    )

    assert response.status_code == 400
    assert "linked product offers" in response.text
    assert session.get(Source, source.id) is not None
    assert session.exec(select(ProductSource)).one() is not None


def test_cannot_change_kind_with_linked_product_offers(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    source = _add_source(
        session,
        slug="shop",
        kind="products",
        adapter="hardloop_html",
        url=None,
    )
    repository = ProductRepository(session)
    product = repository.create_product(title="Solution")
    repository.add_product_source(
        product_id=product.id or 0,
        source_id=source.id or 0,
        url="https://example.test/product",
    )

    response = authenticated_client.post(
        "/sources",
        data={
            "source_id": str(source.id),
            "slug": "shop",
            "kind": "jobs",
            "adapter": "duunitori_json",
            "url": "https://example.test/jobs",
            "enabled": "true",
        },
    )

    assert response.status_code == 400
    assert "Cannot change the kind" in response.text

    session.expire_all()
    unchanged = session.get(Source, source.id)
    assert unchanged is not None
    assert unchanged.kind == "products"


def test_sources_filter_by_kind(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    _add_source(session, slug="jobs-only")
    _add_source(
        session,
        slug="shop-only",
        kind="products",
        adapter="bergfreunde_html",
        url=None,
    )

    response = authenticated_client.get("/sources?kind=products")

    assert response.status_code == 200
    assert "shop-only" in response.text
    assert "jobs-only" not in response.text
