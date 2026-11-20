"""Fail-closed local source pin for the EC2-only Terraform configuration.

This is an additional preflight, not proof of the source embedded in a plan.
"""

import hashlib

import hcl2

from app.artifact_store import ArtifactError, reject_links

APPROVED_SOURCE_SHA256 = {
    "main.tf": "13e247c5504dbba87d3c789c8d60201b5377bfc6310320533b9ba38dd414aa54",
    "variables.tf": "aba24568b420c356fc84dbaf1fd099055e6708951900ec65088cf38b851c84d4",
    "versions.tf": "8d1b8929ed34ce007b473feca9be5c9a1780079770e33558f4084b5e6ce572a2",
    "outputs.tf": "1c4defe0482f3bd78818fb93ac9b48db4dd98ab6c07d9640e2cd66c13f3d25e4",
}


def _contains_forbidden_block(value):
    if isinstance(value, dict):
        return any(key in {"module", "dynamic"} or _contains_forbidden_block(child)
                   for key, child in value.items())
    if isinstance(value, list):
        return any(_contains_forbidden_block(child) for child in value)
    return False


def require_reviewed_local_source(root):
    """Accept only the exact reviewed HCL; source edits require code review."""
    try:
        documents = {}
        for name, expected in APPROVED_SOURCE_SHA256.items():
            path = root / name
            reject_links(path)
            raw = path.read_bytes().replace(b"\r\n", b"\n")
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ArtifactError()
            documents[name] = hcl2.loads(raw.decode("utf-8"))
            if _contains_forbidden_block(documents[name]):
                raise ArtifactError()
        resource = documents["main.tf"]["resource"]
        if len(resource) != 1:
            raise ArtifactError()
        instance = resource[0]['"aws_instance"']['"dev"']
        if instance.get("iam_instance_profile") != '""' or "launch_template" in instance:
            raise ArtifactError()
    except (OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError):
        raise ArtifactError() from None
