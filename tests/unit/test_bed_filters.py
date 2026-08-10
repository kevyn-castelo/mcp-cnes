from __future__ import annotations

import pytest

from cnes_scraper import CNESConfig, CNESScraper, validate_bed_range
from mcp_server import HospitalInfo, MCPServer


def hospital(cnes: str, beds: int, uf: str = "AM") -> HospitalInfo:
    return HospitalInfo(
        cnes=cnes,
        nome_fantasia=f"Hospital {beds}",
        municipio="Manaus",
        uf=uf,
        leitos_existentes=beds,
    )


@pytest.mark.asyncio
async def test_mcp_search_accepts_custom_inclusive_bed_range() -> None:
    server = MCPServer()
    server.data_store.hospitals = [
        hospital("0000049", 49),
        hospital("0000050", 50),
        hospital("0000150", 150),
        hospital("0000151", 151),
    ]

    unfiltered = await server.call_tool(
        "cnes_search_municipio", {"municipio": "Manaus", "limit": 2}
    )
    filtered = await server.call_tool(
        "cnes_search_municipio",
        {"municipio": "Manaus", "min_leitos": 50, "max_leitos": 150},
    )

    assert unfiltered["total_encontrados"] == 4
    assert unfiltered["total_retornados"] == 2
    assert unfiltered["filtros_leitos"] == {"minimo": None, "maximo": None}
    assert [item["leitos_existentes"] for item in filtered["estabelecimentos"]] == [150, 50]
    assert filtered["total_encontrados"] == 2
    assert filtered["total_retornados"] == 2


@pytest.mark.asyncio
async def test_mcp_uf_search_supports_one_sided_range() -> None:
    server = MCPServer()
    server.data_store.hospitals = [hospital("0000050", 50), hospital("0000151", 151)]

    result = await server.call_tool("cnes_search_uf", {"uf": "AM", "min_leitos": 100})

    assert [item["cnes"] for item in result["estabelecimentos"]] == ["0000151"]
    assert result["filtros_leitos"] == {"minimo": 100, "maximo": None}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments, message",
    [
        ({"min_leitos": -1}, "não pode ser negativo"),
        ({"min_leitos": 151, "max_leitos": 150}, "não pode ser maior"),
        ({"min_leitos": "50"}, "deve ser um inteiro"),
    ],
)
async def test_mcp_rejects_invalid_bed_ranges(arguments: dict, message: str) -> None:
    server = MCPServer()
    server.data_store.hospitals = [hospital("0000050", 50)]

    result = await server.call_tool(
        "cnes_search_municipio", {"municipio": "Manaus", **arguments}
    )

    assert message in result["error"]


def bucket(cnes: str, beds: int) -> dict:
    return {
        "key": cnes,
        "total_leitos": {"value": beds},
        "nome_fantasia": {"buckets": [{"key": f"Hospital {beds}"}]},
        "gestao": {"buckets": [{"key": "PRIVADA"}]},
        "natureza": {"buckets": [{"key": "2062 - PRIVADA"}]},
        "uf": {"buckets": [{"key": "AM"}]},
    }


def test_scraper_default_range_does_not_reinsert_rejected_hits() -> None:
    scraper = CNESScraper()
    data = {
        "aggregations": {
            "por_cnes": {
                "buckets": [
                    bucket("0000049", 49),
                    bucket("0000050", 50),
                    bucket("0000150", 150),
                    bucket("0000151", 151),
                ]
            }
        },
        "hits": {
            "hits": [
                {"_source": {"CNES": "0000049", "QT_EXIST": 49}},
                {"_source": {"CNES": "0000151", "QT_EXIST": 151}},
            ]
        },
    }

    result = scraper._extract_hospitals(data, "MANAUS", 50, 150)

    assert set(result) == {"0000050", "0000150"}


def test_scraper_fetch_allows_per_call_range_override(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper = CNESScraper()
    leitos_data = {
        "aggregations": {
            "por_cnes": {"buckets": [bucket("0000151", 151)]}
        }
    }

    def fake_query(index: str, query: dict) -> dict:
        return leitos_data if index == "cnes_leitos*" else {}

    monkeypatch.setattr(scraper, "query_elasticsearch", fake_query)
    monkeypatch.setattr(scraper, "_delay", lambda: None)

    result = scraper.fetch_hospitals_by_city("MANAUS", min_beds=151, max_beds=151)

    assert len(result) == 1
    assert result[0]["cnes"] == "0000151"
    assert result[0]["total_leitos"] == 151


def test_scraper_hit_fallback_revalidates_filters_and_consolidates() -> None:
    scraper = CNESScraper()

    def source(cnes: str, quantity: int, nature: str = "2062", competence: str = "202512"):
        return {
            "_source": {
                "CNES": cnes,
                "QT_EXIST": quantity,
                "NATUREZA_JURIDICA": nature,
                "COMPETENCIA": competence,
                "NOME_FANTASIA": cnes,
                "UF": "AM",
            }
        }

    data = {
        "hits": {
            "hits": [
                source("VALIDO", 20),
                source("VALIDO", 30),
                source("PUBLICO", 100, nature="1000"),
                source("ANTIGO", 100, competence="202511"),
                source("GRANDE", 151),
            ]
        }
    }

    result = scraper._extract_hospitals(data, "MANAUS", 50, 150)

    assert set(result) == {"VALIDO"}
    assert result["VALIDO"]["total_leitos"] == 50


def test_scraper_query_contains_nature_and_competence_filters() -> None:
    config = CNESConfig(COMPETENCIA="202607")
    scraper = CNESScraper(config)

    query = scraper._build_leitos_query("MANAUS")
    boolean_query = query["query"]["bool"]

    assert {"match": {"COMPETENCIA": "202607"}} in boolean_query["must"]
    assert len(boolean_query["should"]) == len(config.PRIVATE_NATURE_CODES)
    assert boolean_query["minimum_should_match"] == 1


@pytest.mark.parametrize("minimum, maximum", [(-1, 150), (151, 150)])
def test_scraper_rejects_invalid_ranges(minimum: int, maximum: int) -> None:
    with pytest.raises(ValueError):
        validate_bed_range(minimum, maximum)
