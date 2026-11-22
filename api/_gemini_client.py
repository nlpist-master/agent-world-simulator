"""עטיפה יחידה לגישה ל-Gemini. המפתח נקרא רק כאן, ממשתנה הסביבה."""
import math
import os
import time
import httpx

BASE = "https://generativelanguage.googleapis.com/v1beta"
# סדר הניסיון כשמודל לא זמין (עומס, הוצא משימוש, מגבלת קצב או timeout). נבדק מול Free Tier ב-11/2026.
FALLBACK_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3-flash-preview",
]
TIMEOUT = 20.0      # לכל ניסיון
DEADLINE = 50.0     # לכל הקריאה, מתחת ל-maxDuration של Vercel
RETRYABLE = {404, 408, 500, 502, 503, 504}
_gone = set()       # מודלים שהחזירו 404 נדלגים עד שהמופע של השרת מתחלף


def _models():
    """GEMINI_MODEL (מודל אחד או רשימה מופרדת בפסיקים) קודם, ואחריו רשימת הגיבוי, בלי כפילויות."""
    env = [m.strip() for m in os.environ.get("GEMINI_MODEL", "").split(",") if m.strip()]
    out = []
    for m in env + FALLBACK_MODELS:
        if m not in out and m not in _gone:
            out.append(m)
    return out or FALLBACK_MODELS[:]


class GeminiError(Exception):
    def __init__(self, message, kind="gemini_error", retry_after=None):
        super().__init__(message)
        self.message, self.kind, self.retry_after = message, kind, retry_after


def _key():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise GeminiError("השרת לא הוגדר עם מפתח Gemini (GEMINI_API_KEY).", "missing_key")
    return key


def _clean(text):
    key = os.environ.get("GEMINI_API_KEY", "")
    return text.replace(key, "***") if key else text


def _retry_delay(resp, data):
    secs = None
    try:
        secs = float(resp.headers.get("retry-after"))
    except (TypeError, ValueError):
        pass
    try:
        for d in data.get("error", {}).get("details", []):
            if str(d.get("@type", "")).endswith("RetryInfo"):
                secs = float(str(d.get("retryDelay", "")).rstrip("s"))
    except (TypeError, ValueError, AttributeError):
        pass
    if secs is None:
        return 30
    return int(min(max(math.ceil(secs), 5), 120))


def generate(system, contents, schema=None, temperature=0.8):
    """מחזיר טקסט. אם schema ניתן, הפלט JSON לפי הסכמה.
    אם מודל לא זמין, עובר למודל הבא ברשימה. נכשל רק אם כולם נכשלו או שנגמר הזמן."""
    cfg = {"temperature": temperature}
    if schema:
        cfg.update({"responseMimeType": "application/json", "responseSchema": schema})
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": cfg,
    }
    key = _key()
    start = time.monotonic()
    last = GeminiError("שירות ה-AI אינו זמין כרגע. נסו שוב.")
    rate_delays = []
    for model in _models():
        remaining = DEADLINE - (time.monotonic() - start)
        if remaining < 3:
            break
        try:
            resp = httpx.post(
                f"{BASE}/models/{model}:generateContent",
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json=body,
                timeout=min(TIMEOUT, remaining),
            )
        except httpx.TimeoutException:
            last = GeminiError("השירות לא הגיב בזמן. נסו שוב.")
            continue
        except httpx.HTTPError:
            last = GeminiError("אירעה שגיאת תקשורת מול שירות ה-AI.")
            continue
        try:
            data = resp.json()
        except ValueError:
            data = {}
        err = data.get("error", {}) if isinstance(data, dict) else {}
        if resp.status_code == 429 or err.get("status") == "RESOURCE_EXHAUSTED":
            rate_delays.append(_retry_delay(resp, data))   # המכסה ב-Free Tier נפרדת לכל מודל
            continue
        if resp.status_code in RETRYABLE:
            if resp.status_code == 404:
                _gone.add(model)
            detail = _clean(str(err.get("message", "")))[:200]
            last = GeminiError(f"שירות ה-AI החזיר שגיאה ({resp.status_code}). {detail}")
            continue
        if resp.status_code >= 400:
            detail = _clean(str(err.get("message", "")))[:200]
            raise GeminiError(f"שירות ה-AI החזיר שגיאה ({resp.status_code}). {detail}")
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError, TypeError):
            raise GeminiError("שירות ה-AI לא החזיר תשובה (ייתכן שנחסמה). נסו לנסח מחדש.")
    if rate_delays:   # לפחות מודל אחד רק חסום זמנית: הלקוח יחכה וינסה שוב
        raise GeminiError("מגבלת קצב", "rate_limited", min(rate_delays))
    raise last


def list_models():
    resp = httpx.get(f"{BASE}/models?pageSize=200", headers={"x-goog-api-key": _key()}, timeout=TIMEOUT)
    resp.raise_for_status()
    return [
        m["name"].split("/")[-1]
        for m in resp.json().get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]


if __name__ == "__main__":
    # python api/_gemini_client.py  -> מציג מודלים זמינים למפתח שלך
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    try:
        for name in list_models():
            print(name)
    except Exception as e:
        print("Failed:", _clean(str(e)))
