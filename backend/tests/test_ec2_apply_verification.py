import copy
import json

import pytest

from app.artifact_store import ArtifactError
from app.ec2_apply_verification import verify_completed_instance, verify_prior_state
from app.ec2_only_policy import AMI, SUBNET, VPC

INSTANCE = "i-0123456789abcdef0"
GROUP = "sg-0123456789abcdef0"


def test_prior_state_matching_and_missing_state():
    import io
    import zipfile
    def plan(state):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("tfstate", json.dumps(state))
        return data.getvalue()
    empty = {"version": 4, "serial": 0, "lineage": "", "resources": []}
    verify_prior_state(plan(empty), None)
    data_only = {**empty, "resources": [{"mode": "data", "type": "aws_subnet", "name": "selected"}]}
    verify_prior_state(plan(data_only), None)
    with pytest.raises(ArtifactError):
        verify_prior_state(plan({**empty, "resources": [{"mode": "managed", "type": "aws_instance", "name": "dev"}]}), None)
    previous = {"version": 4, "serial": 4, "lineage": "example", "resources": []}
    verify_prior_state(plan(previous), json.dumps(previous))
    with pytest.raises(ArtifactError):
        verify_prior_state(plan(previous), json.dumps({**previous, "serial": 5}))
    with pytest.raises(ArtifactError):
        verify_prior_state(plan(previous), None)


@pytest.fixture
def records():
    state = {"version": 4, "resources": [{"mode": "managed", "type": "aws_instance", "name": "dev",
        "instances": [{"attributes": {"id": INSTANCE}}]}]}
    instance = {"InstanceId": INSTANCE, "ImageId": AMI, "InstanceType": "t3.micro", "SubnetId": SUBNET,
        "VpcId": VPC, "SecurityGroups": [{"GroupId": GROUP}], "State": {"Name": "running"},
        "Tags": [{"Key": "Project", "Value": "CloudOps"}, {"Key": "Environment", "Value": "development"},
                 {"Key": "ManagedBy", "Value": "Terraform"}]}
    return state, {"Reservations": [{"OwnerId": "294665681966", "Instances": [instance]}]}


def test_matching_state_and_instance(records):
    state, instances = records
    assert verify_completed_instance(json.dumps(state), json.dumps(instances), GROUP) == INSTANCE


@pytest.mark.parametrize("fault", ["missing", "extra", "wrong_id", "wrong_ami", "wrong_group", "wrong_owner", "public_ip", "ipv6", "profile", "terminated", "missing_tag", "deposed"])
def test_uncertain_or_wrong_results_fail_closed(records, fault):
    state, instances = copy.deepcopy(records)
    instance = instances["Reservations"][0]["Instances"][0]
    if fault == "missing": instances["Reservations"][0]["Instances"] = []
    if fault == "extra": instances["Reservations"][0]["Instances"].append(copy.deepcopy(instance))
    if fault == "wrong_id": instance["InstanceId"] = "i-12345678"
    if fault == "wrong_ami": instance["ImageId"] = "ami-00000000000000000"
    if fault == "wrong_group": instance["SecurityGroups"] = [{"GroupId": "sg-12345678"}]
    if fault == "wrong_owner": instances["Reservations"][0]["OwnerId"] = "999999999999"
    if fault == "public_ip": instance["PublicIpAddress"] = "192.0.2.1"
    if fault == "ipv6": instance["NetworkInterfaces"] = [{"Ipv6Addresses": [{"Ipv6Address": "2001:db8::1"}]}]
    if fault == "profile": instance["IamInstanceProfile"] = {"Arn": "unexpected"}
    if fault == "terminated": instance["State"] = {"Name": "terminated"}
    if fault == "missing_tag": instance["Tags"] = []
    if fault == "deposed": state["resources"][0]["instances"][0]["deposed"] = "old"
    with pytest.raises(ArtifactError):
        verify_completed_instance(json.dumps(state), json.dumps(instances), GROUP)
