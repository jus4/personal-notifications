import asyncio
from typing import Any

from app.models.source import Source
from app.scrapers.jobs import duunitori


def _make_source() -> Source:
    return Source(
        slug="duunitori",
        kind="jobs",
        adapter="duunitori_json",
        url="https://example.test/jobs",
    )


def test_fetch_maps_duunitori_results(monkeypatch: Any) -> None:
    payload = {
        "count": 2,
        "results": [
            {
                "id": 20704922,
                "heading": "Integraatiokehittäjä",
                "short_heading": "",
                "company_name": "Devikone Oy",
            },
            {
                "id": 20704750,
                "heading": "",
                "short_heading": "Ohjelmistokehittäjä",
                "company_name": "Rossum Oy",
            },
        ],
    }

    async def fake_fetch_json(url: str, **kwargs: Any) -> Any:
        assert url == "https://example.test/jobs"
        return payload

    monkeypatch.setattr(duunitori, "fetch_json", fake_fetch_json)

    jobs = asyncio.run(duunitori.DuunitoriJobAdapter().fetch(_make_source()))

    assert [job.external_id for job in jobs] == ["20704922", "20704750"]
    assert [job.title for job in jobs] == [
        "Integraatiokehittäjä",
        "Ohjelmistokehittäjä",
    ]
    assert jobs[0].raw_data == payload["results"][0]
    assert jobs[1].raw_data == payload["results"][1]


def test_fetch_skips_invalid_and_duplicate_entries(monkeypatch: Any) -> None:
    payload = {
        "results": [
            {"id": 1, "heading": "First"},
            {"id": 1, "heading": "Duplicate"},
            {"heading": "Missing id"},
            {"id": 2, "heading": ""},
        ]
    }

    async def fake_fetch_json(url: str, **kwargs: Any) -> Any:
        return payload

    monkeypatch.setattr(duunitori, "fetch_json", fake_fetch_json)

    jobs = asyncio.run(duunitori.DuunitoriJobAdapter().fetch(_make_source()))

    assert len(jobs) == 1
    assert jobs[0].external_id == "1"
    assert jobs[0].title == "First"
