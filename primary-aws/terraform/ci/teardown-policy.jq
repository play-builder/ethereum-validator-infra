# Input: `terraform show -json` for a destroy saved plan.
# A destroy plan is publishable only after the previous approved preparation
# apply persisted both provider-side destroy opt-ins in Terraform state:
#   aws_instance.node        disable_api_termination == false
#   aws_s3_bucket.staging[0] force_destroy           == true

def active_changes:
  [
    .resource_changes[]?
    | select((.change.actions // []) != ["no-op"] and (.change.actions // []) != ["read"])
  ];

def prepared_delete($address; $type; $attr; $prepared):
  .address == $address
  and .type == $type
  and .change.actions == ["delete"]
  and .change.before[$attr] == $prepared;

# Terraform records each successful delete in remote state even when a later
# delete in the same apply fails. On a newly approved resume plan, a protected
# resource may therefore be absent. If it is still present, it must remain the
# exact prepared delete; duplicates and unprepared entries stay fail-closed.
def absent_or_prepared_delete($changes; $address; $type; $attr; $prepared):
  ($changes | map(select(.address == $address))) as $matches
  | ($matches | length) <= 1
    and all($matches[]; prepared_delete($address; $type; $attr; $prepared));

active_changes as $changes
| ($changes | length) > 0
  and all($changes[]; .change.actions == ["delete"])
  and absent_or_prepared_delete($changes; "aws_instance.node"; "aws_instance"; "disable_api_termination"; false)
  and (
    if $staging_enabled then
      absent_or_prepared_delete($changes; "aws_s3_bucket.staging[0]"; "aws_s3_bucket"; "force_destroy"; true)
    else
      ($changes | map(select(.address == "aws_s3_bucket.staging[0]")) | length) == 0
    end
  )
