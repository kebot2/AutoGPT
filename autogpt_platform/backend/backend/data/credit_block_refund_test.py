"""
Direct unit-level coverage for ``UserCredit.refund_credits`` — the source of
truth for the paired spend+refund semantics introduced for the direct
block-execute API surface (see ``backend/executor/utils.py:
refund_for_failed_block_execution``).

The route-level tests in ``api/features/v1_test.py`` and
``api/external/v1/routes_test.py`` mock the credit model. These tests run
against the real ``UserCredit`` so the ``Refund: {original}`` reason wrap,
the ``model_copy`` metadata threading, and the ``cost <= 0`` short-circuit
are exercised end-to-end.
"""

# type: ignore

from typing import Any

import pytest
from prisma.enums import CreditTransactionType
from prisma.models import CreditTransaction, UserBalance

from backend.data.credit import UsageTransactionMetadata, UserCredit
from backend.data.user import DEFAULT_USER_ID


@pytest.fixture
async def setup_test_user():
    """Setup test user and cleanup after test."""
    user_id = DEFAULT_USER_ID

    await CreditTransaction.prisma().delete_many(where={"userId": user_id})
    await UserBalance.prisma().delete_many(where={"userId": user_id})

    yield user_id

    await CreditTransaction.prisma().delete_many(where={"userId": user_id})
    await UserBalance.prisma().delete_many(where={"userId": user_id})


@pytest.mark.asyncio(loop_scope="session")
async def test_refund_credits_writes_paired_refund_row(setup_test_user):
    """A USAGE charge followed by a refund_credits call must produce a
    REFUND row with +cost, the original metadata threaded through, and
    ``reason`` wrapped as ``Refund: {original}`` so credit history can
    pair the two rows by block_id / input cost_filter."""
    user_id = setup_test_user
    credit_system = UserCredit()

    metadata = UsageTransactionMetadata(
        block_id="paid-block",
        block="PaidBlock",
        input={"model": "gpt-4"},
        reason="Direct internal block execution of PaidBlock",
    )

    # Spend (the pre-flight charge mirrored by the new direct-API helper).
    spend_balance = await credit_system.spend_credits(
        user_id=user_id,
        cost=42,
        metadata=metadata,
    )
    assert spend_balance == -42

    # Refund — must add +42 back and write a REFUND row with the wrapped reason.
    refund_balance = await credit_system.refund_credits(
        user_id=user_id,
        cost=42,
        metadata=metadata,
    )
    assert refund_balance == 0

    refund_row = await CreditTransaction.prisma().find_first(
        where={"userId": user_id, "type": CreditTransactionType.REFUND},
        order={"createdAt": "desc"},
    )
    assert refund_row is not None
    assert refund_row.amount == 42

    refund_metadata: dict[str, Any] = dict(refund_row.metadata)  # type: ignore
    assert refund_metadata["block_id"] == "paid-block"
    assert refund_metadata["block"] == "PaidBlock"
    assert refund_metadata["input"] == {"model": "gpt-4"}
    assert (
        refund_metadata["reason"]
        == "Refund: Direct internal block execution of PaidBlock"
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_refund_credits_short_circuits_on_zero_cost(setup_test_user):
    """``refund_credits`` must short-circuit on ``cost <= 0`` so a free
    block (or a refund with no positive amount to undo) never writes a
    spurious REFUND row."""
    user_id = setup_test_user
    credit_system = UserCredit()

    refund_balance = await credit_system.refund_credits(
        user_id=user_id,
        cost=0,
        metadata=UsageTransactionMetadata(reason="should not be persisted"),
    )
    assert refund_balance == 0

    rows = await CreditTransaction.prisma().find_many(
        where={"userId": user_id, "type": CreditTransactionType.REFUND},
    )
    assert rows == []


@pytest.mark.asyncio(loop_scope="session")
async def test_refund_credits_falls_back_to_plain_refund_when_no_reason(
    setup_test_user,
):
    """When the original metadata carries no ``reason``, the refund row
    must record ``"Refund"`` (not ``"Refund: None"``) — keeps credit
    history audit-friendly even on metadata that pre-dates the new
    direct-API source tagging."""
    user_id = setup_test_user
    credit_system = UserCredit()

    metadata = UsageTransactionMetadata(block_id="block-id", block="Block")

    await credit_system.refund_credits(
        user_id=user_id,
        cost=5,
        metadata=metadata,
    )

    refund_row = await CreditTransaction.prisma().find_first(
        where={"userId": user_id, "type": CreditTransactionType.REFUND},
        order={"createdAt": "desc"},
    )
    assert refund_row is not None
    refund_metadata: dict[str, Any] = dict(refund_row.metadata)  # type: ignore
    assert refund_metadata["reason"] == "Refund"
