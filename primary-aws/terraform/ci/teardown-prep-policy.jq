# Input: `terraform show -json` for a prepare-teardown saved plan.
# `$staging_enabled` is supplied by the validated tracked runtime manifest.
#
# Protected destroy opt-ins are provider attributes that must flip in state:
#   aws_instance.node        disable_api_termination true  -> false
#   aws_s3_bucket.staging[0] force_destroy           false -> true

def active_changes:
  [
    .resource_changes[]?
    | select((.change.actions // []) != ["no-op"] and (.change.actions // []) != ["read"])
  ];

def exact_flip($address; $type; $attr; $from; $to):
  .address == $address
  and .type == $type
  and .change.actions == ["update"]
  and .change.before[$attr] == $from
  and .change.after[$attr] == $to
  and ((.change.before | del(.[$attr])) == (.change.after | del(.[$attr])))
  and ((.change.after_unknown // {}) == {});

def exact_already_prepared($address; $type; $attr; $to):
  .address == $address
  and .type == $type
  and .change.actions == ["no-op"]
  and .change.before[$attr] == $to
  and .change.after[$attr] == $to
  and ((.change.before | del(.[$attr])) == (.change.after | del(.[$attr])))
  and ((.change.after_unknown // {}) == {});

def exact_protected_state($address; $type; $attr; $from; $to):
  exact_flip($address; $type; $attr; $from; $to)
  or exact_already_prepared($address; $type; $attr; $to);

active_changes as $changes
| ([.resource_changes[]? | select(.address == "aws_instance.node")]) as $nodes
| ([.resource_changes[]? | select(.address == "aws_s3_bucket.staging[0]")]) as $staging
| ($nodes | length) == 1
  and ($nodes[0] | exact_protected_state("aws_instance.node"; "aws_instance"; "disable_api_termination"; true; false))
  and ($changes | length) >= 1
  and ($changes | all(
    exact_flip("aws_instance.node"; "aws_instance"; "disable_api_termination"; true; false)
    or exact_flip("aws_s3_bucket.staging[0]"; "aws_s3_bucket"; "force_destroy"; false; true)
  ))
  and (
    if $staging_enabled then
      ($staging | length) == 1
      and ($staging[0] | exact_protected_state("aws_s3_bucket.staging[0]"; "aws_s3_bucket"; "force_destroy"; false; true))
      and ($changes | length) <= 2
    else
      ($staging | length) == 0
      and ($changes | length) == 1
    end
  )
  and ([.output_changes[]? | select((.actions // []) != ["no-op"])] | length) == 0
