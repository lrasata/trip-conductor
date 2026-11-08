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
    .replace("${hotel_queue_url}", "https://sqs/hotel")
)
ASL = json.loads(DEFINITION)
STATES = ASL["States"]
TASKS = {name: state for name, state in STATES.items() if state["Type"] == "Task"}
COMMANDS = {name: state for name, state in TASKS.items() if "waitForTaskToken" in state["Resource"]}
RESERVATIONS = {"ReserveFlight": "FlightUnavailable", "ReserveHotel": "HotelUnavailable"}  # step: business error
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


def longest_path_seconds(name):
    """The slowest way from this state to the end: every task on it at its worst case."""
    state = STATES[name]
    own = worst_case_seconds(state) if state["Type"] == "Task" else 0
    return own + max((longest_path_seconds(target) for target in next_states(state)), default=0)


def caught(state):
    return {catch["ErrorEquals"][0]: catch["Next"] for catch in state.get("Catch", [])}


def compensation_chain(name):
    """The task states run from here until the trip is cancelled."""
    chain = []
    while STATES[name]["Type"] != "Succeed":
        if STATES[name]["Type"] == "Task":
            chain.append(name)
        name = STATES[name]["Next"]
    return chain


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


@pytest.mark.parametrize("name", RESERVATIONS)
def test_a_step_before_the_pivot_only_retries_vendor_unavailable(name):
    assert retried_errors(STATES[name]) == {"VendorUnavailable"}


@pytest.mark.parametrize("name", COMMANDS)
def test_vendor_unavailable_backs_off_5_10_20(name):
    retry = STATES[name]["Retry"][0]

    assert (retry["IntervalSeconds"], retry["BackoffRate"], retry["MaxAttempts"]) == (5, 2, 3)


@pytest.mark.parametrize("name", COMMANDS)
def test_a_service_gets_20_seconds_to_answer(name):
    assert COMMANDS[name]["TimeoutSeconds"] == 20  # vendor calls give up after 3 s


@pytest.mark.parametrize(
    "step, error, releases",
    [
        ("ReserveFlight", "FlightUnavailable", []),
        ("ReserveFlight", "VendorUnavailable", []),
        ("ReserveFlight", "States.Timeout", ["ReleaseFlight"]),
        ("ReserveHotel", "HotelUnavailable", ["ReleaseFlight"]),
        ("ReserveHotel", "VendorUnavailable", ["ReleaseFlight"]),
        ("ReserveHotel", "States.Timeout", ["ReleaseHotel", "ReleaseFlight"]),
    ],
)
def test_a_failed_reservation_releases_what_is_held_in_reverse(step, error, releases):
    chain = compensation_chain(caught(STATES[step])[error])

    assert chain == releases + ["PublishTripCancelled"]


@pytest.mark.parametrize("name", ["ReleaseFlight", "ReleaseHotel"])
def test_a_compensation_is_retried_and_a_failed_one_fails_the_execution(name):
    release = STATES[name]

    assert retried_errors(release) == {"VendorUnavailable", "States.Timeout"}
    assert STATES[release["Catch"][0]["Next"]]["Type"] == "Fail"


def test_a_release_carries_the_cancellation_reason():
    for name in ("ReleaseFlight", "ReleaseHotel"):
        assert COMMANDS[name]["Parameters"]["MessageBody"]["data"] == {"reason.$": "$.cancellation.reason"}


def test_the_test_switch_releases_both_holds():
    assert compensation_chain("TestRelease") == ["ReleaseHotel", "ReleaseFlight", "PublishTripCancelled"]


def test_cancelling_does_not_fail_the_execution():
    assert STATES["TripCancelled"]["Type"] == "Succeed"


def test_the_longest_path_fits_in_the_saga_timeout():
    assert longest_path_seconds(ASL["StartAt"]) < ASL["TimeoutSeconds"]


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

    assert reasons - {"TEST_RELEASE"} == {"FLIGHT_UNAVAILABLE", "HOTEL_UNAVAILABLE", "VENDOR_UNAVAILABLE", "TIMEOUT"}
