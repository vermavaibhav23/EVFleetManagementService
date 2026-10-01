from pydantic import AwareDatetime, BaseModel, Field


class JourneyStep(BaseModel):
    stage: str
    at: AwareDatetime
    plan_id: str | None = None
    note: str | None = None


class JourneyLeg(BaseModel):
    trip_id: str | None = None
    completed: bool = False
    ended_at: AwareDatetime


class JourneyProgress(BaseModel):
    run_id: str | None = None
    trip_id: str | None = None
    steps: list[JourneyStep] = Field(default_factory=list, max_length=64)
    previous_legs: list[JourneyLeg] = Field(default_factory=list, max_length=5)
    omitted_steps: int = 0
