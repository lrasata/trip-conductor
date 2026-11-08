"""Checks on the saga's definition. Its behaviour is exercised against AWS for now (ADR 0002)."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
DEFINITION = (
    (ROOT / "statemachine/booking.asl.json")
    .read_text(encoding="utf-8")
    .replace("${event_bus_name}", "bus")
    .replace("${flight_queue_url}", "https://sqs/flight")
)
ASL = json.loads(DEFINITION)
STATES = ASL["States"]
TASKS = {name: state for name, state in STATES.items() if state["Type"] == "Task"}
COMMANDS = {name: state for name, state in TASKS.items() if "waitForTaskToken" in state["Resource"]}
PUBLISHES = {name: state for name, state in TASKS.items() if "putEvents" in state["Resource"]}


def retried_errors(state):
    return {error for retry in state.get("Retry", []) for error in retry["ErrorEquals"]}


def next_states(state):
    targets = [state.get("Next"), state.get("Default")]
    targets += [choice["Next"] for choice in state.get("Choices", [])]
    targets += [catch["Next"] for catch in state.get("Catch", [])]
    return {target for target in targets if target}


def worst_case_seconds(state):
    """All attempts timing out, plus every backoff."""
    timeout = state["TimeoutSeconds"]
    attempts, waits = 1, 0
    for retry in state.get("Retry", []):
        attempts += retry["MaxAttempts"]
        waits += sum(retry["IntervalSeconds"] * retry["BackoffRate"] ** i for i in range(retry["MaxAttempts"]))
    return attempts * timeout + waits


def test_every_target_exists_and_every_state_is_reachable():
    reachable, todo = set(), [ASL["StartAt"]]
    while todo:
        name = todo.pop()
        if name not in reachable:
            reachable.add(name)
            todo.extend(next_states(STATES[name]))

    assert reachable == set(STATES)


@pytest.mark.parametrize("name", TASKS)
def test_every_task_has_a_timeout(name):
    assert TASKS[name]["TimeoutSeconds"] > 0


def test_the_whole_saga_has_a_timeout():
    assert ASL["TimeoutSeconds"] == 900  # the lifetime of a hold


def test_a_step_before_the_pivot_only_retries_vendor_unavailable():
    assert retried_errors(STATES["ReserveFlight"]) == {"VendorUnavailable"}


def test_vendor_unavailable_backs_off_30_60_120():
    retry = STATES["ReserveFlight"]["Retry"][0]

    assert (retry["IntervalSeconds"], retry["BackoffRate"], retry["MaxAttempts"]) == (30, 2, 3)


def test_business_errors_go_straight_to_cancellation():
    caught = {catch["ErrorEquals"][0]: catch["Next"] for catch in STATES["ReserveFlight"]["Catch"]}

    assert caught["FlightUnavailable"] == "FlightUnavailable"
    assert STATES["FlightUnavailable"]["Next"] == "PublishTripCancelled"


def test_a_timed_out_reservation_is_released():
    caught = {catch["ErrorEquals"][0]: catch["Next"] for catch in STATES["ReserveFlight"]["Catch"]}

    assert STATES[caught["States.Timeout"]]["Next"] == "ReleaseFlight"


def test_a_compensation_is_retried_and_a_failed_one_fails_the_execution():
    release = STATES["ReleaseFlight"]

    assert retried_errors(release) == {"VendorUnavailable", "States.Timeout"}
    assert STATES[release["Catch"][0]["Next"]]["Type"] == "Fail"


def test_cancelling_does_not_fail_the_execution():
    assert STATES["TripCancelled"]["Type"] == "Succeed"


def test_the_longest_path_fits_in_the_saga_timeout():
    release_path = worst_case_seconds(STATES["ReleaseFlight"]) + STATES["ReserveFlight"]["TimeoutSeconds"]
    vendor_path = worst_case_seconds(STATES["ReserveFlight"])
    publishes = sum(worst_case_seconds(state) for state in PUBLISHES.values())

    assert max(release_path, vendor_path) + publishes < ASL["TimeoutSeconds"]


@pytest.mark.parametrize("name", COMMANDS)
def test_every_command_carries_the_token_and_the_trip(name):
    body = COMMANDS[name]["Parameters"]["MessageBody"]

    assert body["task_token.$"] == "$$.Task.Token"
    assert body["correlation_id.$"] == "$$.Execution.Name"
    assert body["message_kind"] == "command"
    assert body["message_type"] == name


@pytest.mark.parametrize("name", PUBLISHES)
def test_no_event_carries_the_token(name):
    assert "Task.Token" not in json.dumps(PUBLISHES[name]["Parameters"])


@pytest.mark.parametrize(
    "name, schema",
    [("PublishTripRequested", "trip-requested"), ("PublishTripCancelled", "trip-cancelled")],
)
def test_published_events_follow_their_schema(name, schema):
    entry = STATES[name]["Parameters"]["Entries"][0]
    required = json.loads((ROOT / f"docs/events/{schema}.schema.json").read_text(encoding="utf-8"))["required"]

    assert {key.removesuffix(".$") for key in entry["Detail"]["data"]} == set(required)
    assert entry["DetailType"] == entry["Detail"]["message_type"]
    assert entry["Source"] == "trip-conductor.booking-saga"


def test_cancellation_reasons_are_the_ones_in_the_requirements():
    reasons = {state["Result"]["reason"] for state in STATES.values() if state["Type"] == "Pass"}

    assert reasons - {"TEST_RELEASE"} == {"FLIGHT_UNAVAILABLE", "VENDOR_UNAVAILABLE", "TIMEOUT"}
