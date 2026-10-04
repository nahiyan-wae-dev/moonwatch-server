"""Firebase Cloud Messaging HTTP v1 transport for Moonwatch events."""
import json
import os
import urllib.parse
import urllib.request

TOPIC = "moonvale-alerts"
MAX_BODY = 1500


def send(event):
    credential = os.environ.get("FCM_SERVICE_ACCOUNT", "").strip()
    if not credential:
        return False
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
        info = json.loads(credential)
        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/firebase.messaging"],
        )
        credentials.refresh(Request())
        project_id = info.get("project_id") or credentials.project_id
        token = credentials.token
        if not project_id or not token:
            return False

        data = {key: str(event[key]) for key in (
            "event_id", "source_id", "title", "body", "priority", "url", "ts"
        )}
        data["body"] = data["body"][:MAX_BODY]
        payload = {
            "message": {
                "topic": TOPIC,
                "data": data,
                "android": {"priority": "HIGH"},
            }
        }
        endpoint = f"https://fcm.googleapis.com/v1/projects/{urllib.parse.quote(project_id, safe='')}/messages:send"
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15):
            return True
    except Exception as exc:
        print(f"FCM failed: {type(exc).__name__}: {exc}")
        return False
