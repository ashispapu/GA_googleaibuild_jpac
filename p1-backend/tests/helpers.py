"""Test helpers shared by unit and integration tests."""


def login(client, user):
    """Log in as a demo user (password `demo`) and return the Authorization header."""
    r = client.post("/auth/token", json={"username": user, "password": "demo"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def run_story(client, users, speed=300):
    """calm -> warning -> open incident -> let the agents finish. Returns the incident id."""
    client.post("/simulator/scenario/reset", headers=users["admin"])
    client.post("/simulator/scenario/warning", headers=users["admin"])
    inc = client.post("/incidents", json={"asset_id": "EQ-3012", "trigger": "ml_alert"}, headers=users["op"]).json()
    token = users["op"]["Authorization"][7:]
    with client.websocket_connect(f"/incidents/{inc['id']}/stream?token={token}&speed={speed}") as ws:
        try:
            while True:
                ws.receive_text()
        except Exception:
            pass
    return inc["id"]
