"""
Pure-function tests for webmotors_scraper and vehicle_search.

Covers: slugify, build_detail_url, detail_url_from_listing, normalize_detail,
_to_number, calcular_medias, keep_only_webmotors, extract_vehicle_data.

No network I/O; no browser; no subprocess. All fixtures from conftest.py are
consumed as-is — factories (make_listing, make_detail) are imported directly
since they are plain functions, not pytest fixtures.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import webmotors_scraper as wm
import vehicle_search as vs

# conftest factories are plain functions — import directly.
from tests.conftest import make_listing, make_detail


# ===========================================================================
# slugify
# ===========================================================================

class TestSlugify:
    def test_anchor_version_string(self):
        assert wm.slugify("1.5 i-VTEC FLEX HATCH EXL CVT") == "15-i-vtec-flex-hatch-exl-cvt"

    def test_leading_trailing_spaces(self):
        result = wm.slugify("  HONDA  ")
        assert not result.startswith("-")
        assert not result.endswith("-")
        assert result == "honda"

    def test_multiple_non_alnum_collapse_to_single_dash(self):
        # Two dots become empty, spaces collapse, should produce single dash
        result = wm.slugify("A  B")
        assert "--" not in result
        assert result == "a-b"

    def test_already_clean_lowercase_string(self):
        result = wm.slugify("honda")
        assert result == "honda"

    def test_empty_string(self):
        result = wm.slugify("")
        assert result == ""

    def test_dot_removal(self):
        # Dots are removed (not replaced with dash)
        result = wm.slugify("1.5")
        assert result == "15"

    def test_uppercase_lowercased(self):
        result = wm.slugify("HONDA CITY")
        assert result == "honda-city"

    def test_no_leading_trailing_dashes_after_strip(self):
        # Non-alnum at start/end
        result = wm.slugify("!hello!")
        assert not result.startswith("-")
        assert not result.endswith("-")

    @settings(max_examples=200)
    @given(st.text(min_size=0, max_size=80))
    def test_hypothesis_properties(self, value: str):
        """Output is always lowercase, no leading/trailing '-', no '--', only [a-z0-9-]."""
        result = wm.slugify(value)
        assert result == result.lower(), "output must be lowercase"
        assert not result.startswith("-"), "no leading dash"
        assert not result.endswith("-"), "no trailing dash"
        assert "--" not in result, "no consecutive dashes"
        assert re.fullmatch(r"[a-z0-9-]*", result), "only [a-z0-9-] allowed"


# ===========================================================================
# build_detail_url
# ===========================================================================

BASE = "https://www.webmotors.com.br"

class TestBuildDetailUrl:
    def test_anchor_url(self):
        url = wm.build_detail_url(
            make="HONDA",
            model="CITY",
            version="1.5 i-VTEC FLEX HATCH EXL CVT",
            doors=4,
            year=2022,
            unique_id=69863170,
        )
        assert url.endswith(
            "/api/detail/car/honda/city/15-i-vtec-flex-hatch-exl-cvt/4-portas/2022/69863170"
        )

    def test_starts_with_base_url(self):
        url = wm.build_detail_url(
            make="HONDA", model="CITY", version="EXL",
            doors=4, year=2022, unique_id=1
        )
        assert url.startswith(BASE)

    def test_doors_string_coerced_to_int(self):
        url_str = wm.build_detail_url(
            make="HONDA", model="CITY", version="EXL",
            doors="4", year=2022, unique_id=1
        )
        url_int = wm.build_detail_url(
            make="HONDA", model="CITY", version="EXL",
            doors=4, year=2022, unique_id=1
        )
        assert url_str == url_int
        assert "4-portas" in url_str

    def test_year_float_coerced_to_int(self):
        url_float = wm.build_detail_url(
            make="HONDA", model="CITY", version="EXL",
            doors=4, year=2022.0, unique_id=1
        )
        url_int = wm.build_detail_url(
            make="HONDA", model="CITY", version="EXL",
            doors=4, year=2022, unique_id=1
        )
        assert url_float == url_int
        assert "2022" in url_float

    def test_year_string_coerced(self):
        url = wm.build_detail_url(
            make="HONDA", model="CITY", version="EXL",
            doors=4, year="2022", unique_id=1
        )
        assert "2022" in url
        # Should not contain "2022.0"
        assert "2022.0" not in url

    def test_doors_string_four_produces_4_portas(self):
        url = wm.build_detail_url(
            make="VW", model="GOL", version="1.0",
            doors="4", year=2020, unique_id=99
        )
        assert "4-portas" in url

    def test_unique_id_in_url(self):
        url = wm.build_detail_url(
            make="HONDA", model="CITY", version="EXL",
            doors=4, year=2022, unique_id=69863170
        )
        assert url.endswith("/69863170")


# ===========================================================================
# detail_url_from_listing
# ===========================================================================

class TestDetailUrlFromListing:
    def test_produces_valid_url_from_make_listing(self):
        listing = make_listing()
        url = wm.detail_url_from_listing(listing)
        assert url.startswith(BASE + "/api/detail/car/")
        assert "honda" in url
        assert "city" in url
        assert str(listing["UniqueId"]) in url

    def test_year_model_float_used_as_int_in_url(self):
        # YearModel=2024.0 -> should appear as "2024", not "2024.0"
        listing = make_listing(year_model=2024.0, year_fabrication="2023")
        url = wm.detail_url_from_listing(listing)
        assert "2024" in url
        assert "2024.0" not in url
        assert "2023" not in url

    def test_year_model_present_takes_precedence_over_fabrication(self):
        listing = make_listing(year_model=2023.0, year_fabrication="2020")
        url = wm.detail_url_from_listing(listing)
        assert "2023" in url
        assert "2020" not in url

    def test_year_model_missing_falls_back_to_fabrication(self):
        listing = make_listing(year_fabrication="2021")
        # Override YearModel to None to test fallback
        listing["Specification"]["YearModel"] = None
        url = wm.detail_url_from_listing(listing)
        assert "2021" in url

    def test_year_model_zero_falsy_falls_back_to_fabrication(self):
        # 0 is falsy in Python, should fall back
        listing = make_listing(year_fabrication="2020")
        listing["Specification"]["YearModel"] = 0
        url = wm.detail_url_from_listing(listing)
        assert "2020" in url

    def test_number_ports_missing_defaults_to_4(self):
        listing = make_listing()
        del listing["Specification"]["NumberPorts"]
        url = wm.detail_url_from_listing(listing)
        assert "4-portas" in url

    def test_number_ports_none_defaults_to_4(self):
        listing = make_listing()
        listing["Specification"]["NumberPorts"] = None
        url = wm.detail_url_from_listing(listing)
        assert "4-portas" in url

    def test_unique_id_in_url(self):
        listing = make_listing(unique_id=12345678)
        url = wm.detail_url_from_listing(listing)
        assert url.endswith("/12345678")


# ===========================================================================
# normalize_detail
# ===========================================================================

class TestNormalizeDetail:
    def test_flat_keys_and_values_from_make_detail(self):
        detail = make_detail()
        result = wm.normalize_detail(detail)

        # Verify all expected flat keys exist
        expected_keys = {
            "unique_id", "titulo", "marca", "modelo", "versao",
            "ano_modelo", "ano_fabricacao", "km", "cor", "cambio",
            "preco", "cidade", "estado",
        }
        assert set(result.keys()) == expected_keys

    def test_values_from_make_detail_defaults(self):
        detail = make_detail()
        result = wm.normalize_detail(detail)

        assert result["unique_id"] == 69863170
        assert result["marca"] == "HONDA"
        assert result["modelo"] == "CITY"
        assert result["versao"] == "1.5 i-VTEC FLEX HATCH EXL CVT"
        assert result["ano_modelo"] == "2022"
        assert result["ano_fabricacao"] == "2022"
        assert result["km"] == "70000"
        assert result["cor"] == "Prata"
        assert result["cambio"] == "CVT"
        assert result["preco"] == "90000"  # STRING in detail
        assert result["cidade"] == "Volta Redonda"
        assert result["estado"] == "Rio de Janeiro (RJ)"
        assert result["titulo"] == "HONDA CITY 1.5 i-VTEC FLEX HATCH EXL CVT"

    def test_seller_absent_gives_none_cidade_and_estado(self):
        # pass seller_city=None to OMIT the Seller key entirely
        detail = make_detail(seller_city=None, seller_state=None)
        assert "Seller" not in detail  # guard: fixture did omit it
        result = wm.normalize_detail(detail)
        assert result["cidade"] is None
        assert result["estado"] is None

    def test_price_is_string_not_float(self):
        detail = make_detail(price="115900")
        result = wm.normalize_detail(detail)
        assert result["preco"] == "115900"
        assert isinstance(result["preco"], str)

    def test_custom_make_model(self):
        detail = make_detail(make="TOYOTA", model="COROLLA", version="2.0 GR-S")
        result = wm.normalize_detail(detail)
        assert result["marca"] == "TOYOTA"
        assert result["modelo"] == "COROLLA"
        assert result["versao"] == "2.0 GR-S"


# ===========================================================================
# _to_number
# ===========================================================================

class TestToNumber:
    def test_none_returns_none(self):
        assert vs._to_number(None) is None

    def test_empty_string_returns_none(self):
        assert vs._to_number("") is None

    def test_int_returns_float(self):
        result = vs._to_number(115900)
        assert result == 115900.0
        assert isinstance(result, float)

    def test_float_returns_float(self):
        result = vs._to_number(115900.0)
        assert result == 115900.0
        assert isinstance(result, float)

    def test_plain_string_int(self):
        assert vs._to_number("115900") == 115900.0

    def test_pt_br_format_with_comma_decimal(self):
        # "R$ 115.900,00" -> comma is decimal, dot is thousands -> 115900.0
        assert vs._to_number("R$ 115.900,00") == 115900.0

    def test_lone_dot_is_decimal_point(self):
        # GOTCHA: no comma present -> dot treated as decimal separator
        # "115.900" -> 115.9 (NOT 115900)
        assert vs._to_number("115.900") == 115.9

    def test_string_with_currency_prefix(self):
        # currency chars stripped, then parsed
        assert vs._to_number("R$90000") == 90000.0

    def test_zero_int(self):
        assert vs._to_number(0) == 0.0

    def test_zero_float(self):
        assert vs._to_number(0.0) == 0.0

    def test_string_with_comma_only(self):
        # "1.000,50" -> pt-BR: 1000.50
        assert vs._to_number("1.000,50") == 1000.50


# ===========================================================================
# calcular_medias — PRIORITY REGRESSION
# ===========================================================================

class TestCalcularMedias:
    def test_regression_float_price_not_inflated(self):
        """
        REGRESSION: old code did str(value).replace('.','') which would
        convert 115900.0 -> "1159000" (inflated by 10x).
        Correct result: avg(115900.0, 112000) = 113950.0, NOT 1139500.0.
        """
        anuncios = [{"preco": 115900.0}, {"preco": "112000"}]
        result = vs.calcular_medias(anuncios)
        assert result["media_preco"] == 113950.0
        assert result["media_preco"] != 1139500.0  # the old bug value

    def test_mixed_float_and_string_prices(self):
        anuncios = [
            {"preco": 100000.0},
            {"preco": "200000"},
            {"preco": 300000.0},
        ]
        result = vs.calcular_medias(anuncios)
        assert result["media_preco"] == 200000.0

    def test_media_km(self):
        anuncios = [
            {"preco": 100000.0, "km": 10000.0},
            {"preco": 200000.0, "km": 20000.0},
        ]
        result = vs.calcular_medias(anuncios)
        assert result["media_km"] == 15000.0

    def test_empty_list_returns_none(self):
        result = vs.calcular_medias([])
        assert result["media_preco"] is None
        assert result["media_km"] is None

    def test_custom_campo_preco(self):
        anuncios = [{"valor": 50000.0}, {"valor": 70000.0}]
        result = vs.calcular_medias(anuncios, campo_preco="valor")
        assert result["media_preco"] == 60000.0

    def test_custom_campo_km(self):
        anuncios = [{"preco": 100000.0, "quilometragem": 30000.0}]
        result = vs.calcular_medias(anuncios, campo_km="quilometragem")
        assert result["media_km"] == 30000.0

    def test_entries_missing_preco_field_are_skipped(self):
        # Entries without the campo_preco key are skipped (get returns None -> filtered)
        anuncios = [
            {"preco": 100000.0},
            {"km": 10000.0},          # no "preco"
            {"preco": 200000.0},
        ]
        result = vs.calcular_medias(anuncios)
        assert result["media_preco"] == 150000.0

    def test_entries_missing_km_field_are_skipped(self):
        anuncios = [
            {"preco": 100000.0, "km": 10000.0},
            {"preco": 200000.0},             # no "km"
        ]
        result = vs.calcular_medias(anuncios)
        assert result["media_km"] == 10000.0

    def test_all_missing_preco_gives_none(self):
        anuncios = [{"km": 10000.0}, {"km": 20000.0}]
        result = vs.calcular_medias(anuncios)
        assert result["media_preco"] is None

    def test_rounding_two_decimals(self):
        # 100000 + 200001 = 300001 / 3 = 100000.333...
        anuncios = [{"preco": 100000.0}, {"preco": 100000.0}, {"preco": 100001.0}]
        result = vs.calcular_medias(anuncios)
        assert result["media_preco"] == round(300001.0 / 3, 2)

    def test_single_entry(self):
        anuncios = [{"preco": 75000.0, "km": 50000.0}]
        result = vs.calcular_medias(anuncios)
        assert result["media_preco"] == 75000.0
        assert result["media_km"] == 50000.0


# ===========================================================================
# keep_only_webmotors
# ===========================================================================

class TestKeepOnlyWebmotors:
    def _make_results(self, urls: list[str]) -> list[dict]:
        return [{"url": u, "title": f"Result {i}"} for i, u in enumerate(urls)]

    def test_keeps_webmotors_only(self):
        results = self._make_results([
            "https://www.webmotors.com.br/carro/usado/123",
            "https://www.olx.com.br/carro/456",
            "https://www.webmotors.com.br/carro/789",
            "https://www.icarros.com.br/",
        ])
        filtered = vs.keep_only_webmotors(results)
        assert len(filtered) == 2
        for r in filtered:
            assert "webmotors.com.br" in r["url"]

    def test_empty_list(self):
        assert vs.keep_only_webmotors([]) == []

    def test_no_webmotors_urls(self):
        results = self._make_results([
            "https://www.olx.com.br/123",
            "https://www.mercadolivre.com.br/456",
        ])
        assert vs.keep_only_webmotors(results) == []

    def test_all_webmotors_urls(self):
        results = self._make_results([
            "https://www.webmotors.com.br/a",
            "https://api.webmotors.com.br/b",
        ])
        assert len(vs.keep_only_webmotors(results)) == 2

    def test_entry_missing_url_key_excluded(self):
        results = [{"title": "no url here"}]
        # .get("url", "") -> "" -> "webmotors.com.br" not in "" -> excluded
        assert vs.keep_only_webmotors(results) == []

    def test_preserves_all_fields(self):
        results = [{"url": "https://www.webmotors.com.br/x", "content": "some text", "extra": 42}]
        filtered = vs.keep_only_webmotors(results)
        assert filtered[0]["content"] == "some text"
        assert filtered[0]["extra"] == 42


# ===========================================================================
# extract_vehicle_data
# ===========================================================================

class TestExtractVehicleData:
    def test_basic_extraction(self):
        text = "Honda City R$ 115.900 33.384 Km"
        result = vs.extract_vehicle_data(text)
        assert len(result) == 1
        assert result[0]["preco"] == 115900
        assert result[0]["km"] == 33384

    def test_multiple_pairs(self):
        text = "Carro A R$ 80.000 20.000 Km. Carro B R$ 95.000 45.000 Km"
        result = vs.extract_vehicle_data(text)
        assert len(result) == 2
        assert result[0]["preco"] == 80000
        assert result[0]["km"] == 20000
        assert result[1]["preco"] == 95000
        assert result[1]["km"] == 45000

    def test_empty_text_returns_empty_list(self):
        assert vs.extract_vehicle_data("") == []

    def test_price_only_no_km_returns_empty(self):
        # No km match -> min(len(precos), len(kms)) = 0 -> empty
        text = "R$ 100.000 sem km aqui"
        result = vs.extract_vehicle_data(text)
        assert result == []

    def test_km_only_no_price_returns_empty(self):
        text = "50.000 Km mas sem preco"
        result = vs.extract_vehicle_data(text)
        assert result == []

    def test_km_case_insensitive(self):
        # re.IGNORECASE: "km", "KM", "Km" all match
        text = "R$ 50.000 30.000 km"
        result = vs.extract_vehicle_data(text)
        assert len(result) == 1
        assert result[0]["km"] == 30000

    def test_price_without_dot_separator(self):
        text = "R$ 90000 15000 Km"
        result = vs.extract_vehicle_data(text)
        assert len(result) == 1
        assert result[0]["preco"] == 90000

    def test_preco_field_is_int(self):
        text = "R$ 115.900 33.384 Km"
        result = vs.extract_vehicle_data(text)
        assert isinstance(result[0]["preco"], int)
        assert isinstance(result[0]["km"], int)

    def test_pairs_zipped_by_position(self):
        # 3 prices but 2 km -> only 2 pairs produced
        text = "R$ 100.000 R$ 200.000 R$ 300.000 10.000 Km 20.000 Km"
        result = vs.extract_vehicle_data(text)
        assert len(result) == 2
