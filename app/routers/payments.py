"""Record / edit / delete real money transfers between two members."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Response, status

from app import errors, views
from app.db import Database, now, random_id
from app.deps import (
    DbDep,
    GroupIdDep,
    IfMatchDep,
    UserDep,
    check_version,
    require_group_membership,
)
from app.models import Payment as PaymentRecord
from app.schemas import Error, Payment, PaymentInput

router = APIRouter(tags=["Payments"])

PaymentIdDep = Annotated[str, Path(alias="paymentId")]


def _validate(
    db: Database, group_id: str, payer_member_id: str, body: PaymentInput
) -> None:
    member_ids = {m.id for m in db.members_of(group_id)}
    if body.recipientMemberId not in member_ids:
        raise errors.validation("Pick a recipient.")
    if body.recipientMemberId == payer_member_id:
        raise errors.validation("Payer and recipient must differ.")
    if not body.amountMinor > 0:
        raise errors.validation("Amount must be greater than zero.")


def _require_payment(
    db: Database, group_id: str, payment_id: str
) -> PaymentRecord:
    payment = db.payment(payment_id)
    if payment is None or payment.group_id != group_id:
        raise errors.not_found("Payment not found.")
    return payment


@router.post(
    "/groups/{groupId}/payments",
    response_model=Payment,
    status_code=status.HTTP_201_CREATED,
    summary="Record a payment",
    responses={400: {"model": Error}, 403: {"model": Error}, 404: {"model": Error}},
)
def create_payment(
    groupId: GroupIdDep,
    body: PaymentInput,
    db: DbDep,
    user: UserDep,
    response: Response,
) -> Payment:
    with db.lock:
        # The payer is always the caller's own membership; it is never taken
        # from the request body.
        _, me = require_group_membership(db, user, groupId)
        _validate(db, groupId, me.id, body)
        stamp = now()
        payment = db.add_payment(
            PaymentRecord(
                id=random_id("pay"),
                group_id=groupId,
                payer_member_id=me.id,
                recipient_member_id=body.recipientMemberId,
                amount_minor=body.amountMinor,
                date=body.date,
                note=(body.note or "").strip(),
                created_by_user_id=user.id,
                created_at=stamp,
                updated_at=stamp,
                version=1,
            )
        )

    response.headers["ETag"] = f'"{payment.version}"'
    return views.payment(payment, viewer_user_id=user.id)


@router.put(
    "/groups/{groupId}/payments/{paymentId}",
    response_model=Payment,
    summary="Replace a payment",
    responses={
        400: {"model": Error},
        403: {"model": Error},
        404: {"model": Error},
        409: {"model": Error},
        412: {"model": Error},
    },
)
def update_payment(
    groupId: GroupIdDep,
    paymentId: PaymentIdDep,
    body: PaymentInput,
    expected_version: IfMatchDep,
    db: DbDep,
    user: UserDep,
    response: Response,
) -> Payment:
    with db.lock:
        require_group_membership(db, user, groupId)
        payment = _require_payment(db, groupId, paymentId)
        if payment.created_by_user_id != user.id:
            raise errors.forbidden(
                "Only the person who recorded a payment can change it."
            )
        check_version(
            payment.version,
            expected_version,
            views.conflict_payload(
                views.payment(payment, viewer_user_id=user.id)
            ),
        )
        # The payer cannot be changed.
        _validate(db, groupId, payment.payer_member_id, body)
        payment.recipient_member_id = body.recipientMemberId
        payment.amount_minor = body.amountMinor
        payment.date = body.date
        payment.note = (body.note or "").strip()
        payment.updated_at = now()
        payment.version += 1

    response.headers["ETag"] = f'"{payment.version}"'
    return views.payment(payment, viewer_user_id=user.id)


@router.delete(
    "/groups/{groupId}/payments/{paymentId}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a payment",
    responses={
        403: {"model": Error},
        404: {"model": Error},
        409: {"model": Error},
        412: {"model": Error},
    },
)
def delete_payment(
    groupId: GroupIdDep,
    paymentId: PaymentIdDep,
    expected_version: IfMatchDep,
    db: DbDep,
    user: UserDep,
) -> Response:
    with db.lock:
        require_group_membership(db, user, groupId)
        payment = _require_payment(db, groupId, paymentId)
        if payment.created_by_user_id != user.id:
            raise errors.forbidden(
                "Only the person who recorded a payment can delete it."
            )
        check_version(
            payment.version,
            expected_version,
            views.conflict_payload(
                views.payment(payment, viewer_user_id=user.id)
            ),
        )
        db.delete_payment(payment.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
