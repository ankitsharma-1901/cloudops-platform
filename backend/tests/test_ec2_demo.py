from app import ec2_demo
from app.auth import get_authenticated_principal
import pytest
from types import SimpleNamespace
from botocore.exceptions import NoCredentialsError
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier


class FakeEC2:
    def __init__(self):
        self.created = []
        self.terminated = []
        self.token = None
        self.mismatch = False
        self.fail = False
        self.state = "running"

    def describe_subnets(self, **kwargs):
        return {"Subnets": [{"SubnetId": ec2_demo.SUBNET, "VpcId": "vpc-05828cfb0f6ae89e4", "OwnerId": ec2_demo.ACCOUNT, "AvailabilityZone": "ca-central-1a", "State": "available", "AssignIpv6AddressOnCreation": False}]}

    def describe_security_groups(self, **kwargs):
        return {"SecurityGroups": [{"GroupId": ec2_demo.GROUP, "VpcId": "vpc-05828cfb0f6ae89e4", "OwnerId": ec2_demo.ACCOUNT, "GroupName": "restricted", "IpPermissions": [], "IpPermissionsEgress": [{"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]}]}

    def describe_images(self, **kwargs):
        return {"Images": [{"State": "available", "Architecture": "x86_64", "RootDeviceName": "/dev/xvda"}]}

    def run_instances(self, **kwargs):
        self.created.append(kwargs)
        self.token = kwargs["ClientToken"]
        if self.fail:
            raise TimeoutError()
        return {"Instances": [{"InstanceId": "i-0123456789abcdef0"}]}

    def describe_instances(self, **kwargs):
        tags = {**ec2_demo.TAGS, "CloudOpsDemoId": self.token}
        if self.mismatch:
            tags["ManagedBy"] = "other"
        return {"Reservations": [{"OwnerId": ec2_demo.ACCOUNT, "Instances": [{"InstanceId": "i-0123456789abcdef0", "ImageId": ec2_demo.AMI, "InstanceType": "t3.micro", "VpcId": "vpc-05828cfb0f6ae89e4", "SubnetId": ec2_demo.SUBNET, "PrivateIpAddress": "10.0.1.4", "BlockDeviceMappings": [{"DeviceName": "/dev/xvda", "Ebs": {"VolumeId": "vol-123"}}], "SecurityGroups": [{"GroupId": ec2_demo.GROUP}], "Tags": [{"Key": k, "Value": v} for k, v in tags.items()], "State": {"Name": self.state}}]}]}

    def terminate_instances(self, **kwargs):
        self.terminated.append(kwargs)
        return {}


def activate(monkeypatch):
    monkeypatch.setenv("CLOUDOPS_ENABLE_EC2_DEMO", "true")
    monkeypatch.setattr(ec2_demo, "enabled", lambda: True)
    # Exercise the pre-existing lifecycle only; production remains fail-closed.
    monkeypatch.setattr(ec2_demo, "require_provisioning_approval", lambda: None)


def test_default_off_and_unauthenticated(client, monkeypatch):
    monkeypatch.delenv("CLOUDOPS_ENABLE_EC2_DEMO", raising=False)
    assert client.post("/api/v1/ec2-demo/create", json={"confirmation": "CREATE ONE EC2 DEMO INSTANCE"}).status_code == 503
    client.app.dependency_overrides.pop(get_authenticated_principal)
    assert client.get("/api/v1/ec2-demo").status_code == 401


def test_single_create_and_exact_termination(client, monkeypatch):
    fake = FakeEC2()
    activate(monkeypatch)
    monkeypatch.setattr(ec2_demo, "clients", lambda: fake)
    response = client.post("/api/v1/ec2-demo/create", json={"confirmation": "CREATE ONE EC2 DEMO INSTANCE"})
    assert response.status_code == 200
    displayed = client.get("/api/v1/ec2-demo").json()["instance"]
    assert displayed["id"] == "i-0123456789abcdef0" and displayed["aws_state"] == "running"
    assert displayed["private_ip"] == "10.0.1.4" and displayed["volumes"] == [{"device": "/dev/xvda", "id": "vol-123"}]
    request = fake.created[0]
    assert request["MaxCount"] == 1 and request["MetadataOptions"]["HttpTokens"] == "required"
    assert request["ImageId"] == "ami-009d185a5ea80b390" and request["InstanceType"] == "t3.micro"
    assert request["NetworkInterfaces"][0]["SubnetId"] == "subnet-08dfc8d266e48fc91"
    assert request["NetworkInterfaces"][0]["Groups"] == ["sg-068c70a801608db19"]
    assert request["NetworkInterfaces"][0]["AssociatePublicIpAddress"] is False
    assert request["BlockDeviceMappings"][0]["Ebs"]["Encrypted"] is True
    assert request["BlockDeviceMappings"][0]["Ebs"]["VolumeSize"] == 8
    assert request["BlockDeviceMappings"][0]["Ebs"]["VolumeType"] == "gp3"
    assert all({tag["Key"]: tag["Value"] for tag in spec["Tags"]}["Name"] == "cloudops-ec2-demo" for spec in request["TagSpecifications"])
    assert "IamInstanceProfile" not in request and "LaunchTemplate" not in request
    assert client.post("/api/v1/ec2-demo/create", json={"confirmation": "CREATE ONE EC2 DEMO INSTANCE"}).status_code == 409
    assert client.post("/api/v1/ec2-demo/terminate", json={"confirmation": "TERMINATE i-00000000000000000"}).status_code == 400
    assert client.post("/api/v1/ec2-demo/terminate", json={"confirmation": "TERMINATE i-0123456789abcdef0"}).status_code == 200
    assert fake.terminated == [{"InstanceIds": ["i-0123456789abcdef0"]}]
    fake.state = "terminated"
    displayed = client.get("/api/v1/ec2-demo").json()["instance"]
    assert displayed["status"] == "terminated" and displayed["aws_state"] == "terminated"


def test_uncertain_create_never_retries(client, monkeypatch):
    fake = FakeEC2()
    fake.fail = True
    activate(monkeypatch)
    monkeypatch.setattr(ec2_demo, "clients", lambda: fake)
    assert client.post("/api/v1/ec2-demo/create", json={"confirmation": "CREATE ONE EC2 DEMO INSTANCE"}).status_code == 503
    assert client.get("/api/v1/ec2-demo").json()["instance"]["status"] == "needs_operator_review"
    assert client.post("/api/v1/ec2-demo/create", json={"confirmation": "CREATE ONE EC2 DEMO INSTANCE"}).status_code == 409
    assert len(fake.created) == 1


def test_wrong_tags_never_terminate(client, monkeypatch):
    fake = FakeEC2()
    activate(monkeypatch)
    monkeypatch.setattr(ec2_demo, "clients", lambda: fake)
    assert client.post("/api/v1/ec2-demo/create", json={"confirmation": "CREATE ONE EC2 DEMO INSTANCE"}).status_code == 200
    fake.mismatch = True
    assert client.post("/api/v1/ec2-demo/terminate", json={"confirmation": "TERMINATE i-0123456789abcdef0"}).status_code == 503
    assert not fake.terminated


def test_wrong_aws_identity_never_creates_client(monkeypatch):
    provider = SimpleNamespace(_popen=None)
    core = SimpleNamespace(set_config_variable=lambda *args: None,
        get_component=lambda name: SimpleNamespace(get_provider=lambda name: provider))
    monkeypatch.setattr(ec2_demo.botocore.session, "get_session", lambda: core)
    class Session:
        def client(self, service, **kwargs):
            assert service == "sts"
            return SimpleNamespace(get_caller_identity=lambda: {"Account": "111111111111", "Arn": "bad"})
    monkeypatch.setattr(ec2_demo.boto3, "Session", lambda **kwargs: Session())
    with pytest.raises(ValueError):
        ec2_demo.clients()


@pytest.mark.parametrize("identity", [
    {"Account": ec2_demo.ACCOUNT, "Arn": f"arn:aws:sts::{ec2_demo.ACCOUNT}:assumed-role/cloudops-terraform-deploy/session"},
    {"Account": "111111111111", "Arn": f"arn:aws:sts::{ec2_demo.ACCOUNT}:assumed-role/{ec2_demo.ROLE}/session"},
    {"Account": ec2_demo.ACCOUNT, "Arn": f"arn:aws:sts::{ec2_demo.ACCOUNT}:assumed-role/{ec2_demo.ROLE}/session/extra"},
])
def test_demo_profile_and_role_are_pinned(monkeypatch, identity):
    provider = SimpleNamespace(_popen=None)
    core = SimpleNamespace(set_config_variable=lambda *args: None,
        get_component=lambda name: SimpleNamespace(get_provider=lambda name: provider))
    monkeypatch.setattr(ec2_demo.botocore.session, "get_session", lambda: core)
    def session(**kwargs):
        assert kwargs["profile_name"] == "cloudops-ec2-demo-deploy"
        return SimpleNamespace(client=lambda service, **kw: SimpleNamespace(get_caller_identity=lambda: identity))
    monkeypatch.setattr(ec2_demo.boto3, "Session", session)
    with pytest.raises(ValueError):
        ec2_demo.clients()


def test_expected_demo_identity_returns_ec2_client(monkeypatch):
    provider = SimpleNamespace(_popen=None)
    profiles = []
    core = SimpleNamespace(set_config_variable=lambda key, value: profiles.append((key, value)),
        get_component=lambda name: SimpleNamespace(get_provider=lambda name: provider))
    monkeypatch.setattr(ec2_demo.botocore.session, "get_session", lambda: core)
    ec2 = object()
    class Session:
        def client(self, service, **kwargs):
            if service == "sts":
                return SimpleNamespace(get_caller_identity=lambda: {"Account": ec2_demo.ACCOUNT,
                    "Arn": f"arn:aws:sts::{ec2_demo.ACCOUNT}:assumed-role/{ec2_demo.ROLE}/session"})
            assert service == "ec2"
            return ec2
    def create_session(**kwargs):
        assert kwargs["profile_name"] == "cloudops-ec2-demo-deploy"
        return Session()
    monkeypatch.setattr(ec2_demo.boto3, "Session", create_session)
    assert ec2_demo.clients() is ec2
    assert profiles == [("profile", "cloudops-ec2-demo-deploy")]


def test_missing_credentials_fail_closed(client, monkeypatch):
    activate(monkeypatch)
    monkeypatch.setattr(ec2_demo, "clients", lambda: (_ for _ in ()).throw(NoCredentialsError()))
    assert client.post("/api/v1/ec2-demo/create", json={"confirmation": "CREATE ONE EC2 DEMO INSTANCE"}).status_code == 503
    assert client.get("/api/v1/ec2-demo").json()["instance"]["status"] == "needs_operator_review"


def test_wrong_instance_id_never_terminates(client, monkeypatch):
    fake = FakeEC2()
    activate(monkeypatch)
    monkeypatch.setattr(ec2_demo, "clients", lambda: fake)
    assert client.post("/api/v1/ec2-demo/create", json={"confirmation": "CREATE ONE EC2 DEMO INSTANCE"}).status_code == 200
    original = fake.describe_instances
    def wrong_instance(**kwargs):
        response = original(**kwargs)
        response["Reservations"][0]["Instances"][0]["InstanceId"] = "i-00000000000000000"
        return response
    fake.describe_instances = wrong_instance
    assert client.post("/api/v1/ec2-demo/terminate", json={"confirmation": "TERMINATE i-0123456789abcdef0"}).status_code == 503
    assert not fake.terminated


def test_verified_completion_preserves_history_and_allows_next_create(client, monkeypatch):
    fake = FakeEC2()
    activate(monkeypatch)
    monkeypatch.setattr(ec2_demo, "clients", lambda: fake)
    create = {"confirmation": "CREATE ONE EC2 DEMO INSTANCE"}
    complete = {"confirmation": "COMPLETE i-0123456789abcdef0"}
    assert client.post("/api/v1/ec2-demo/create", json=create).status_code == 200
    assert client.post("/api/v1/ec2-demo/create", json=create).status_code == 409
    fake.state = "terminated"  # A stale 'created' database status must still be verified in AWS.
    assert client.post("/api/v1/ec2-demo/complete", json=complete).json()["status"] == "completed"
    status = client.get("/api/v1/ec2-demo").json()
    assert status["instance"] is None
    assert status["history"][0]["id"] == "i-0123456789abcdef0"
    assert status["history"][0]["created_at"] and status["history"][0]["completed_at"]
    assert client.post("/api/v1/ec2-demo/complete", json=complete).status_code == 404
    assert client.post("/api/v1/ec2-demo/create", json=create).status_code == 200
    assert client.post("/api/v1/ec2-demo/create", json=create).status_code == 409


@pytest.mark.parametrize("failure", ["running", "unknown", "credentials", "timeout", "wrong_tags"])
def test_completion_uncertainty_never_releases_reservation(client, monkeypatch, failure):
    fake = FakeEC2()
    activate(monkeypatch)
    monkeypatch.setattr(ec2_demo, "clients", lambda: fake)
    create = {"confirmation": "CREATE ONE EC2 DEMO INSTANCE"}
    assert client.post("/api/v1/ec2-demo/create", json=create).status_code == 200
    fake.state = "terminated" if failure in {"credentials", "timeout", "wrong_tags"} else failure
    if failure == "credentials":
        monkeypatch.setattr(ec2_demo, "clients", lambda: (_ for _ in ()).throw(NoCredentialsError()))
    if failure == "timeout":
        fake.describe_instances = lambda **kwargs: (_ for _ in ()).throw(TimeoutError())
    if failure == "wrong_tags":
        fake.mismatch = True
    assert client.post("/api/v1/ec2-demo/complete", json={"confirmation": "COMPLETE i-0123456789abcdef0"}).status_code == 503
    assert client.post("/api/v1/ec2-demo/create", json=create).status_code == 409
    status = client.get("/api/v1/ec2-demo").json()
    assert status["instance"]["status"] == "created" and status["history"] == []


def test_concurrent_completion_only_archives_once(client, monkeypatch):
    fake = FakeEC2()
    activate(monkeypatch)
    monkeypatch.setattr(ec2_demo, "clients", lambda: fake)
    assert client.post("/api/v1/ec2-demo/create", json={"confirmation": "CREATE ONE EC2 DEMO INSTANCE"}).status_code == 200
    fake.state = "terminated"
    barrier = Barrier(2, timeout=10)
    describe = fake.describe_instances
    def synchronized(**kwargs):
        barrier.wait()
        return describe(**kwargs)
    fake.describe_instances = synchronized
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post("/api/v1/ec2-demo/complete", json={"confirmation": "COMPLETE i-0123456789abcdef0"}).status_code, range(2)))
    assert sorted(results) == [200, 409]
    assert len(client.get("/api/v1/ec2-demo").json()["history"]) == 1


def test_concurrent_create_reserves_only_one_slot(client, monkeypatch):
    fake = FakeEC2()
    activate(monkeypatch)
    monkeypatch.setattr(ec2_demo, "clients", lambda: fake)
    barrier = Barrier(2, timeout=10)
    def create(_):
        barrier.wait()
        return client.post("/api/v1/ec2-demo/create", json={"confirmation": "CREATE ONE EC2 DEMO INSTANCE"}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, range(2)))
    assert sorted(results) == [200, 409]
    assert len(fake.created) == 1


def test_stale_terminated_status_does_not_bypass_aws(client, monkeypatch):
    from sqlalchemy import text
    fake = FakeEC2()
    activate(monkeypatch)
    monkeypatch.setattr(ec2_demo, "clients", lambda: fake)
    create = {"confirmation": "CREATE ONE EC2 DEMO INSTANCE"}
    assert client.post("/api/v1/ec2-demo/create", json=create).status_code == 200
    with client.app.state.engine.begin() as connection:
        connection.execute(text("UPDATE ec2_demo SET status='terminated'"))
    assert client.post("/api/v1/ec2-demo/complete", json={"confirmation": "COMPLETE i-0123456789abcdef0"}).status_code == 503
    assert client.post("/api/v1/ec2-demo/create", json=create).status_code == 409


def test_completion_requires_owner_and_exact_id(client, monkeypatch):
    from app.auth import AuthenticatedPrincipal
    fake = FakeEC2()
    activate(monkeypatch)
    monkeypatch.setattr(ec2_demo, "clients", lambda: fake)
    assert client.post("/api/v1/ec2-demo/create", json={"confirmation": "CREATE ONE EC2 DEMO INSTANCE"}).status_code == 200
    fake.state = "terminated"
    assert client.post("/api/v1/ec2-demo/complete", json={"confirmation": "COMPLETE i-00000000000000000"}).status_code == 400
    client.app.dependency_overrides[get_authenticated_principal] = lambda: AuthenticatedPrincipal(
        "https://issuer.example|another-engineer", frozenset({"engineer"}))
    assert client.post("/api/v1/ec2-demo/complete", json={"confirmation": "COMPLETE i-0123456789abcdef0"}).status_code == 404


def test_termination_must_be_acknowledged_before_reconciliation(client, monkeypatch):
    fake = FakeEC2()
    activate(monkeypatch)
    monkeypatch.setattr(ec2_demo, "clients", lambda: fake)
    assert client.post("/api/v1/ec2-demo/create", json={"confirmation": "CREATE ONE EC2 DEMO INSTANCE"}).status_code == 200
    from sqlalchemy import text
    with client.app.state.engine.begin() as connection:
        connection.execute(text("UPDATE ec2_demo SET status='terminating'"))
    fake.state = "terminated"
    assert client.get("/api/v1/ec2-demo").json()["instance"]["status"] == "terminating"
    assert client.post("/api/v1/ec2-demo/complete", json={"confirmation": "COMPLETE i-0123456789abcdef0"}).status_code == 409


@pytest.mark.parametrize("network", ["absent", "wrong"])
def test_terminated_network_attributes_may_disappear_but_not_mismatch(client, monkeypatch, network):
    fake = FakeEC2()
    activate(monkeypatch)
    monkeypatch.setattr(ec2_demo, "clients", lambda: fake)
    assert client.post("/api/v1/ec2-demo/create", json={"confirmation": "CREATE ONE EC2 DEMO INSTANCE"}).status_code == 200
    fake.state = "terminated"
    original = fake.describe_instances
    def describe(**kwargs):
        result = original(**kwargs)
        instance = result["Reservations"][0]["Instances"][0]
        if network == "absent":
            instance.pop("SubnetId")
            instance.pop("VpcId")
            instance["SecurityGroups"] = []
        else:
            instance["SubnetId"] = "subnet-incorrect"
        return result
    fake.describe_instances = describe
    response = client.post("/api/v1/ec2-demo/complete", json={"confirmation": "COMPLETE i-0123456789abcdef0"})
    assert response.status_code == (200 if network == "absent" else 503)


def test_legacy_returned_termination_reconciles_after_restart(client, monkeypatch):
    from sqlalchemy import text
    fake = FakeEC2()
    activate(monkeypatch)
    monkeypatch.setattr(ec2_demo, "clients", lambda: fake)
    assert client.post("/api/v1/ec2-demo/create", json={"confirmation": "CREATE ONE EC2 DEMO INSTANCE"}).status_code == 200
    assert client.post("/api/v1/ec2-demo/terminate", json={"confirmation": "TERMINATE i-0123456789abcdef0"}).status_code == 200
    with client.app.state.engine.begin() as connection:
        connection.execute(text("UPDATE ec2_demo SET status='terminating'"))
    fake.state = "terminated"
    assert client.get("/api/v1/ec2-demo").json()["instance"]["status"] == "terminated"
    assert client.post("/api/v1/ec2-demo/complete", json={"confirmation": "COMPLETE i-0123456789abcdef0"}).status_code == 200
    with client.app.state.engine.connect() as connection:
        events = list(connection.execute(text("SELECT event_type FROM ec2_demo_audit ORDER BY occurred_at")).scalars())
    assert events == ["create_started", "create_returned", "termination_started", "termination_returned", "termination_confirmed", "lifecycle_completed"]


def test_history_and_active_reservation_survive_application_restart(client, database_url, monkeypatch):
    from app.main import create_app
    from app.auth import AuthenticatedPrincipal
    from fastapi.testclient import TestClient
    fake = FakeEC2()
    activate(monkeypatch)
    monkeypatch.setattr(ec2_demo, "clients", lambda: fake)
    create = {"confirmation": "CREATE ONE EC2 DEMO INSTANCE"}
    assert client.post("/api/v1/ec2-demo/create", json=create).status_code == 200
    fake.state = "terminated"
    assert client.post("/api/v1/ec2-demo/complete", json={"confirmation": "COMPLETE i-0123456789abcdef0"}).status_code == 200
    assert client.post("/api/v1/ec2-demo/create", json=create).status_code == 200
    app = create_app(database_url)
    app.dependency_overrides[get_authenticated_principal] = lambda: AuthenticatedPrincipal(
        "https://issuer.example|engineer-user", frozenset({"engineer"}))
    with TestClient(app) as restarted:
        state = restarted.get("/api/v1/ec2-demo").json()
        assert state["instance"]["id"] == "i-0123456789abcdef0"
        assert len(state["history"]) == 1
        assert restarted.post("/api/v1/ec2-demo/create", json=create).status_code == 409
