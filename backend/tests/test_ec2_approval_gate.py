from app import ec2_demo


def test_legacy_and_reviewed_create_cannot_bypass_approval(client, monkeypatch):
    monkeypatch.setattr(ec2_demo, "enabled", lambda: True)
    called = []
    monkeypatch.setattr(ec2_demo, "clients", lambda: called.append(True))
    for payload in (
        {"confirmation": "CREATE ONE EC2 DEMO INSTANCE"},
        {"confirmation": "CREATE ONE EC2 DEMO INSTANCE", "review_id": "unapproved"},
    ):
        response = client.post("/api/v1/ec2-demo/create", json=payload)
        assert response.status_code == 503
        assert "approval" in response.json()["detail"]
    assert called == []
    assert client.get("/api/v1/ec2-demo").json()["instance"] is None
