"""Offline regression boundaries for administrator-reviewed IAM templates."""

import json
import configparser
import unittest
from pathlib import Path

DIRECTORY = Path(__file__).parent


def load(name):
    return json.loads((DIRECTORY / name).read_text(encoding="utf-8"))


def values(value):
    return value if isinstance(value, list) else [value]


class DeploymentPolicyTests(unittest.TestCase):
    def test_managed_policies_fit_size_limit_and_have_no_wildcard_actions(self):
        for name in ("network", "compute", "instance-iam"):
            policy = load(f"{name}-policy.json.example")
            with self.subTest(policy=name):
                self.assertLessEqual(len(json.dumps(policy, separators=(",", ":"))), 6144)
                for statement in policy["Statement"]:
                    self.assertEqual(statement["Effect"], "Allow")
                    self.assertTrue(all("*" not in action for action in values(statement["Action"])))

    def test_trust_requires_exact_nonroot_user_and_mfa(self):
        statement = load("trust-policy.json.example")["Statement"][0]
        self.assertEqual(statement["Action"], "sts:AssumeRole")
        self.assertEqual(statement["Condition"]["ArnEquals"]["aws:PrincipalArn"], "arn:aws:iam::<ACCOUNT_ID>:user/cloudops-dev")
        self.assertEqual(statement["Condition"]["Bool"]["aws:MultiFactorAuthPresent"], "true")
        self.assertEqual(statement["Principal"]["AWS"], "arn:aws:iam::<ACCOUNT_ID>:root")

    def test_source_can_assume_only_deployment_role(self):
        statements = load("source-user-policy.json.example")["Statement"]
        self.assertEqual(len(statements), 1)
        self.assertEqual(statements[0]["Action"], "sts:AssumeRole")
        self.assertEqual(statements[0]["Resource"], "arn:aws:iam::<ACCOUNT_ID>:role/cloudops-terraform-deploy")

    def test_ec2_writes_are_regional_and_read_wildcards_are_readonly(self):
        for name in ("network", "compute"):
            for statement in load(f"{name}-policy.json.example")["Statement"]:
                for action in values(statement["Action"]):
                    if action.startswith("ec2:"):
                        self.assertEqual(statement["Condition"]["StringEquals"]["aws:RequestedRegion"], "ca-central-1")
                    if statement["Resource"] == "*":
                        self.assertTrue(action.startswith(("ec2:Describe", "ssm:Describe")))

    def test_launch_enforces_hardening_and_reviewed_image(self):
        statements = {s["Sid"]: s for s in load("compute-policy.json.example")["Statement"]}
        instance = statements["LaunchSmallIMDSv2Instance"]["Condition"]
        self.assertEqual(instance["StringEquals"]["ec2:InstanceType"], "t3.micro")
        self.assertEqual(instance["StringEquals"]["ec2:MetadataHttpTokens"], "required")
        self.assertTrue(instance["ArnEquals"]["ec2:InstanceProfile"].endswith("/cloudops-dev-ec2-ssm"))
        self.assertEqual(statements["LaunchEncryptedRootVolume"]["Condition"]["Bool"]["ec2:Encrypted"], "true")
        self.assertTrue(statements["LaunchApprovedImage"]["Resource"].endswith("/<APPROVED_AMI_ID>"))

    def test_iam_cannot_manage_deployer_or_attach_arbitrary_policy(self):
        statements = load("instance-iam-policy.json.example")["Statement"]
        for statement in statements:
            self.assertTrue(statement["Resource"].endswith("/cloudops-dev-ec2-ssm"))
            self.assertNotIn("*", statement["Resource"])
        attachment = next(s for s in statements if s["Sid"] == "AttachOnlySSMCore")
        self.assertEqual(attachment["Condition"]["ArnEquals"]["iam:PolicyARN"], "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
        passing = next(s for s in statements if s["Sid"] == "PassOnlySSMRoleToEC2")
        self.assertEqual(passing["Condition"]["StringEquals"]["iam:PassedToService"], "ec2.amazonaws.com")

    def test_no_inbound_or_bucket_admin_grants(self):
        for name in ("network", "compute", "instance-iam"):
            for statement in load(f"{name}-policy.json.example")["Statement"]:
                for action in values(statement["Action"]):
                    self.assertNotIn("Ingress", action)
                    self.assertFalse(action.startswith("s3:"))
                    self.assertNotIn(action, ("iam:PutRolePolicy", "iam:CreateAccessKey", "iam:CreateUser"))

    def test_backend_delete_is_limited_to_lock(self):
        policy = json.loads((DIRECTORY.parent / "backend-policy.json.example").read_text())
        for statement in policy["Statement"]:
            if "s3:DeleteObject" in values(statement["Action"]):
                self.assertEqual(statement["Resource"], "arn:aws:s3:::<STATE_BUCKET>/cloudops/development/terraform.tfstate.tflock")

    def test_profile_chain_uses_cli_session_without_recursive_export(self):
        config = configparser.ConfigParser()
        config.read(DIRECTORY / "aws-config.ini.example")
        session = config["profile cloudops-deploy-session"]
        self.assertEqual(session["source_profile"], "cloudops-iam")
        self.assertTrue(session["role_arn"].endswith("/cloudops-terraform-deploy"))
        self.assertIn("mfa_serial", session)
        self.assertEqual(config["profile cloudops-deploy"]["credential_process"], "aws configure export-credentials --profile cloudops-deploy-session --format process")
        for section in config.values():
            self.assertTrue({"aws_access_key_id", "aws_secret_access_key", "aws_session_token"}.isdisjoint(section))


if __name__ == "__main__":
    unittest.main()
