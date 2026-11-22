from enum import Enum
from typing import Literal, Optional
from pydantic import BaseModel, Field

MAX_ROUNDS = 10  # מספר מרבי של תורות עוזר מול העסק בריצה אחת (שאלות למשתמש אינן נספרות)


class Status(str, Enum):
    CONTINUE = "continue"
    NEED_USER_INFO = "need_user_info"
    DONE = "done"
    FAILED = "failed"


class Profile(BaseModel):
    name: str = Field("", max_length=100)
    address: str = Field("", max_length=200)
    phone: str = Field("", max_length=50)
    member_id: str = Field("", max_length=50)
    preferences: str = Field("", max_length=300)


class Message(BaseModel):
    # assistant = העוזר, agent = העסק, ask = שאלה למשתמש, user = תשובת המשתמש
    speaker: Literal["assistant", "agent", "ask", "user"]
    text: str = Field("", max_length=2000)


class StepRequest(BaseModel):
    kind: Literal["assistant", "agent"]
    request: str = Field(..., min_length=1, max_length=500)
    agent_id: Optional[str] = Field(None, max_length=50)
    profile: Profile = Profile()
    transcript: list[Message] = Field(default_factory=list, max_length=80)
    round: int = Field(0, ge=0, le=MAX_ROUNDS + 1)  # מספר תורות העוזר שנספרו עד כה


class Summary(BaseModel):
    outcome: str = ""
    details: list[str] = Field(default_factory=list)
    open_actions: list[str] = Field(default_factory=list)


class RequirementCheck(BaseModel):
    requirement: str = Field("", max_length=200)
    status: Literal["confirmed", "communicated", "waived", "unresolved"] = "unresolved"
    evidence: str = Field("", max_length=400)


class CompletionCheck(BaseModel):
    ready: bool = False
    unresolved: list[str] = Field(default_factory=list)
    requirements: list[RequirementCheck] = Field(default_factory=list)


class AssistantOutput(BaseModel):
    status: Status
    message: str = ""
    question: str = ""
    agent_id: str = ""
    completion_check: Optional[CompletionCheck] = None
    summary: Optional[Summary] = None


class StepResponse(BaseModel):
    speaker: str
    text: str = ""
    status: Status = Status.CONTINUE
    agent_id: str = ""
    question: str = ""
    summary: Optional[Summary] = None


class ErrorResponse(BaseModel):
    error: str  # rate_limited | gemini_error | missing_key | bad_request
    message: str
    retry_after_seconds: Optional[int] = None
