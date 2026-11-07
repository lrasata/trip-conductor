"""A circuit breaker shared by every Lambda instance, with its state in DynamoDB (ADR 0005).

    breaker = Breaker(table, "airline", Config.from_env(), on_change=announce)
    hold = breaker.call(lambda: vendor.place_hold(...))

`fn` raises VendorFailure for what counts as a failure (timeouts, connection errors, 5xx).
A business refusal is a normal return: it means the vendor is healthy.

One item per vendor. Every transition is a conditional write, so under concurrency exactly
one caller makes it, and only that caller gets `on_change`. If DynamoDB is unreachable the
breaker fails open: losing protection is better than refusing every booking.
"""

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from botocore.exceptions import BotoCoreError, ClientError

CLOSED = "CLOSED"
OPEN = "OPEN"
HALF_OPEN = "HALF_OPEN"

T = TypeVar("T")


class VendorFailure(Exception):
    """The vendor timed out, was unreachable or answered with a server error."""


class CircuitOpen(Exception):
    """The breaker is open: the vendor was not called."""


@dataclass(frozen=True)
class Config:
    failure_threshold: int = 5
    window_seconds: int = 60
    cooldown_seconds: int = 30
    # A probe that hasn't reported after this long (its Lambda crashed) can be taken over,
    # otherwise the breaker would stay half-open forever.
    probe_lease_seconds: int = 30

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            failure_threshold=int(os.environ.get("BREAKER_FAILURE_THRESHOLD", cls.failure_threshold)),
            window_seconds=int(os.environ.get("BREAKER_WINDOW_SECONDS", cls.window_seconds)),
            cooldown_seconds=int(os.environ.get("BREAKER_COOLDOWN_SECONDS", cls.cooldown_seconds)),
            probe_lease_seconds=int(os.environ.get("BREAKER_PROBE_LEASE_SECONDS", cls.probe_lease_seconds)),
        )


class Breaker:
    def __init__(
        self,
        table,
        vendor: str,
        config: Config = Config(),
        on_change: Callable[[str, int], None] = lambda change, failure_count: None,
        on_store_error: Callable[[Exception], None] = lambda exc: None,
        clock: Callable[[], float] = time.time,
    ):
        self._table = table
        self._vendor = vendor
        self._config = config
        self._on_change = on_change  # called with "opened" or "closed", and the failure count
        self._on_store_error = on_store_error
        self._clock = clock

    def call(self, fn: Callable[[], T]) -> T:
        probe = self._admit()
        try:
            result = fn()
        except VendorFailure:
            self._guarded(self._record_failure, probe)
            raise
        self._guarded(self._record_success, probe)
        return result

    def _now(self) -> int:
        return int(self._clock())

    def _guarded(self, record: Callable[[int | None], None], probe: int | None) -> None:
        try:
            record(probe)
        except (BotoCoreError, ClientError) as exc:
            self._on_store_error(exc)

    def _update(self, update: str, condition: str, values: dict, names: dict | None = None) -> dict | None:
        """A conditional write. Returns the new item, or None if the condition failed."""
        kwargs = {"ExpressionAttributeNames": names} if names else {}
        try:
            return self._table.update_item(
                Key={"vendor": self._vendor},
                UpdateExpression=update,
                ConditionExpression=condition,
                ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
                **kwargs,
            )["Attributes"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise

    def _admit(self) -> int | None:
        """Let the call through, or raise CircuitOpen. Returns the probe's start time if this
        caller is the probe, None for a normal call."""
        try:
            item = self._table.get_item(Key={"vendor": self._vendor}, ConsistentRead=True).get("Item", {})
            state = item.get("state", CLOSED)
            if state == CLOSED:
                return None

            since_attr, wait = (
                ("opened_at", self._config.cooldown_seconds)
                if state == OPEN
                else ("probe_started_at", self._config.probe_lease_seconds)
            )
            now = self._now()
            if now < int(item[since_attr]) + wait:
                raise CircuitOpen(self._vendor)

            claimed = self._update(
                "SET #state = :half_open, probe_started_at = :now",
                "#state = :seen_state AND #since = :seen_since",
                {
                    ":half_open": HALF_OPEN,
                    ":now": now,
                    ":seen_state": state,
                    ":seen_since": item[since_attr],
                },
                {"#state": "state", "#since": since_attr},
            )
            if claimed is None:
                raise CircuitOpen(self._vendor)  # another caller won the probe
            return now
        except (BotoCoreError, ClientError) as exc:
            self._on_store_error(exc)
            return None

    def _record_success(self, probe: int | None) -> None:
        if probe is None:
            return
        closed = self._update(
            "SET #state = :closed, failure_count = :zero REMOVE opened_at, probe_started_at, window_started_at",
            "#state = :half_open AND probe_started_at = :probe",
            {":closed": CLOSED, ":zero": 0, ":half_open": HALF_OPEN, ":probe": probe},
            {"#state": "state"},
        )
        if closed is not None:
            self._on_change("closed", 0)

    def _record_failure(self, probe: int | None) -> None:
        if probe is not None:
            # Not announced: consumers were told it opened, and it never closed.
            self._update(
                "SET #state = :open, opened_at = :now REMOVE probe_started_at",
                "#state = :half_open AND probe_started_at = :probe",
                {":open": OPEN, ":now": self._now(), ":half_open": HALF_OPEN, ":probe": probe},
                {"#state": "state"},
            )
            return

        now = self._now()
        cutoff = now - self._config.window_seconds
        item = self._update(
            "ADD failure_count :one",
            "#state = :closed AND window_started_at > :cutoff",
            {":one": 1, ":closed": CLOSED, ":cutoff": cutoff},
            {"#state": "state"},
        )
        if item is None:  # first failure, or the window is over: start a new one
            item = self._update(
                "SET #state = :closed, failure_count = :one, window_started_at = :now",
                "(attribute_not_exists(#state) OR #state = :closed) "
                "AND (attribute_not_exists(window_started_at) OR window_started_at <= :cutoff)",
                {":closed": CLOSED, ":one": 1, ":now": now, ":cutoff": cutoff},
                {"#state": "state"},
            )
        if item is None or int(item["failure_count"]) < self._config.failure_threshold:
            return

        opened = self._update(
            "SET #state = :open, opened_at = :now",
            "#state = :closed",
            {":open": OPEN, ":now": now, ":closed": CLOSED},
            {"#state": "state"},
        )
        if opened is not None:
            self._on_change("opened", int(item["failure_count"]))
