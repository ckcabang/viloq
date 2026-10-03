"""What people do with the app, as counted metrics, beside the HTTP and
connection-pool metrics the instrumentation records on its own.

Each one answers a question the request metrics cannot: is anyone signing up,
do sign-in links fail and why, which splits do people use, how often do two
people edit the same record at once, and how large do groups grow.

They carry no attribute that says where they came from: like every other
metric, they are sent with the resource from `backend.telemetry`, which names
the environment (`deployment.environment.name`) and the deployed image
(`service.version`). Their own attributes are kept to a few fixed values each,
never an id, an email or an amount, so the series stay few and say nothing
about anyone.

Every series a counter can have is started at zero as the process starts.
A series otherwise appears with its first count, and `rate` and `increase`
take nothing from a series' first sample: the first sign-up, group or
expense after each deploy (which starts new series, labelled with the new
version) would never show, nor, errors being rare, most errors at all. Errors
are started for every code on every route in the contract, some 170 series.

Until `Telemetry.instrument` hands the app a real `MeterProvider`, every
method here records into a no-op one, so the routers can call them
unconditionally.
"""

from __future__ import annotations

from collections.abc import Iterable
from itertools import product
from typing import Literal, get_args

from fastapi import FastAPI, Request
from fastapi.routing import APIRoute, iter_route_contexts
from opentelemetry.metrics import MeterProvider, NoOpMeterProvider

from backend.schemas import SplitType, Strategy

SignInResult = Literal[
    # A new account, or a returning one.
    "signed_up",
    "signed_in",
    # Refused: no such link, one already used, or one that ran out of time.
    # A rise in `used_link` is the mark of a mail scanner opening links first.
    "unknown_link",
    "used_link",
    "expired_link",
]

Operation = Literal["create", "update", "delete"]

# The `code` of an error response: those `openapi.yaml` lists for `Error`.
ErrorCode = Literal[
    "validation",
    "unauthorized",
    "forbidden",
    "not_found",
    "version_conflict",
    "currency_locked",
    "invalid_token",
    "expired_token",
    "invite_revoked",
    "precondition_required",
    "internal",
]

# The expense counts a group's dashboard returns, unpaginated, on every load.
_GROUP_EXPENSE_BUCKETS = (0, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000)


class Metrics:
    """The app's own instruments, recording into `provider`.

    `routes` are the templates errors are counted by, from `api_routes`.
    """

    def __init__(
        self, provider: MeterProvider | None = None, routes: Iterable[str] = ()
    ) -> None:
        meter = (provider or NoOpMeterProvider()).get_meter("viloq")
        self._magic_links = meter.create_counter(
            "viloq.magic_links.requested",
            unit="{link}",
            description="Sign-in links asked for from the sign-in page.",
        )
        self._sign_ins = meter.create_counter(
            "viloq.sign_ins",
            unit="{sign_in}",
            description="Sign-in links followed, by whether they let someone in.",
        )
        self._groups = meter.create_counter(
            "viloq.groups.created",
            unit="{group}",
            description="Groups created.",
        )
        self._joins = meter.create_counter(
            "viloq.members.joined",
            unit="{member}",
            description="People who joined a group with its invite code.",
        )
        self._expenses = meter.create_counter(
            "viloq.expense.changes",
            unit="{expense}",
            description="Expenses created, updated or deleted, by split type.",
        )
        self._payments = meter.create_counter(
            "viloq.payment.changes",
            unit="{payment}",
            description="Payments recorded, updated or deleted.",
        )
        self._settlements = meter.create_counter(
            "viloq.settlements.suggested",
            unit="{settlement}",
            description="Settlement suggestions shown, by strategy.",
        )
        self._group_expenses = meter.create_histogram(
            "viloq.group.expenses",
            unit="{expense}",
            description="Expenses in a group each time its dashboard loads.",
            explicit_bucket_boundaries_advisory=_GROUP_EXPENSE_BUCKETS,
        )
        self._errors = meter.create_counter(
            "viloq.api.errors",
            unit="{error}",
            description="Error responses, by the contract's error code.",
        )
        self._start_at_zero(routes)

    def _start_at_zero(self, routes: Iterable[str]) -> None:
        for counter in (self._magic_links, self._groups, self._joins):
            counter.add(0)
        for result in get_args(SignInResult):
            self._sign_ins.add(0, {"viloq.sign_in.result": result})
        for operation, split_type in product(get_args(Operation), get_args(SplitType)):
            self._expenses.add(
                0, {"viloq.operation": operation, "viloq.split_type": split_type}
            )
        for operation in get_args(Operation):
            self._payments.add(0, {"viloq.operation": operation})
        for strategy in get_args(Strategy):
            self._settlements.add(0, {"viloq.settlement.strategy": strategy})
        for route, code in product(routes, get_args(ErrorCode)):
            self._errors.add(0, {"error.type": code, "http.route": route})
        # The one error a path that matched no route gets.
        self._errors.add(0, {"error.type": "not_found"})

    def magic_link_requested(self) -> None:
        self._magic_links.add(1)

    def sign_in(self, result: SignInResult) -> None:
        self._sign_ins.add(1, {"viloq.sign_in.result": result})

    def group_created(self) -> None:
        self._groups.add(1)

    def member_joined(self) -> None:
        self._joins.add(1)

    def expense_changed(self, operation: Operation, split_type: str) -> None:
        self._expenses.add(
            1, {"viloq.operation": operation, "viloq.split_type": split_type}
        )

    def payment_changed(self, operation: Operation) -> None:
        self._payments.add(1, {"viloq.operation": operation})

    def settlement_suggested(self, strategy: str) -> None:
        self._settlements.add(1, {"viloq.settlement.strategy": strategy})

    def group_loaded(self, expense_count: int) -> None:
        self._group_expenses.record(expense_count)

    def error(self, request: Request, code: str) -> None:
        """An error response with the contract's `code`, e.g. `version_conflict`.

        The status code alone runs several apart: a 409 is a lost edit race or
        a locked currency, a 400 a bad form or a dead sign-in link. The route is
        its template (`/api/v1/groups/{groupId}`), and left out for a path that
        matched none, so a scan of random URLs adds no series.
        """
        attributes = {"error.type": code}
        route = _route_template(request)
        if route:
            attributes["http.route"] = route
        self._errors.add(1, attributes)


def _route_template(request: Request) -> str | None:
    """The full template of the route that matched `request`, if one did.

    The matched route's own `path` lacks the prefix it was included under
    (`/api/v1`), so it is looked up among the app's routes as included, as the
    FastAPI instrumentation does for its `http.route`.
    """
    matched = request.scope.get("route")
    if matched is None:
        return None
    for context in iter_route_contexts(request.app.routes):
        if context.original_route is matched:
            return context.path
    return getattr(matched, "path", None)


def api_routes(app: FastAPI) -> list[str]:
    """The templates of `app`'s routes in the contract, as errors name them."""
    return sorted(
        {
            context.path
            for context in iter_route_contexts(app.routes)
            if isinstance(context.original_route, APIRoute)
            and context.original_route.include_in_schema
        }
    )


def app_metrics(request: Request) -> Metrics:
    """FastAPI dependency: the metrics of the app serving `request`."""
    return request.app.state.metrics
