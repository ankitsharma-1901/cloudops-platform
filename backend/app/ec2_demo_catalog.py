"""Fixed, backend-controlled settings for the direct EC2 demo.

These are not user-selectable provisioning options. Changing them requires
review of the demo IAM policy, networking, and ownership checks.
"""

ACCOUNT = "294665681966"
REGION = "ca-central-1"
AMI = "ami-009d185a5ea80b390"
SUBNET = "subnet-08dfc8d266e48fc91"
GROUP = "sg-068c70a801608db19"
VPC = "vpc-05828cfb0f6ae89e4"
PROFILE = "cloudops-ec2-demo-deploy"
ROLE = "cloudops-ec2-demo-deploy"
INSTANCE_TYPE = "t3.micro"
INSTANCE_NAME = "cloudops-ec2-demo"
ROOT_VOLUME_SIZE_GIB = 8
ROOT_VOLUME_TYPE = "gp3"
TAGS = {"Project": "CloudOps", "Environment": "development", "ManagedBy": "CloudOpsDemo"}
