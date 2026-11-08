import boto3
import pytest
from botocore.exceptions import EndpointConnectionError
from moto import mock_aws

from circuit_breaker import CLOSED, HALF_OPEN, OPEN, Breaker, CircuitOpen, Config, VendorFailure

CONFIG = Config(failure_threshold=3, window_seconds=60, cooldown_seconds=30, probe_lease_seconds=20)


class Clock:
    def __init__(self):
        self.now = 1_000_000

    def __call__(self):
        return self.now


@pytest.fixture
def table(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    with mock_aws():
        yield boto3.resource("dynamodb").create_table(
            TableName="trip-conductor-test-circuit-breakers",
            KeySchema=[{"AttributeName": "vendor", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "vendor", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def changes():
    return []


@pytest.fixture
def breaker(table, clock, changes):
    return Breaker(table, "airline", CONFIG, on_change=lambda *change: changes.append(change), clock=clock)


def fail():
    raise VendorFailure("503")


def fail_times(breaker, count):
    for _ in range(count):
        with pytest.raises(VendorFailure):
            breaker.call(fail)


def stored_state(table):
    return table.get_item(Key={"vendor": "airline"}).get("Item", {}).get("state")


def test_a_healthy_vendor_is_called_and_writes_nothing(breaker, table):
    assert breaker.call(lambda: "held") == "held"

    assert "Item" not in table.get_item(Key={"vendor": "airline"})


def test_a_business_refusal_is_not_a_failure(breaker, table):
    for _ in range(10):
        assert breaker.call(lambda: None) is None

    assert stored_state(table) is None


def test_stays_closed_below_the_threshold(breaker, table, changes):
    fail_times(breaker, 2)

    assert stored_state(table) == CLOSED
    assert changes == []


def test_opens_at_the_threshold_and_announces_it_once(breaker, table, changes):
    fail_times(breaker, 3)

    assert stored_state(table) == OPEN
    assert changes == [("opened", 3)]


def test_failures_outside_the_window_do_not_add_up(breaker, table, clock):
    fail_times(breaker, 2)
    clock.now += 61
    fail_times(breaker, 2)

    assert stored_state(table) == CLOSED


def test_open_fails_fast_without_calling_the_vendor(breaker):
    fail_times(breaker, 3)
    called = []

    with pytest.raises(CircuitOpen):
        breaker.call(lambda: called.append(True))

    assert called == []


def test_a_successful_probe_closes_the_breaker(breaker, table, clock, changes):
    fail_times(breaker, 3)
    clock.now += 30

    assert breaker.call(lambda: "held") == "held"

    assert stored_state(table) == CLOSED
    assert changes == [("opened", 3), ("closed", 0)]
    fail_times(breaker, 2)  # the failure count started over
    assert stored_state(table) == CLOSED


def test_a_failed_probe_reopens_without_a_second_announcement(breaker, table, clock, changes):
    fail_times(breaker, 3)
    clock.now += 30

    fail_times(breaker, 1)

    assert stored_state(table) == OPEN
    assert changes == [("opened", 3)]
    with pytest.raises(CircuitOpen):  # a new cool-down started
        breaker.call(lambda: "held")


def test_only_one_caller_gets_the_probe(breaker, table, clock):
    fail_times(breaker, 3)
    clock.now += 30
    other_caller = Breaker(table, "airline", CONFIG, clock=clock)
    others = []

    def probe():
        with pytest.raises(CircuitOpen):
            other_caller.call(lambda: others.append(True))
        assert stored_state(table) == HALF_OPEN
        return "held"

    breaker.call(probe)

    assert others == []


def test_a_crashed_probe_can_be_taken_over(breaker, table, clock):
    fail_times(breaker, 3)
    clock.now += 30

    class Crash(Exception):
        pass

    def crash():
        raise Crash

    with pytest.raises(Crash):
        breaker.call(crash)
    assert stored_state(table) == HALF_OPEN

    with pytest.raises(CircuitOpen):  # still within the lease
        breaker.call(lambda: "held")

    clock.now += 20
    assert breaker.call(lambda: "held") == "held"
    assert stored_state(table) == CLOSED


def test_the_late_answer_of_a_replaced_probe_changes_nothing(breaker, table, clock):
    fail_times(breaker, 3)
    clock.now += 30
    late_caller = Breaker(table, "airline", CONFIG, clock=clock)

    def replaced_while_running():
        clock.now += 20
        with pytest.raises(VendorFailure):
            late_caller.call(fail)  # takes the probe over and reopens
        raise VendorFailure("timeout")

    with pytest.raises(VendorFailure):
        breaker.call(replaced_while_running)

    item = table.get_item(Key={"vendor": "airline"})["Item"]
    assert item["state"] == OPEN
    assert int(item["opened_at"]) == clock.now


def test_vendors_do_not_share_a_breaker(breaker, table, clock):
    fail_times(breaker, 3)

    hotel = Breaker(table, "hotel", CONFIG, clock=clock)

    assert hotel.call(lambda: "held") == "held"


class BrokenTable:
    def get_item(self, **kwargs):
        raise EndpointConnectionError(endpoint_url="https://dynamodb")

    update_item = get_item


def test_fails_open_when_dynamodb_is_unreachable():
    errors = []
    breaker = Breaker(BrokenTable(), "airline", CONFIG, on_store_error=errors.append)

    assert breaker.call(lambda: "held") == "held"
    with pytest.raises(VendorFailure):
        breaker.call(fail)

    assert len(errors) == 3  # the read before each call, and recording the failure


def test_config_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("BREAKER_FAILURE_THRESHOLD", "2")
    monkeypatch.setenv("BREAKER_COOLDOWN_SECONDS", "5")

    config = Config.from_env()

    assert (config.failure_threshold, config.cooldown_seconds, config.window_seconds) == (2, 5, 60)
