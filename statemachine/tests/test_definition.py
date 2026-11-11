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
    .replace("${payment_queue_url}", "https://sqs/payment")
)
ASL = json.loads(DEFINITION)
STATES = ASL["States"]
# The states inside the confirmation's parallel branches, too.
ALL_STATES = {**STATES, **{
    name: state
    for parallel in STATES.values() if parallel["Type"] == "Parallel"
    for branch in parallel["Branches"]
    for name, state in branch["States"].items()
}}
TASKS = {name: state for name, state in ALL_STATES.items() if state["Type"] == "Task"}
COMMANDS = {name: state for name, state in TASKS.items() if "waitForTaskToken" in state["Resource"]}
RESERVATIONS = ["ReserveFlight", "ReserveHotel"]
CONFIRMATIONS = ["ConfirmFlight", "ConfirmHotel"]
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


def longest_path_seconds(name, states=STATES):
    """The slowest way from this state to the end: every task on it at its worst case."""
    state = states[name]
    own = 0
    if state["Type"] == "Task":
        own = worst_case_seconds(state)
    elif state["Type"] == "Parallel":
        own = max(longest_path_seconds(branch["StartAt"], branch["States"]) for branch in state["Branches"])
    return own + max((longest_path_seconds(target, states) for target in next_states(state)), default=0)


def caught(state):
    return {catch["ErrorEquals"][0]: catch["Next"] for catch in state.get("Catch", [])}


def tasks_until_the_end(name):
    """The task states run from here, on the happy path of each, until the execution ends."""
    chain = []
    while "Next" in STATES[name]:
        if STATES[name]["Type"] == "Task":
            chain.append(name)
        name = STATES[name]["Next"]
    return chain, name


def test_every_target_exists_and_every_state_is_reachable():
    reachable, todo = set(), [ASL["StartAt"]]
    while todo:
        name = todo.pop()
        if name not in reachable:
            reachable.add(name)
            todo.extend(next_states(STATES[name]))

    assert reachable == set(STATES)


def test_the_happy_path():
    chain, end = tasks_until_the_end(ASL["StartAt"])

    assert chain == ["PublishTripRequested", "ReserveFlight", "ReserveHotel", "ChargePayment", "PublishTripConfirmed"]
    assert STATES["ChargePayment"]["Next"] == "ConfirmBooking"
    assert end == "TripConfirmed"


@pytest.mark.parametrize("name", TASKS)
def test_every_task_has_a_timeout(name):
    assert TASKS[name]["TimeoutSeconds"] > 0


def test_the_whole_saga_has_a_timeout():
    assert ASL["TimeoutSeconds"] == 900  # the lifetime of a hold


@pytest.mark.parametrize("name", RESERVATIONS)
def test_a_reservation_only_retries_vendor_unavailable(name):
    assert retried_errors(STATES[name]) == {"VendorUnavailable"}


@pytest.mark.parametrize("name", COMMANDS)
def test_retries_back_off_5_10_20(name):
    retry = COMMANDS[name]["Retry"][0]

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
        ("ChargePayment", "PaymentDeclined", ["ReleaseHotel", "ReleaseFlight"]),
        ("ChargePayment", "VendorUnavailable", ["ReleaseHotel", "ReleaseFlight"]),
    ],
)
def test_a_failure_before_the_pivot_releases_what_is_held_in_reverse(step, error, releases):
    chain, end = tasks_until_the_end(caught(STATES[step])[error])

    assert chain == releases + ["PublishTripCancelled"]
    assert end == "TripCancelled"


@pytest.mark.parametrize("name", ["ReleaseFlight", "ReleaseHotel"])
def test_a_compensation_is_retried_and_a_failed_one_fails_the_execution(name):
    release = STATES[name]

    assert retried_errors(release) == {"VendorUnavailable", "States.Timeout"}
    assert STATES[release["Catch"][0]["Next"]]["Type"] == "Fail"


def test_a_release_carries_the_cancellation_reason():
    for name in ("ReleaseFlight", "ReleaseHotel"):
        assert COMMANDS[name]["Parameters"]["MessageBody"]["data"] == {"reason.$": "$.cancellation.reason"}


def test_cancelling_does_not_fail_the_execution():
    assert STATES["TripCancelled"]["Type"] == "Succeed"


# ---------------------------------------------------------------- the pivot


def test_the_charge_is_the_sum_of_both_holds():
    data = STATES["ChargePayment"]["Parameters"]["MessageBody"]["data"]

    assert data["amount.$"] == "States.MathAdd($.flight_reservation.amount, $.hotel_reservation.amount)"
    assert data["payment_token.$"] == "$.payment_token"


def test_a_charge_with_no_answer_is_retried_then_left_to_a_person():
    charge = STATES["ChargePayment"]

    assert retried_errors(charge) == {"VendorUnavailable", "States.Timeout"}  # safe: idempotent on the trip ID
    chain, end = tasks_until_the_end(caught(charge)["States.Timeout"])
    assert chain == ["PublishTripNeedsAttention"]
    assert end == "NeedsAttention"


def test_both_bookings_are_confirmed_in_parallel():
    branches = STATES["ConfirmBooking"]["Branches"]

    assert [branch["StartAt"] for branch in branches] == CONFIRMATIONS


@pytest.mark.parametrize("name", CONFIRMATIONS)
def test_a_confirmation_is_retried_and_never_compensated(name):
    assert retried_errors(COMMANDS[name]) == {"VendorUnavailable", "States.Timeout"}
    assert "Catch" not in COMMANDS[name]
    chain, end = tasks_until_the_end(caught(STATES["ConfirmBooking"])["States.ALL"])
    assert not any(step.startswith("Release") for step in chain)
    assert end == "NeedsAttention"


def test_needs_attention_fails_the_execution_so_the_alarm_fires():
    assert STATES["NeedsAttention"]["Type"] == "Fail"


def test_the_longest_path_fits_in_the_saga_timeout():
    assert longest_path_seconds(ASL["StartAt"]) < ASL["TimeoutSeconds"]


# ---------------------------------------------------------------- messages


@pytest.mark.parametrize("name", COMMANDS)
def test_every_command_carries_the_token_and_the_trip(name):
    body = COMMANDS[name]["Parameters"]["MessageBody"]

    assert body["task_token.$"] == "$$.Task.Token"
    assert body["correlation_id.$"] == "$$.Execution.Name"
    assert body["message_kind"] == "command"
    assert body["message_type"] == name


@pytest.mark.parametrize("name", PUBLISHES)
def test_no_event_carries_a_token(name):
    parameters = json.dumps(PUBLISHES[name]["Parameters"])

    assert "Task.Token" not in parameters
    assert "payment_token" not in parameters


def test_only_the_payment_service_gets_the_card():
    assert [name for name in COMMANDS if "payment_token" in json.dumps(COMMANDS[name])] == ["ChargePayment"]


@pytest.mark.parametrize(
    "name, schema",
    [
        ("PublishTripRequested", "trip-requested"),
        ("PublishTripCancelled", "trip-cancelled"),
        ("PublishTripConfirmed", "trip-confirmed"),
        ("PublishTripNeedsAttention", "trip-needs-attention"),
    ],
)
def test_published_events_follow_their_schema(name, schema):
    entry = STATES[name]["Parameters"]["Entries"][0]
    required = json.loads((ROOT / f"docs/events/{schema}.schema.json").read_text(encoding="utf-8"))["required"]

    assert {key.removesuffix(".$") for key in entry["Detail"]["data"]} == set(required)
    assert entry["DetailType"] == entry["Detail"]["message_type"]
    assert entry["Source"] == "trip-conductor.booking-saga"


def test_cancellation_reasons_are_the_ones_in_the_requirements():
    reasons = {state["Result"]["reason"] for state in STATES.values() if "reason" in state.get("Result", {})}

    assert reasons == {"FLIGHT_UNAVAILABLE", "HOTEL_UNAVAILABLE", "PAYMENT_DECLINED", "VENDOR_UNAVAILABLE", "TIMEOUT"}
