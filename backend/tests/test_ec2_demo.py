from app import ec2_demo
from app.auth import get_authenticated_principal


class FakeEC2:
    def __init__(self):
        self.created = []
        self.terminated = []
        self.token = None
        self.mismatch = False
        self.fail = False

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
        return {"Reservations": [{"OwnerId": ec2_demo.ACCOUNT, "Instances": [{"InstanceId": "i-0123456789abcdef0", "ImageId": ec2_demo.AMI, "InstanceType": "t3.micro", "VpcId": "vpc-05828cfb0f6ae89e4", "SubnetId": ec2_demo.SUBNET, "SecurityGroups": [{"GroupId": ec2_demo.GROUP}], "Tags": [{"Key": k, "Value": v} for k, v in tags.items()], "State": {"Name": "running"}}]}]}

    def terminate_instances(self, **kwargs):
        self.terminated.append(kwargs)
        return {}


def activate(monkeypatch):
    monkeypatch.setenv("CLOUDOPS_ENABLE_EC2_DEMO", "true")
    monkeypatch.setattr(ec2_demo, "enabled", lambda: True)


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
    request = fake.created[0]
    assert request["MaxCount"] == 1 and request["MetadataOptions"]["HttpTokens"] == "required"
    assert request["NetworkInterfaces"][0]["AssociatePublicIpAddress"] is False
    assert request["BlockDeviceMappings"][0]["Ebs"]["Encrypted"] is True
    assert "IamInstanceProfile" not in request and "LaunchTemplate" not in request
    assert client.post("/api/v1/ec2-demo/create", json={"confirmation": "CREATE ONE EC2 DEMO INSTANCE"}).status_code == 409
    assert client.post("/api/v1/ec2-demo/terminate", json={"confirmation": "TERMINATE i-00000000000000000"}).status_code == 400
    assert client.post("/api/v1/ec2-demo/terminate", json={"confirmation": "TERMINATE i-0123456789abcdef0"}).status_code == 200
    assert fake.terminated == [{"InstanceIds": ["i-0123456789abcdef0"]}]


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
    from types import SimpleNamespace
    provider = SimpleNamespace(_popen=None)
    core = SimpleNamespace(set_config_variable=lambda *args: None,
        get_component=lambda name: SimpleNamespace(get_provider=lambda name: provider))
    monkeypatch.setattr(ec2_demo.botocore.session, "get_session", lambda: core)
    class Session:
        def client(self, service, **kwargs):
            assert service == "sts"
            return SimpleNamespace(get_caller_identity=lambda: {"Account": "111111111111", "Arn": "bad"})
    monkeypatch.setattr(ec2_demo.boto3, "Session", lambda **kwargs: Session())
    import pytest
    with pytest.raises(ValueError):
        ec2_demo.clients()
