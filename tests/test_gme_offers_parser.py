"""Parsing the GME public-offers XML across the pre- and post-2025 schemas."""

import io
import zipfile

import pytest

from openeuphemia.gme.offers import parse_mgp_offers_zip

XML_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<NewDataSet>
  <OfferteOperatori>
    <PURPOSE_CD>OFF</PURPOSE_CD>
    <TYPE_CD>REG</TYPE_CD>
    <STATUS_CD>ACC</STATUS_CD>
    <MARKET_CD>MGP</MARKET_CD>
    <UNIT_REFERENCE_NO>UP_TEST_1</UNIT_REFERENCE_NO>
    <{period_tag}>1</{period_tag}>
    <BID_OFFER_DATE_DT>20250401</BID_OFFER_DATE_DT>
    <TRANSACTION_REFERENCE_NO>1</TRANSACTION_REFERENCE_NO>
    <QUANTITY_NO>10.5</QUANTITY_NO>
    <AWARDED_QUANTITY_NO>10.5</AWARDED_QUANTITY_NO>
    <ENERGY_PRICE_NO>55.25</ENERGY_PRICE_NO>
    <MERIT_ORDER_NO>42</MERIT_ORDER_NO>
    <PARTIAL_QTY_ACCEPTED_IN>N</PARTIAL_QTY_ACCEPTED_IN>
    <ADJ_QUANTITY_NO>10.5</ADJ_QUANTITY_NO>
    <ADJ_ENERGY_PRICE_NO>55.25</ADJ_ENERGY_PRICE_NO>
    <ZONE_CD>NORD</ZONE_CD>
    <AWARDED_PRICE_NO>60.0</AWARDED_PRICE_NO>
    <OPERATORE>TEST OPERATOR</OPERATORE>
    <SUBMITTED_DT>20250331093000000</SUBMITTED_DT>
    <BILATERAL_IN>false</BILATERAL_IN>
    {extra}
  </OfferteOperatori>
</NewDataSet>
"""


def offers_zip(period_tag: str, extra: str = "") -> bytes:
    xml = XML_TEMPLATE.format(period_tag=period_tag, extra=extra)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("20250401MGPOffertePubbliche.xml", xml)
    return buffer.getvalue()


@pytest.mark.parametrize("period_tag", ["INTERVAL_NO", "PERIOD"])
def test_parses_both_period_schemas(period_tag):
    frame = parse_mgp_offers_zip(offers_zip(period_tag), delivery_day="2025-04-01")
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["delivery_date"] == "2025-04-01"
    assert row["interval_no"] == 1
    assert row["zone_cd"] == "NORD"
    assert row["energy_price_eur_per_mwh"] == 55.25
    assert row["adj_quantity_mw"] == 10.5
    assert row["merit_order_no"] == 42
    assert row["bilateral_in"] == False  # noqa: E712 — pandas stores np.bool_


def test_parses_2025_offer_type_field():
    payload = offers_zip("PERIOD", extra="<OFFER_TYPE>B</OFFER_TYPE>")
    frame = parse_mgp_offers_zip(payload)
    assert frame.iloc[0]["offer_type"] == "B"


def test_rejects_mismatched_delivery_day():
    with pytest.raises(ValueError):
        parse_mgp_offers_zip(offers_zip("PERIOD"), delivery_day="2025-04-02")
