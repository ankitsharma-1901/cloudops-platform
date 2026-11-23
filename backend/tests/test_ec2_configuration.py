from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from app import ec2_demo
from app.auth import AuthenticatedPrincipal, get_authenticated_principal
from app.ec2_configuration import catalog
from test_ec2_demo import FakeEC2, activate


def review(client, name="web-demo"):
    return client.post("/api/v1/ec2-demo/review", json={**catalog()["defaults"], "instance_name": name})


@pytest.mark.parametrize("field,value", [
    ("region", "us-east-1"), ("ami", "ami-00000000000000000"), ("instance_type", "t3.large"),
    ("operating_system", "ubuntu"), ("storage_gib", 20), ("storage_gib", True),
    ("storage_type", "gp2"), ("encrypted", False), ("subnet", "subnet-other"),
    ("security_group", "sg-other"), ("vpc", "vpc-other"), ("instance_name", "INVALID NAME"),
    ("instance_name", "a"), ("instance_name", "a" * 33),
])
def test_review_rejects_unsupported_values(client, field, value):
    config = {**catalog()["defaults"], field: value}
    assert client.post("/api/v1/ec2-demo/review", json=config).status_code == 422


def test_review_create_binds_name_and_cannot_reuse(client, monkeypatch):
    fake = FakeEC2()
    activate(monkeypatch)
    monkeypatch.setattr(ec2_demo, "clients", lambda: fake)
    response = review(client)
    assert response.status_code == 200
    review_id = response.json()["review_id"]
    assert not fake.created
    payload = {"confirmation": "CREATE ONE EC2 DEMO INSTANCE", "review_id": review_id}
    assert client.post("/api/v1/ec2-demo/create", json={**payload, "instance_name": "changed"}).status_code == 422
    assert client.post("/api/v1/ec2-demo/create", json=payload).status_code == 200
    assert {tag["Key"]: tag["Value"] for tag in fake.created[0]["TagSpecifications"][0]["Tags"]}["Name"] == "web-demo"
    assert client.get("/api/v1/ec2-demo").json()["instance"]["name"] == "web-demo"
    assert client.post("/api/v1/ec2-demo/create", json=payload).status_code == 409


def test_review_is_bound_to_authenticated_engineer(client, monkeypatch):
    activate(monkeypatch)
    review_id = review(client).json()["review_id"]
    client.app.dependency_overrides[get_authenticated_principal] = lambda: AuthenticatedPrincipal("other", frozenset({"engineer"}))
    assert client.post("/api/v1/ec2-demo/create", json={"confirmation": "CREATE ONE EC2 DEMO INSTANCE", "review_id": review_id}).status_code == 404
    client.app.dependency_overrides[get_authenticated_principal] = lambda: AuthenticatedPrincipal("other", frozenset())
    assert client.get("/api/v1/ec2-demo/catalog").status_code == 403
    assert review(client).status_code == 403


def test_concurrent_reviewed_create_reserves_once(client, monkeypatch):
    fake = FakeEC2()
    activate(monkeypatch)
    monkeypatch.setattr(ec2_demo, "clients", lambda: fake)
    review_id = review(client).json()["review_id"]
    barrier = Barrier(2, timeout=10)
    def create(_):
        barrier.wait()
        return client.post("/api/v1/ec2-demo/create", json={"confirmation": "CREATE ONE EC2 DEMO INSTANCE", "review_id": review_id}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create, range(2))) == [200, 409]
    assert len(fake.created) == 1


def test_expired_review_cannot_create(client, monkeypatch):
    from sqlalchemy import text
    fake = FakeEC2()
    activate(monkeypatch)
    monkeypatch.setattr(ec2_demo, "clients", lambda: fake)
    review_id = review(client).json()["review_id"]
    with client.app.state.engine.begin() as connection:
        connection.execute(text("UPDATE ec2_demo_reviews SET expires_at='2000-01-01 00:00:00' WHERE id=:id"), {"id": review_id})
    assert client.post("/api/v1/ec2-demo/create", json={"confirmation": "CREATE ONE EC2 DEMO INSTANCE", "review_id": review_id}).status_code == 409
    assert not fake.created


def test_custom_name_survives_completion_and_historical_record(client, monkeypatch):
    fake = FakeEC2()
    activate(monkeypatch)
    monkeypatch.setattr(ec2_demo, "clients", lambda: fake)
    review_id = review(client).json()["review_id"]
    assert client.post("/api/v1/ec2-demo/create", json={"confirmation": "CREATE ONE EC2 DEMO INSTANCE", "review_id": review_id}).status_code == 200
    fake.state = "terminated"
    assert client.post("/api/v1/ec2-demo/complete", json={"confirmation": "COMPLETE i-0123456789abcdef0"}).status_code == 200
    assert client.get("/api/v1/ec2-demo").json()["history"][0]["name"] == "web-demo"


def test_catalog_and_review_do_not_use_aws(client, monkeypatch):
    def fail_if_called():
        raise AssertionError("AWS client must not be created during review")
    monkeypatch.setattr(ec2_demo, "clients", fail_if_called)
    assert client.get("/api/v1/ec2-demo/catalog").status_code == 200
    assert review(client).status_code == 200


def test_additive_migration_preserves_existing_demo_records(tmp_path):
    import sqlite3
    from app.database import create_database_engine, initialize_database
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE ec2_demo (id INTEGER PRIMARY KEY, owner_subject TEXT NOT NULL, operation_token TEXT NOT NULL, status TEXT NOT NULL, instance_id TEXT, message TEXT, created_at DATETIME NOT NULL)")
    connection.execute("INSERT INTO ec2_demo VALUES (1, 'owner', 'token', 'created', 'i-12345678', NULL, '2026-11-22 00:00:00')")
    connection.commit(); connection.close()
    engine = create_database_engine("sqlite:///" + str(path))
    initialize_database(engine)
    with engine.connect() as connection:
        from sqlalchemy import text
        result = connection.execute(text("SELECT instance_id, status, instance_name FROM ec2_demo WHERE id=1")).one()
        assert tuple(result) == ("i-12345678", "created", None)
    engine.dispose()
