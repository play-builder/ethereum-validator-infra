#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
pr="$root/.github/workflows/terraform-pr.yml"
deploy="$root/.github/workflows/terraform-deploy.yml"
teardown="$root/.github/workflows/terraform-teardown.yml"
policy="$root/primary-aws/terraform/ci/plan-policy.jq"
summary_filter="$root/primary-aws/terraform/ci/plan-summary.jq"
runtime_manifest="$root/primary-aws/terraform/ci/runtime-inputs.json"
runtime_renderer="$root/shared/scripts/render-terraform-ci-runtime.py"
prepare_policy="$root/primary-aws/terraform/ci/teardown-prep-policy.jq"
destroy_policy="$root/primary-aws/terraform/ci/teardown-policy.jq"
codeowners="$root/.github/CODEOWNERS"

fail() { printf 'TERRAFORM_CICD_BOUNDARY=FAIL reason=%s\n' "$1" >&2; exit 1; }

for required in \
  "$pr" "$deploy" "$teardown" "$policy" "$summary_filter" \
  "$runtime_manifest" "$runtime_renderer" "$prepare_policy" "$destroy_policy" \
  "$codeowners"
do
  test -f "$required" || fail required_surface_missing
done

# Pull-request checks never obtain AWS credentials. Deploy/teardown jobs derive every
# runtime value from the reviewed manifest instead of mutable repository variables.
! rg -n 'id-token: write|configure-aws-credentials|aws-actions/' "$pr" >/dev/null || fail pr_credential_boundary
! rg -n 'TF_VAR_[A-Z0-9_]+|\$\{\{[[:space:]]*vars\.' "$pr" "$deploy" "$teardown" >/dev/null || fail mutable_runtime_variable_boundary
for workflow in "$deploy" "$teardown"; do
  rg -q 'RUNTIME_MANIFEST: primary-aws/terraform/ci/runtime-inputs.json' "$workflow" || fail tracked_manifest_missing
  rg -q 'runtime-inputs.canonical.json' "$workflow" || fail canonical_manifest_summary_missing
  rg -q 'runtime_manifest_sha256' "$workflow" || fail runtime_manifest_hash_binding_missing
  rg -q 'terraform_apply_role_arn' "$workflow" || fail apply_role_metadata_binding_missing
  rg -q 'cancel-in-progress: false' "$workflow" || fail cancelling_concurrency
  rg -q 'metadata binding mismatch' "$workflow" || fail metadata_fail_closed_missing
  rg -q 'expired plan metadata' "$workflow" || fail expiry_fail_closed_missing
  rg -q 'Authorization: Bearer \$GH_READ_TOKEN' "$workflow" || fail authenticated_main_lookup_missing
  rg -q '\$GITHUB_API_URL/repos/\$GITHUB_REPOSITORY/git/ref/heads/main' "$workflow" || fail exact_main_api_missing
done
test "$(rg -c 'python3 shared/scripts/render-terraform-ci-runtime.py' "$deploy")" -eq 2 || fail deploy_renderer_count
test "$(rg -c 'python3 shared/scripts/render-terraform-ci-runtime.py' "$teardown")" -eq 2 || fail teardown_renderer_count
test "$(rg -c -- '--mode teardown' "$teardown")" -eq 2 || fail teardown_mode_binding

# The normal deploy, protected-destroy preparation, and final destroy are three
# explicit policy gates. The final apply consumes only the already saved tf.plan.
rg -q 'prepare_teardown:' "$deploy" || fail prepare_teardown_dispatch_missing
rg -q 'teardown-prep-policy.jq' "$deploy" || fail prepare_teardown_policy_missing
rg -q 'TEARDOWN_PREP=PASS' "$deploy" || fail prepare_teardown_gate_missing
rg -q 'admin_cidrs=.*admin_cidrs.*ci\.auto\.tfvars\.json' "$deploy" || fail plan_policy_admin_binding_missing
backup_extractor='backup_peer="$(jq -c '\''if has("backup_peer_public_ip") then .backup_peer_public_ip else error("missing backup_peer_public_ip") end'\'' "$runtime_dir/ci.auto.tfvars.json")"'
staging_extractor='staging_enabled="$(jq -c '\''if (has("enable_staging_bucket") and ((.enable_staging_bucket | type) == "boolean")) then .enable_staging_bucket else error("missing or invalid enable_staging_bucket") end'\'' "$runtime_dir/ci.auto.tfvars.json")"'
rg -F -q "$backup_extractor" "$deploy" || fail plan_policy_backup_binding_missing
for workflow in "$deploy" "$teardown"; do
  rg -F -q "$staging_extractor" "$workflow" || fail staging_boolean_binding_missing
done
rg -F -q -- '--argjson admin_cidrs "$admin_cidrs"' "$deploy" || fail plan_policy_admin_arg_missing
rg -F -q -- '--argjson backup_peer "$backup_peer"' "$deploy" || fail plan_policy_backup_arg_missing
rg -q 'teardown-policy.jq' "$teardown" || fail teardown_policy_missing
rg -q 'TEARDOWN_PASS=PASS' "$teardown" || fail teardown_gate_missing
rg -q 'environment: hoodi-testnet-dev$' "$deploy" || fail deploy_approval_environment_missing
rg -q 'environment: hoodi-testnet-dev-teardown$' "$teardown" || fail teardown_approval_environment_missing
test "$(rg -c 'terraform -chdir=primary-aws/terraform apply -input=false -lock-timeout=5m tf.plan' "$deploy")" -eq 1 || fail deploy_exact_apply_count
test "$(rg -c 'terraform -chdir=primary-aws/terraform apply -input=false -lock-timeout=5m tf.plan' "$teardown")" -eq 1 || fail teardown_exact_apply_count
! rg -n 'actions/upload-artifact|tf\.plan.*GITHUB_OUTPUT|plan\.json.*GITHUB_OUTPUT' "$deploy" "$teardown" >/dev/null || fail raw_plan_github_artifact
rg -q 'plan_object_key="\$GITHUB_REPOSITORY/hoodi-testnet-dev/' "$deploy" || fail deploy_s3_prefix_mismatch
rg -q 'plan_object_key="\$GITHUB_REPOSITORY/hoodi-testnet-dev-teardown/' "$teardown" || fail teardown_s3_prefix_mismatch
! rg -n '^\s+test .*\$\{\{ inputs\.' "$deploy" "$teardown" >/dev/null || fail dispatch_input_shell_interpolation

# Changing the renderer changes AWS account, role, backend, and Terraform inputs.
# An exact helper rule or the broader /shared/ rule must assign the security team.
# Distribution ZIPs use the placeholder; PART 01 renders the verified organization.
runtime_renderer_owner="$(awk '$1 == "/shared/scripts/render-terraform-ci-runtime.py" {print $2}' "$codeowners")"
if [ -z "$runtime_renderer_owner" ]; then
  runtime_renderer_owner="$(awk '$1 == "/shared/" {print $2}' "$codeowners")"
fi
[[ "$runtime_renderer_owner" =~ ^@[A-Za-z0-9][A-Za-z0-9_.-]{0,38}/security-approvers$ ]] \
  || fail runtime_renderer_codeowner_missing
if [ -n "${GITHUB_REPOSITORY:-}" ]; then
  github_org="${GITHUB_REPOSITORY%%/*}"
  test "$GITHUB_REPOSITORY" != "$github_org" || fail github_repository_invalid
  test "$runtime_renderer_owner" = "@${github_org}/security-approvers" \
    || fail runtime_renderer_codeowner_repository_mismatch
fi

# jq -e treats valid JSON null/false results as a failing exit status. These
# runtime values are intentionally nullable/boolean, so their extractors must
# preserve the value and fail only when the member is absent or mistyped.
backup_peer="$(printf '%s\n' '{"backup_peer_public_ip":null}' | jq -c 'if has("backup_peer_public_ip") then .backup_peer_public_ip else error("missing backup_peer_public_ip") end')" \
  || fail null_backup_extractor_failed
test "$backup_peer" = "null" || fail null_backup_extractor_changed
staging_enabled="$(printf '%s\n' '{"enable_staging_bucket":false}' | jq -c 'if (has("enable_staging_bucket") and ((.enable_staging_bucket | type) == "boolean")) then .enable_staging_bucket else error("missing or invalid enable_staging_bucket") end')" \
  || fail false_staging_extractor_failed
test "$staging_enabled" = "false" || fail false_staging_extractor_changed

ordinary_unknown='{"resource_changes":[{"address":"aws_instance.node","type":"aws_instance","change":{"actions":["create"],"after":{},"after_unknown":{"id":true},"after_sensitive":{}}}]}'
printf '%s\n' "$ordinary_unknown" \
  | jq -e --argjson admin_cidrs '["203.0.113.10/32"]' --argjson backup_peer null -f "$policy" >/dev/null \
  || fail ordinary_unknown_rejected

public_ingress='{"resource_changes":[{"address":"aws_vpc_security_group_ingress_rule.public","type":"aws_vpc_security_group_ingress_rule","change":{"actions":["create"],"after":{"cidr_ipv4":"0.0.0.0/0"},"after_unknown":{},"after_sensitive":{}}}]}'
if printf '%s\n' "$public_ingress" \
  | jq -e --argjson admin_cidrs '["203.0.113.10/32"]' --argjson backup_peer null -f "$policy" >/dev/null; then
  fail public_ingress_allowed
else
  test "$?" -eq 1 || fail plan_policy_runtime_error
fi

unknown_sensitive='{"resource_changes":[{"address":"example_secret.nested","type":"example_secret","change":{"actions":["create"],"after":{},"after_unknown":{"secret_object":{"value":true}},"after_sensitive":{"secret_object":true}}}]}'
if printf '%s\n' "$unknown_sensitive" \
  | jq -e --argjson admin_cidrs '["203.0.113.10/32"]' --argjson backup_peer null -f "$policy" >/dev/null; then
  fail unknown_sensitive_allowed
else
  test "$?" -eq 1 || fail plan_policy_runtime_error
fi

ssh_bound='{"resource_changes":[{"address":"aws_vpc_security_group_ingress_rule.ssh_admin[\"203.0.113.10/32\"]","type":"aws_vpc_security_group_ingress_rule","change":{"actions":["create"],"after":{"cidr_ipv4":"203.0.113.10/32","ip_protocol":"tcp","from_port":22,"to_port":22},"after_unknown":{},"after_sensitive":{}}}]}'
printf '%s\n' "$ssh_bound" \
  | jq -e --argjson admin_cidrs '["203.0.113.10/32"]' --argjson backup_peer null -f "$policy" >/dev/null \
  || fail plan_policy_rejected_manifest_bound_ssh
if printf '%s\n' "$ssh_bound" \
  | jq -e --argjson admin_cidrs '["198.51.100.20/32"]' --argjson backup_peer null -f "$policy" >/dev/null; then
  fail plan_policy_ignored_admin_cidr_binding
else
  test "$?" -eq 1 || fail plan_policy_runtime_error
fi

prepare_good='{"resource_changes":[{"address":"aws_instance.node","type":"aws_instance","change":{"actions":["update"],"before":{"disable_api_termination":true,"instance_type":"m7g.xlarge"},"after":{"disable_api_termination":false,"instance_type":"m7g.xlarge"},"after_unknown":{}}},{"address":"aws_s3_bucket.staging[0]","type":"aws_s3_bucket","change":{"actions":["update"],"before":{"force_destroy":false,"bucket":"approved-staging"},"after":{"force_destroy":true,"bucket":"approved-staging"},"after_unknown":{}}}],"output_changes":{}}'
printf '%s\n' "$prepare_good" | jq -e --argjson staging_enabled true -f "$prepare_policy" >/dev/null || fail prepare_policy_rejected_exact_opt_in

prepare_resume='{"resource_changes":[{"address":"aws_instance.node","type":"aws_instance","change":{"actions":["no-op"],"before":{"disable_api_termination":false,"instance_type":"m7g.xlarge"},"after":{"disable_api_termination":false,"instance_type":"m7g.xlarge"},"after_unknown":{}}},{"address":"aws_s3_bucket.staging[0]","type":"aws_s3_bucket","change":{"actions":["update"],"before":{"force_destroy":false,"bucket":"approved-staging"},"after":{"force_destroy":true,"bucket":"approved-staging"},"after_unknown":{}}}],"output_changes":{}}'
printf '%s\n' "$prepare_resume" | jq -e --argjson staging_enabled true -f "$prepare_policy" >/dev/null || fail prepare_policy_rejected_partial_failure_resume

prepare_mixed='{"resource_changes":[{"address":"aws_instance.node","type":"aws_instance","change":{"actions":["update"],"before":{"disable_api_termination":true,"instance_type":"m7g.xlarge"},"after":{"disable_api_termination":false,"instance_type":"m7g.2xlarge"},"after_unknown":{}}},{"address":"aws_s3_bucket.staging[0]","type":"aws_s3_bucket","change":{"actions":["update"],"before":{"force_destroy":false,"bucket":"approved-staging"},"after":{"force_destroy":true,"bucket":"approved-staging"},"after_unknown":{}}}],"output_changes":{}}'
if printf '%s\n' "$prepare_mixed" | jq -e --argjson staging_enabled true -f "$prepare_policy" >/dev/null; then
  fail prepare_policy_allowed_mixed_change
else
  test "$?" -eq 1 || fail prepare_policy_runtime_error
fi

destroy_good='{"resource_changes":[{"address":"aws_instance.node","type":"aws_instance","change":{"actions":["delete"],"before":{"disable_api_termination":false}}},{"address":"aws_s3_bucket.staging[0]","type":"aws_s3_bucket","change":{"actions":["delete"],"before":{"force_destroy":true}}}]}'
printf '%s\n' "$destroy_good" | jq -e --argjson staging_enabled true -f "$destroy_policy" >/dev/null || fail teardown_policy_rejected_prepared_destroy

destroy_resume='{"resource_changes":[{"address":"aws_sns_topic.alerts","type":"aws_sns_topic","change":{"actions":["delete"],"before":{"name":"eth-failover-hoodi-alerts"}}}]}'
printf '%s\n' "$destroy_resume" | jq -e --argjson staging_enabled true -f "$destroy_policy" >/dev/null || fail teardown_policy_rejected_partial_failure_resume

destroy_unprepared='{"resource_changes":[{"address":"aws_instance.node","type":"aws_instance","change":{"actions":["delete"],"before":{"disable_api_termination":true}}},{"address":"aws_s3_bucket.staging[0]","type":"aws_s3_bucket","change":{"actions":["delete"],"before":{"force_destroy":true}}}]}'
if printf '%s\n' "$destroy_unprepared" | jq -e --argjson staging_enabled true -f "$destroy_policy" >/dev/null; then
  fail teardown_policy_allowed_unprepared_destroy
else
  test "$?" -eq 1 || fail teardown_policy_runtime_error
fi

summary_input='{"resource_changes":[{"address":"aws_sns_topic_subscription.operators[\"operator@example.com\"]","type":"aws_sns_topic_subscription","change":{"actions":["create"],"before":null,"after":{"endpoint":"DO_NOT_LEAK"}}}]}'
summary="$(printf '%s\n' "$summary_input" | jq -c -e -f "$summary_filter")" || fail plan_summary_runtime_error
test "$summary" = '{"counts":{"create":1,"update":0,"delete":0,"replace":0,"read":0},"changes":[{"address":"aws_sns_topic_subscription.operators[\"<redacted-key>\"]","actions":["create"]}]}' || fail plan_summary_contract
[[ "$summary" != *DO_NOT_LEAK* ]] || fail plan_summary_value_leak
[[ "$summary" != *operator@example.com* ]] || fail plan_summary_address_key_leak
printf 'TERRAFORM_CICD_BOUNDARY=PASS\n'
