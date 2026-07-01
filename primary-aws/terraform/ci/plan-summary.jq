# Emit approval evidence only. Never include before/after values from a plan.
def actions:
  .change.actions // [];

def is_replace($actions):
  (($actions | index("create")) != null) and (($actions | index("delete")) != null);

# Terraform includes for_each keys in resource addresses. Those keys can contain
# operator email addresses or admin CIDRs, so approval evidence must redact them.
def sanitized_address:
  gsub("\\[\"(?:\\\\.|[^\"\\\\])*\"\\]"; "[\"<redacted-key>\"]");

{
  counts: (
    reduce .resource_changes[]? as $resource (
      {create: 0, update: 0, delete: 0, replace: 0, read: 0};
      ($resource | actions) as $actions
      | if is_replace($actions) then .replace += 1
        elif $actions == ["create"] then .create += 1
        elif $actions == ["update"] then .update += 1
        elif $actions == ["delete"] then .delete += 1
        elif $actions == ["read"] then .read += 1
        else .
        end
    )
  ),
  changes: [
    .resource_changes[]?
    | select((.change.actions // []) != ["no-op"])
    | {address: (.address | sanitized_address), actions: (.change.actions // [])}
  ]
}
