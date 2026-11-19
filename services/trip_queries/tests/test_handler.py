import json

import pytest
from conftest import api_event, stored_view

from trip_queries_service.handler import handler


def call(event):
    response = handler(event, None)
    return response["statusCode"], json.loads(response["body"])


def test_a_trip_is_returned_without_internal_fields(table):
    table.put_item(Item=stored_view())

    status, body = call(api_event("trp_123"))

    assert status == 200
    assert body["status"] == "CONFIRMED"
    assert body["total_amount"] == 61800
    assert body["legs"]["flight"]["booking_reference"] == "FL-ABC"
    assert [entry["type"] for entry in body["timeline"]] == ["TripRequested", "TripConfirmed"]
    for hidden in ("applied_message_ids", "version", "attention", "flight_offer_id"):
        assert hidden not in body


def test_an_unknown_trip_is_a_404(table):
    status, body = call(api_event("trp_unknown"))

    assert status == 404
    assert body["error"] == "not_found"


def test_trips_are_not_cached(table):
    table.put_item(Item=stored_view())

    assert handler(api_event("trp_123"), None)["headers"]["Cache-Control"] == "no-store"


def test_a_customers_trips_are_listed_newest_first(table):
    table.put_item(Item=stored_view("trp_old", created_at="2026-11-01T10:00:00.000Z"))
    table.put_item(Item=stored_view("trp_new", created_at="2026-11-18T10:00:00.000Z", status="CANCELLED"))
    table.put_item(Item=stored_view("trp_other", customer_id="cus_other"))

    status, body = call(api_event(customer_id="cus_demo"))

    assert status == 200
    assert [trip["trip_id"] for trip in body["trips"]] == ["trp_new", "trp_old"]
    assert body["trips"][0]["status"] == "CANCELLED"
    assert "legs" not in body["trips"][0]
    assert body["next"] is None


def test_the_list_is_paged(table):
    for day in range(1, 6):
        table.put_item(Item=stored_view(f"trp_{day}", created_at=f"2026-11-0{day}T10:00:00.000Z"))

    seen, token = [], None
    while True:
        query = {"customer_id": "cus_demo", "limit": "2", **({"next": token} if token else {})}
        status, body = call(api_event(**query))
        assert status == 200
        seen += [trip["trip_id"] for trip in body["trips"]]
        token = body["next"]
        if token is None:
            break

    assert seen == ["trp_5", "trp_4", "trp_3", "trp_2", "trp_1"]


def test_a_paging_token_only_works_for_its_customer(table):
    for day in range(1, 4):
        table.put_item(Item=stored_view(f"trp_{day}", created_at=f"2026-11-0{day}T10:00:00.000Z"))
    _, body = call(api_event(customer_id="cus_demo", limit="1"))

    status, _ = call(api_event(customer_id="cus_other", next=body["next"]))

    assert status == 400


@pytest.mark.parametrize(
    "event",
    [
        api_event(),
        api_event(customer_id="not valid!"),
        api_event(customer_id="cus_demo", limit="0"),
        api_event(customer_id="cus_demo", limit="51"),
        api_event(customer_id="cus_demo", limit="ten"),
        api_event(customer_id="cus_demo", next="not-a-token"),
        api_event("trp with spaces"),
    ],
)
def test_bad_requests_are_400(table, event):
    status, body = call(event)

    assert status == 400
    assert body["error"] == "bad_request"
