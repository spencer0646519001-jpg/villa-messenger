"""
Replays one gold case's full history + final turn through the real Villa Messenger
pipeline (InquiryService -> ConversationStateService -> ConversationReplyComposer),
against a fresh throwaway SQLite DB, mirroring
app/api/line_webhook_routes.py::_process_pipeline_event -- minus LINE I/O, message
persistence, and webhook dedup, none of which affect state/action/reply outcomes.

Every turn (history AND final) runs the SAME decision -> record -> compose ->
mark_completed/clear_reconfirm sequence production runs per real message, because a
history turn's compose() can mark the state "completed" or clear its
accumulated_while_off flag -- both change what the NEXT turn sees. Only the final
turn's outputs are returned for scoring.

No LLM: llm_provider=None (rule-based-only mode -- see eval plan decision 1).
No live Calendar: a deterministic FakeAvailabilityService (eval.fixtures).
"""

from __future__ import annotations

import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.domain.inquiry_decision import InquiryDecision
from app.repositories.conversation_state_repository import ConversationStateRepository
from app.repositories.sqlite import init_db
from app.schemas import InboundMessage
from app.services.conversation_reply_composer import ComposedReply, ConversationReplyComposer
from app.services.conversation_state_service import ConversationStateService
from app.services.inquiry_service import InquiryService
from app.services.tenant_config_loaders import (
    make_tenant_amenities_loader,
    make_tenant_location_loader,
    make_tenant_pricing_loader,
    make_tenant_room_policy_loader,
    make_tenant_special_dates_loader,
    make_tenant_stay_policy_loader,
)

from eval import fixtures


@dataclass
class CaseResult:
    case_id: str
    final_message: InboundMessage
    final_decision: InquiryDecision
    final_state: dict | None
    final_composed: ComposedReply


def run_case(case: dict) -> CaseResult:
    history = case.get("history") or []
    turns_text = [h["content"] for h in history] + [case["input"]]
    timestamps = fixtures.turn_timestamps(case)
    modes = fixtures.turn_operation_modes(case)
    assert len(turns_text) == len(timestamps) == len(modes), (
        "turn count mismatch between history+input, timestamps, and operation modes"
    )

    platform_user_id = case.get("pseudonymous_user_id") or case["case_id"]
    availability_service = fixtures.build_availability_service(case)

    with tempfile.TemporaryDirectory(prefix="villa-eval-") as tmp_dir:
        db_path = Path(tmp_dir) / f"{uuid.uuid4()}.db"
        init_db(db_path)
        tenant_id = fixtures.seed_tenant(db_path)

        pricing_loader = make_tenant_pricing_loader(db_path)
        special_dates_loader = make_tenant_special_dates_loader(db_path)
        room_policy_loader = make_tenant_room_policy_loader(db_path)
        stay_policy_loader = make_tenant_stay_policy_loader(db_path)
        amenities_loader = make_tenant_amenities_loader(db_path)
        location_loader = make_tenant_location_loader(db_path)

        state_service = ConversationStateService(ConversationStateRepository(db_path))
        composer = ConversationReplyComposer(
            tenant_pricing_loader=pricing_loader,
            tenant_special_dates_loader=special_dates_loader,
            tenant_stay_policy_loader=stay_policy_loader,
            tenant_amenities_loader=amenities_loader,
            tenant_room_policy_loader=room_policy_loader,
            tenant_location_loader=location_loader,
            availability_service=availability_service,
        )

        final_message: InboundMessage | None = None
        final_decision: InquiryDecision | None = None
        final_state: dict | None = None
        final_composed: ComposedReply | None = None

        for text, ts, mode in zip(turns_text, timestamps, modes):
            mode_service = fixtures.build_operation_mode_service(db_path, now=ts)
            fixtures.apply_operation_mode(mode_service, tenant_id=tenant_id, mode=mode)

            message = InboundMessage(
                tenant_id=tenant_id,
                tenant_slug=fixtures.TENANT_SLUG,
                tenant_timezone=fixtures.TENANT_TIMEZONE,
                platform="line",
                platform_user_id=platform_user_id,
                customer_display_name=None,
                text=text,
                timestamp=ts,
            )
            service = InquiryService(
                operation_mode_service=mode_service,
                tenant_pricing_loader=pricing_loader,
                tenant_special_dates_loader=special_dates_loader,
                tenant_room_policy_loader=room_policy_loader,
                now_provider=lambda ts=ts: ts,
                availability_service=availability_service,
                llm_provider=None,
            )
            decision = service.handle_message(message=message)
            state = state_service.record(message=message, decision=decision)
            composed = composer.compose(message=message, decision=decision, state=state)
            if composed.completed_state_id is not None:
                state_service.mark_completed(
                    tenant_id=tenant_id, state_id=composed.completed_state_id
                )
            if composed.reconfirm_shown_state_id is not None:
                state_service.clear_accumulated_while_off(
                    tenant_id=tenant_id, state_id=composed.reconfirm_shown_state_id
                )
            final_message, final_decision, final_state, final_composed = (
                message,
                decision,
                state,
                composed,
            )

        assert final_message is not None
        assert final_decision is not None
        assert final_composed is not None
        return CaseResult(
            case_id=case["case_id"],
            final_message=final_message,
            final_decision=final_decision,
            final_state=final_state,
            final_composed=final_composed,
        )
