"""עטיפה יחידה לגישה ל-Gemini. המפתח נקרא רק כאן, ממשתנה הסביבה."""
import math
import os
import httpx

BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-flash"
TIMEOUT = 25.0


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
    """מחזיר טקסט. אם schema ניתן, הפלט JSON לפי הסכמה."""
    model = os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
    cfg = {"temperature": temperature}
    if schema:
        cfg.update({"responseMimeType": "application/json", "responseSchema": schema})
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": cfg,
    }
    try:
        resp = httpx.post(
            f"{BASE}/models/{model}:generateContent",
            headers={"x-goog-api-key": _key(), "Content-Type": "application/json"},
            json=body,
            timeout=TIMEOUT,
        )
    except httpx.TimeoutException:
        raise GeminiError("השירות לא הגיב בזמן. נסו שוב.")
    except httpx.HTTPError:
        raise GeminiError("אירעה שגיאת תקשורת מול שירות ה-AI.")
    try:
        data = resp.json()
    except ValueError:
        data = {}
    err = data.get("error", {}) if isinstance(data, dict) else {}
    if resp.status_code == 429 or err.get("status") == "RESOURCE_EXHAUSTED":
        raise GeminiError("מגבלת קצב", "rate_limited", _retry_delay(resp, data))
    if resp.status_code >= 400:
        detail = _clean(str(err.get("message", "")))[:200]
        raise GeminiError(f"שירות ה-AI החזיר שגיאה ({resp.status_code}). {detail}")
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        raise GeminiError("שירות ה-AI לא החזיר תשובה (ייתכן שנחסמה). נסו לנסח מחדש.")


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
