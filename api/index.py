import json
import os
import sys
from pathlib import Path

# ייבוא יחיד לכל הסביבות: uvicorn api.index:app וגם Vercel (קבצי _ אינם הופכים לפונקציות)
sys.path.insert(0, os.path.dirname(__file__))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

import _gemini_client as gemini
import _prompts as prompts
from _agents_config import BY_ID, public_list
from _schemas import AssistantOutput, MAX_ROUNDS, Status, StepRequest, StepResponse, Summary

app = FastAPI(title="Agent World Simulator")
MAX_TEXT = 1800  # חיתוך פלט ארוך כדי שלא יחרוג מהוולידציה בצעד הבא
MAX_SUMMARY_OUTCOME = 220
MAX_SUMMARY_ITEM = 160
MAX_SUMMARY_ITEMS = 5


def _compact_text(value, limit):
    """Normalize generated summary text and keep it short enough for the result card."""
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    # Prefer a complete sentence when the model has started rambling.
    boundary = max(text.rfind(mark, 0, limit) for mark in (".", "!", "?", "。"))
    if boundary >= limit // 2:
        return text[:boundary + 1].strip()
    return text[:limit - 1].rstrip() + "…"


def _normalize_summary(summary, status):
    summary = summary or Summary()
    outcome = _summary_outcome(summary.outcome, MAX_SUMMARY_OUTCOME)
    if not outcome:
        outcome = "הבקשה הושלמה בסימולציה." if status == Status.DONE else "לא הצלחתי להשלים את הבקשה."

    def clean_items(values):
        return [
            item for item in (_compact_text(value, MAX_SUMMARY_ITEM) for value in values)
            if item
        ][:MAX_SUMMARY_ITEMS]

    return Summary(
        outcome=outcome,
        details=clean_items(summary.details),
        open_actions=clean_items(summary.open_actions),
    )


def _summary_outcome(value, limit):
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    # Keep the first complete sentence when the model ignores the short-summary instruction.
    for index, char in enumerate(text[:limit]):
        if char in ".!?״" and (index + 1 == len(text) or text[index + 1].isspace()):
            return text[:index + 1].strip()
    if len(text) <= limit:
        return text
    clipped = text[:limit - 1].rsplit(" ", 1)[0].rstrip()
    return clipped + "…"


def _completion_message(unresolved):
    items = [" ".join(str(item).split())[:140] for item in unresolved if str(item).strip()]
    if not items:
        items = ["כמה פרטים מפורשים בבקשה עדיין לא אושרו"]
    return "לפני שאוכל לסיים, אשמח לוודא מולכם: " + "; ".join(items[:3]) + ". תוכלו לאשר זאת?"


def _polite_failure_message(message):
    text = _compact_text(message, 500)
    has_thanks = any(marker in text for marker in ("תודה", "מעריך", "מעריכה"))
    has_goodbye = any(marker in text for marker in ("יום טוב", "יום נעים", "להתראות", "נעצור כאן", "לא נתקדם כרגע"))
    if text and has_thanks and has_goodbye:
        return text
    if text:
        return "תודה על הבירור ועל הזמן. " + text + " נעצור כאן. יום טוב."
    return "תודה על הבירור ועל הזמן. לצערי לא נוכל להתקדם עם הבקשה כרגע, אז נעצור כאן. יום טוב."


def _completion_gaps(check, transcript, request):
    if not check:
        return ["לא התקבל אישור מפורש לכל פרטי הבקשה"]
    business_text = [m.text for m in transcript if m.speaker == "agent"]
    assistant_text = [m.text for m in transcript if m.speaker == "assistant"]
    user_text = [request, *(m.text for m in transcript if m.speaker == "user")]
    gaps = list(check.unresolved)
    if not check.requirements:
        gaps.append("רשימת דרישות מלאה עם אישורים מהשיחה")
    for item in check.requirements:
        quote = item.evidence.strip()
        if item.status == "unresolved":
            gaps.append(item.requirement or "דרישה שטרם אושרה")
        elif item.status == "confirmed" and (not quote or not any(quote in text for text in business_text)):
            gaps.append(item.requirement or "אישור עסקי ללא ציטוט תומך")
        elif item.status == "communicated" and (not quote or not any(quote in text for text in assistant_text)):
            gaps.append(item.requirement or "מידע שלא הועבר לעסק")
        elif item.status == "waived" and (not quote or not any(quote in text for text in user_text)):
            gaps.append(item.requirement or "ויתור משתמש ללא ציטוט תומך")
    if not check.ready:
        gaps.append("דרישות שטרם אושרו במפורש")
    return list(dict.fromkeys(gap for gap in gaps if gap.strip()))


def _err(status, error, message, retry=None):
    body = {"error": error, "message": message}
    if retry is not None:
        body["retry_after_seconds"] = retry
    return JSONResponse(status_code=status, content=body)


@app.exception_handler(RequestValidationError)
async def _validation(request, exc):
    return _err(422, "bad_request", "הנתונים חורגים מהמגבלות (אורך או מספר הודעות). התחילו בקשה חדשה.")


@app.get("/api/agents")
def agents():
    return public_list()


@app.post("/api/step")
def step(req: StepRequest):
    if req.round > MAX_ROUNDS or (req.kind == "assistant" and req.round >= MAX_ROUNDS):
        return _err(400, "bad_request", "חרגתם ממספר הסבבים המרבי.")
    try:
        if req.kind == "agent":
            agent = BY_ID.get(req.agent_id or "")
            if not agent:
                return _err(400, "bad_request", "סוכן לא מוכר.")
            contents = prompts.agent_contents(req.transcript)
            if not contents or contents[0]["role"] != "user" or contents[-1]["role"] != "user":
                return _err(400, "bad_request", "תמלול השיחה אינו תקין. התחילו בקשה חדשה.")
            text = gemini.generate(prompts.agent_system(agent), contents, temperature=0.9)
            return StepResponse(speaker="agent", text=text[:MAX_TEXT])

        raw = gemini.generate(
            prompts.assistant_system(), prompts.assistant_contents(req),
            schema=prompts.ASSISTANT_SCHEMA, temperature=0.4,
        )
        try:
            out = AssistantOutput(**json.loads(raw))
        except (ValueError, ValidationError, TypeError):
            return _err(502, "gemini_error", "העוזר החזיר תשובה לא תקינה. נסו שוב.")
        out.message = out.message[:MAX_TEXT]
        out.question = out.question[:300]
        agent_id = req.agent_id or (out.agent_id if out.agent_id in BY_ID else "")
        if out.status == Status.DONE:
            check = out.completion_check
            unresolved = _completion_gaps(check, req.transcript, req.request)
            business_has_replied = bool(req.transcript and req.transcript[-1].speaker == "agent")
            if not business_has_replied or unresolved:
                if not unresolved:
                    unresolved = ["אישור מפורש מהעסק לכל תנאי הבקשה"]
                out.status = Status.CONTINUE
                out.message = _completion_message(unresolved)
                out.summary = None
        if out.status == Status.CONTINUE and (agent_id not in BY_ID or not out.message.strip()):
            out.status = Status.FAILED
            out.summary = Summary(outcome="לא נמצא עסק מתאים לבקשה, או שהעוזר לא הצליח לנסח הודעה. נסו לנסח את הבקשה מחדש.")
        if out.status == Status.FAILED:
            out.message = _polite_failure_message(out.message)
        if out.status == Status.NEED_USER_INFO and not out.question.strip():
            out.question = "העסק מבקש פרט נוסף. מה להשיב?"
        if out.status in (Status.DONE, Status.FAILED):
            out.summary = _normalize_summary(out.summary, out.status)
        return StepResponse(
            speaker="assistant", text=out.message, status=out.status,
            agent_id=agent_id, question=out.question, summary=out.summary,
        )
    except gemini.GeminiError as e:
        if e.kind == "rate_limited":
            return _err(429, "rate_limited", "הגענו למגבלת הבקשות של השירות החינמי.", e.retry_after or 30)
        return _err(500 if e.kind == "missing_key" else 502, e.kind, e.message)


PUBLIC = Path(__file__).resolve().parent.parent / "public"
if PUBLIC.is_dir():  # בפיתוח מקומי; ב-Vercel הקבצים הסטטיים מוגשים ישירות מתיקיית public
    app.mount("/", StaticFiles(directory=str(PUBLIC), html=True), name="static")
