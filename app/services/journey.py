"""Compact observed transitions carried by telemetry, independent of scenario names."""

from app.models.journey import JourneyLeg, JourneyProgress, JourneyStep

STAGES = {
    "PARKED": "parked",
    "AWAITING_DECISION": "decision",
    "EN_ROUTE_TO_CHARGER": "to_charger",
    "WAITING_FOR_CHARGER": "waiting",
    "WAITING_TO_CHARGE": "waiting",
    "CHARGING": "charging",
    "READY": "ready",
    "RESUMING_TRIP": "delivering",
    "DRIVING": "delivering",
    "AT_CUSTOMER": "at_customer",
    "STRANDED": "stranded",
    "HEALTH_HOLD": "health_hold",
    "RECOVERY_REQUIRED": "recovery",
    "OFFLINE": "offline",
}


def advance_journey(
    previous,
    *,
    run_id,
    trip_id,
    state,
    phase,
    now,
    plan_id=None,
    decision=None,
    interruption=None,
):
    same_run = previous and previous.run_id == run_id
    progress = (
        previous.model_copy(deep=True) if same_run else JourneyProgress(run_id=run_id)
    )
    if progress.trip_id != trip_id:
        if progress.steps and progress.trip_id:
            progress.previous_legs.append(
                JourneyLeg(
                    trip_id=progress.trip_id,
                    completed=any(s.stage == "at_customer" for s in progress.steps),
                    ended_at=now,
                )
            )
            progress.previous_legs = progress.previous_legs[-5:]
        progress.trip_id = trip_id
        progress.steps = []
        progress.omitted_steps = 0

    def append(stage, note=None, associated_plan=None):
        last = progress.steps[-1] if progress.steps else None
        if last and last.stage == stage == "decision":
            if note:
                last.note, last.plan_id, last.at = note, associated_plan, now
            return
        if last and (last.stage, last.note, last.plan_id) == (
            stage,
            note,
            associated_plan,
        ):
            return
        progress.steps.append(
            JourneyStep(stage=stage, at=now, note=note, plan_id=associated_plan)
        )

    if interruption:
        append("interrupted", interruption)
    if decision and not any(
        s.stage == "decision" and s.note == decision and s.plan_id == plan_id
        for s in progress.steps
    ):
        # An approved decision is evidence, but never invent earlier physical movement.
        append("decision", decision, plan_id)
    stage = (
        "handover" if phase == "WAITING_FOR_HANDOVER" else STAGES.get(state, "offline")
    )
    append(
        stage,
        associated_plan=plan_id
        if stage in {"to_charger", "waiting", "charging", "ready"}
        else None,
    )
    if len(progress.steps) > 64:
        progress.omitted_steps += len(progress.steps) - 64
        progress.steps = progress.steps[-64:]
    return progress
