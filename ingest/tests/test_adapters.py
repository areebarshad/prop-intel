"""Open-data adapters.

Offline: responses are stubbed with respx, so the suite never depends on a
county server being up. The fixtures are shaped like the real payloads —
including ArcGIS's habit of returning epoch-millisecond dates and reporting
query errors inside an HTTP 200.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from ingest.apis import available_adapters, get_adapter
from ingest.apis.arcgis import ArcGISAdapter
from ingest.apis.base import epoch_millis_to_date, parse_iso_date, to_float
from ingest.apis.socrata import SocrataAdapter

SERVICE = "https://example.test/rest/services/LDS/DevelopmentTracker/FeatureServer"
QUERY_URL = f"{SERVICE}/4/query"


def _feature(**attrs: object) -> dict[str, object]:
    base = {
        "RECORDID": "BLDR-260001",
        "APPTYPEALIAS": "Commercial New",
        "PROJECT_NAME": "Stanley Martin Companies",
        "PARCEL_ID": "0713 01 0008A",
        "RECORD_STATUS": "Issued",
        "ESTIMATED_COST": 5_000_000.0,
        "SUBMITTED_DATE": 1613433600000,
        "ISSUED_DATE": 1629158400000,
        "MAR_ADDRESS": "7312 BRADDOCK RD, ANNANDALE, VA",
    }
    base.update(attrs)
    return {"attributes": base, "geometry": {"x": -77.2, "y": 38.8}}


class TestValueParsing:
    def test_epoch_millis_becomes_a_date(self) -> None:
        assert epoch_millis_to_date(1629158400000) == date(2021, 8, 17)

    @pytest.mark.parametrize("bad", [None, "", 0, "not-a-number"])
    def test_unparseable_epoch_is_none_not_an_exception(self, bad: object) -> None:
        """A single malformed date must not abort a 500-record page."""
        assert epoch_millis_to_date(bad) is None

    def test_iso_date(self) -> None:
        assert parse_iso_date("2026-03-14T00:00:00") == date(2026, 3, 14)

    def test_us_format_date(self) -> None:
        assert parse_iso_date("03/14/2026") == date(2026, 3, 14)

    def test_unparseable_iso_is_none(self) -> None:
        assert parse_iso_date("last thursday") is None

    def test_to_float_handles_junk(self) -> None:
        assert to_float("abc") is None
        assert to_float("1200.5") == 1200.5


class TestAdapterRegistry:
    def test_both_adapters_are_registered(self) -> None:
        assert available_adapters() == ["arcgis", "socrata"]

    def test_unknown_adapter_names_the_available_ones(self) -> None:
        with pytest.raises(KeyError, match="arcgis"):
            get_adapter("carrier-pigeon", "Fairfax", {})


class TestArcGISValidation:
    def test_missing_service_url_is_caught_before_any_request(self) -> None:
        """A misconfigured source should name the missing key, not 404."""
        with pytest.raises(ValueError, match="service_url"):
            ArcGISAdapter("Fairfax", {"layer_id": 0}).validate()

    def test_missing_layer_id_is_caught(self) -> None:
        with pytest.raises(ValueError, match="layer_id"):
            ArcGISAdapter("Fairfax", {"service_url": SERVICE}).validate()

    def test_layer_zero_is_valid(self) -> None:
        """0 is falsy; a naive check would reject the first layer."""
        ArcGISAdapter("Fairfax", {"service_url": SERVICE, "layer_id": 0}).validate()


class TestArcGISFetch:
    @pytest.fixture
    def adapter(self) -> ArcGISAdapter:
        return ArcGISAdapter("Fairfax", {"service_url": SERVICE, "layer_id": 4})

    @respx.mock
    async def test_maps_fields_onto_a_permit_record(self, adapter: ArcGISAdapter) -> None:
        respx.get(QUERY_URL).mock(return_value=httpx.Response(200, json={"features": [_feature()]}))

        records = [r async for r in adapter.fetch()]

        assert len(records) == 1
        record = records[0]
        assert record.permit_number == "BLDR-260001"
        assert record.permit_type == "Commercial New"
        assert record.applicant_name_raw == "Stanley Martin Companies"
        assert record.valuation_usd == 5_000_000.0
        assert record.issued_date == date(2021, 8, 17)
        assert record.locality == "Fairfax"

    @respx.mock
    async def test_records_which_column_the_name_came_from(self, adapter: ArcGISAdapter) -> None:
        """Attribution has to be auditable.

        Fairfax has no applicant column, so a name may come from PROJECT_NAME —
        which often names the project or tenant rather than the developer.
        Recording the field is what makes a wrong link explicable later.
        """
        respx.get(QUERY_URL).mock(return_value=httpx.Response(200, json={"features": [_feature()]}))

        records = [r async for r in adapter.fetch()]

        assert records[0].mention_field == "PROJECT_NAME"

    @respx.mock
    async def test_geometry_becomes_lat_lon(self, adapter: ArcGISAdapter) -> None:
        respx.get(QUERY_URL).mock(return_value=httpx.Response(200, json={"features": [_feature()]}))

        record = [r async for r in adapter.fetch()][0]

        assert record.lat == 38.8
        assert record.lon == -77.2

    @respx.mock
    async def test_a_record_without_a_permit_number_is_dropped(
        self, adapter: ArcGISAdapter
    ) -> None:
        """No natural key means a re-crawl would insert a second copy."""
        respx.get(QUERY_URL).mock(
            return_value=httpx.Response(200, json={"features": [_feature(RECORDID=None)]})
        )

        assert [r async for r in adapter.fetch()] == []

    @respx.mock
    async def test_a_record_with_no_name_still_loads(self, adapter: ArcGISAdapter) -> None:
        """Most Fairfax permits have no usable name; they are still real filings."""
        respx.get(QUERY_URL).mock(
            return_value=httpx.Response(200, json={"features": [_feature(PROJECT_NAME=None)]})
        )

        record = [r async for r in adapter.fetch()][0]

        assert record.applicant_name_raw is None
        assert not record.has_mention

    @respx.mock
    async def test_an_error_inside_a_200_is_raised(self, adapter: ArcGISAdapter) -> None:
        """ArcGIS reports query errors with HTTP 200 and an `error` key.

        Treating that as an empty result would silently report zero permits for
        a locality that is actually misconfigured.
        """
        respx.get(QUERY_URL).mock(
            return_value=httpx.Response(
                200, json={"error": {"code": 400, "message": "Invalid field"}}
            )
        )

        with pytest.raises(RuntimeError, match="ArcGIS error"):
            [r async for r in adapter.fetch()]

    @respx.mock
    async def test_limit_stops_early(self, adapter: ArcGISAdapter) -> None:
        respx.get(QUERY_URL).mock(
            return_value=httpx.Response(
                200,
                json={"features": [_feature(RECORDID=f"BLDR-{i}") for i in range(10)]},
            )
        )

        assert len([r async for r in adapter.fetch(limit=3)]) == 3

    @respx.mock
    async def test_empty_response_terminates(self, adapter: ArcGISAdapter) -> None:
        respx.get(QUERY_URL).mock(return_value=httpx.Response(200, json={"features": []}))

        assert [r async for r in adapter.fetch()] == []

    @respx.mock
    async def test_since_is_pushed_into_the_where_clause(self) -> None:
        """Filtering server-side, not client-side, keeps the payload small."""
        adapter = ArcGISAdapter(
            "Fairfax",
            {"service_url": SERVICE, "layer_id": 4, "date_field": "ISSUED_DATE"},
        )
        route = respx.get(QUERY_URL).mock(return_value=httpx.Response(200, json={"features": []}))

        [r async for r in adapter.fetch(since=date(2026, 1, 1))]

        where = route.calls[0].request.url.params["where"]
        assert "ISSUED_DATE >= DATE '2026-01-01'" in where

    @respx.mock
    async def test_a_custom_field_map_overrides_the_default(self) -> None:
        adapter = ArcGISAdapter(
            "Henrico",
            {
                "service_url": SERVICE,
                "layer_id": 4,
                "field_map": {"RECORDID": "permit_number", "OWNER": "applicant_name_raw"},
            },
        )
        respx.get(QUERY_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "attributes": {
                                "RECORDID": "X-1",
                                "OWNER": "Gumenick Properties",
                                "PROJECT_NAME": "ignored",
                            }
                        }
                    ]
                },
            )
        )

        record = [r async for r in adapter.fetch()][0]

        assert record.applicant_name_raw == "Gumenick Properties"
        assert record.mention_field == "OWNER"


class TestSocrata:
    def test_missing_dataset_id_is_caught(self) -> None:
        with pytest.raises(ValueError, match="dataset_id"):
            SocrataAdapter("Richmond City", {"domain": "data.richmondgov.com"}).validate()

    def test_missing_domain_is_caught(self) -> None:
        with pytest.raises(ValueError, match="domain"):
            SocrataAdapter("Richmond City", {"dataset_id": "abcd-1234"}).validate()

    @respx.mock
    async def test_maps_rows_onto_permit_records(self) -> None:
        adapter = SocrataAdapter(
            "Richmond City",
            {
                "domain": "data.test",
                "dataset_id": "abcd-1234",
                "field_map": {
                    "permit_number": "permit_number",
                    "owner_name": "applicant_name_raw",
                    "issued": "issued_date",
                    "value": "valuation_usd",
                },
            },
        )
        respx.get("https://data.test/resource/abcd-1234.json").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "permit_number": "B-2026-001",
                        "owner_name": "Thalhimer Realty Partners",
                        "issued": "2026-03-14T00:00:00",
                        "value": "2500000",
                    }
                ],
            )
        )

        record = [r async for r in adapter.fetch()][0]

        assert record.permit_number == "B-2026-001"
        assert record.applicant_name_raw == "Thalhimer Realty Partners"
        assert record.issued_date == date(2026, 3, 14)
        assert record.valuation_usd == 2_500_000.0
