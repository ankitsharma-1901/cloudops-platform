# Terraform Foundations: Local Configuration Lab

This lab introduces Terraform input variables, validation, a resource, outputs,
plans, and local state. It uses only Terraform's built-in `terraform_data`
resource. It does **not** configure AWS, use cloud credentials, install
software, or create any AWS or other external resources. The region, instance
type, and install flags are values being practiced and stored, not actions.

## Sample configuration

The defaults model the existing CloudOps server-request example:

| Input | Default |
| --- | --- |
| `name` | `web-dev-01` |
| `environment` | `development` |
| `region` | `ca-central-1` |
| `instance_type` | `t3.micro` |
| `install_nginx` | `true` |
| `install_docker` | `true` |

The allowed environments, regions, and instance types match the current local
API choices. The two install flags are descriptive booleans; they do not
install Nginx or Docker.

## What each HCL block does

Terraform reads all `.tf` files in this directory as one configuration.

### `main.tf`

- The `terraform` block sets a minimum Terraform version of 1.4.0. That is the
  release that introduced the built-in `terraform_data` resource.
- The `resource "terraform_data" "server_configuration"` block records the
  selected inputs in Terraform state. Its `input` argument is an object built
  from the variables. This built-in resource needs no provider and has no
  provisioners or external side effects.

### `variables.tf`

Each `variable` block declares an input, its type, a human-readable description,
and a default. The `name` variable also checks a simple lowercase identifier
pattern. `environment`, `region`, and `instance_type` use `validation` blocks
to allow only the listed values. Terraform's `bool` type ensures the install
flags are true/false values rather than strings.

When a value fails validation, Terraform stops with that variable's
`error_message` before it can create or update the `terraform_data` state entry.

### `outputs.tf`

- `selected_configuration` exposes the resource's `output` object so the
  selected fields can be inspected after applying.
- `configuration_id` exposes the local identifier Terraform assigns to the
  `terraform_data` instance. It identifies this state object; it is not an
  AWS resource ID.

## Run the lab

Run these commands in PowerShell from the repository root. `init` prepares the
directory for Terraform; `fmt -check` checks HCL formatting without changing
files; `validate` checks the configuration. `plan` previews state changes.
The first plan should show one `terraform_data.server_configuration` object
being created and the two outputs becoming available. None of these commands
creates cloud resources.

```powershell
Set-Location "D:\Devops\PythonLearn\cloudops-platform\terraform\labs\01-foundations"
terraform init
terraform fmt -check
terraform validate
terraform plan -out=initial.tfplan
terraform show initial.tfplan
```

### Apply and inspect local state

Applying the saved plan records the sample object in local Terraform state.
The `apply` command below is provided for your own learning session; it is not
run as part of preparing this lab. Review the plan before you choose to run it.

```powershell
terraform apply initial.tfplan
terraform output
terraform state list
terraform state show terraform_data.server_configuration
```

The state list should contain `terraform_data.server_configuration`.
`state show` displays the saved input values. This is local state only and does
not represent a running server.

### Change one input and re-plan

Pass a temporary override to the plan command to model a different instance
type. This does not edit `variables.tf`; it applies only to this command and
the saved plan. Review the difference before applying it.

```powershell
terraform plan -var="instance_type=t3.small" -out=changed.tfplan
terraform show changed.tfplan
```

Compared with the first plan, Terraform should show the `terraform_data`
configuration changing in place: only `instance_type` changes from `t3.micro`
to `t3.small`; the other five values remain the same. Apply this saved plan
only if you choose to update the local state:

```powershell
terraform apply changed.tfplan
terraform output
terraform state show terraform_data.server_configuration
```

The output and state should now report `t3.small`. No machine is resized,
because no machine exists in this lab.

### Destroy the lab state

Review the destroy plan first. If you choose to proceed, `terraform destroy`
asks for confirmation before removing the `terraform_data` entry from local
state. It destroys no AWS or other external resources.

```powershell
terraform plan -destroy
terraform destroy
terraform state list
```

After destroy, the state list should no longer contain
`terraform_data.server_configuration`. Terraform configuration files remain
in place; destroy removes the managed state object, not this lab's source files.

## Local state and safety

Terraform uses its default local backend in this lab. The repository's
`.gitignore` excludes `.terraform/`, state files, plan files, and `.tfvars`
files. Do not put credentials or secrets in this sample configuration or
commit local state.

No `terraform apply` or `terraform destroy` was run while creating this lab.