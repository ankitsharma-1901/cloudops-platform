"""Offline structural checks; not a substitute for effective IAM simulation."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "terraform/aws/ec2-only/iam"


def test_dedicated_role_trust_requires_operator_and_mfa():
    policy = json.loads((ROOT / "trust-policy.json").read_text())
    statement, = policy["Statement"]
    assert statement["Principal"]["AWS"] == "arn:aws:iam::294665681966:user/cloudops-dev"
    assert statement["Condition"]["Bool"]["aws:MultiFactorAuthPresent"] == "true"


def test_role_never_grants_role_passing_or_state_deletion():
    policy = json.loads((ROOT / "permissions-policy.json").read_text())
    statements = policy["Statement"]
    assert all("iam:PassRole" not in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])
               for s in statements if s["Effect"] == "Allow")
    assert all("ec2:TerminateInstances" not in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])
               for s in statements if s["Effect"] == "Allow")
    state = next(s for s in statements if s["Sid"] == "StateReadWrite")
    assert state["Action"] == ["s3:GetObject", "s3:PutObject"]
    lock = next(s for s in statements if s["Sid"] == "NativeLock")
    assert lock["Resource"].endswith("/terraform.tfstate.tflock")
    assert "s3:DeleteObject" in lock["Action"]


def test_network_interface_requires_no_public_ipv4():
    statements = json.loads((ROOT / "permissions-policy.json").read_text())["Statement"]
    allow = next(s for s in statements if s["Sid"] == "LaunchNetworkInterface")
    deny = next(s for s in statements if s["Sid"] == "DenyPublicIPv4")
    assert allow["Condition"]["Bool"]["ec2:AssociatePublicIpAddress"] == "false"
    assert deny["Effect"] == "Deny"
    assert deny["Condition"]["Bool"]["ec2:AssociatePublicIpAddress"] == "true"


def test_bucket_listing_uses_only_exact_backend_prefixes():
    statements = json.loads((ROOT / "permissions-policy.json").read_text())["Statement"]
    listing = next(s for s in statements if s["Sid"] == "ListStatePrefix")
    assert listing["Condition"]["StringEquals"]["s3:prefix"] == [
        "cloudops/development/ec2-only/terraform.tfstate",
        "cloudops/development/ec2-only/terraform.tfstate.tflock",
        "cloudops/workspaces/",
    ]
