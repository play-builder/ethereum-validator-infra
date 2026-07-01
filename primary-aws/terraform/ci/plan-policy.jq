# The input is local `terraform show -json tf.plan` data.  This filter emits only
# true/false; callers must never print that input into a workflow log or artifact.
def destructive:
  .resource_changes[]?
  | select((.change.actions // []) | index("delete"));

def canonical_ipv4:
  type == "string"
  and test("^(0|[1-9][0-9]{0,2})(\\.(0|[1-9][0-9]{0,2})){3}$")
  and all(split(".")[]; (tonumber >= 0 and tonumber <= 255));

def canonical_ipv4_host_cidr:
  type == "string"
  and (
    split("/") as $parts
    | ($parts | length) == 2
      and $parts[1] == "32"
      and ($parts[0] | canonical_ipv4)
  );

# Every ingress rule in the current graph is allowlisted, not merely public
# ingress.  This closes /1 splitting, numeric protocol aliases, broad RPC rules,
# legacy aws_security_group_rule schemas, and unknown network-field bypasses.
def exact_vpc_ingress_shape:
  (.change.after // {}) as $after
  | (.change.after_unknown // {}) as $unknown
  | (($after.cidr_ipv6 // null) == null)
    and (($after.prefix_list_id // null) == null)
    and (($after.referenced_security_group_id // null) == null)
    and ([
      $unknown | keys[]?
      | select(. == "cidr_ipv4" or . == "cidr_ipv6" or . == "from_port" or . == "to_port" or . == "ip_protocol" or . == "prefix_list_id" or . == "referenced_security_group_id")
    ] | length == 0);

def approved_vpc_ingress:
  (.change.after // {}) as $after
  | (.type == "aws_vpc_security_group_ingress_rule")
    and exact_vpc_ingress_shape
    and (
      (
        .address == "aws_vpc_security_group_ingress_rule.el_p2p_tcp"
        and $after.ip_protocol == "tcp" and $after.from_port == 30303 and $after.to_port == 30303
        and $after.cidr_ipv4 == "0.0.0.0/0"
      )
      or (
        .address == "aws_vpc_security_group_ingress_rule.el_p2p_udp"
        and $after.ip_protocol == "udp" and $after.from_port == 30303 and $after.to_port == 30303
        and $after.cidr_ipv4 == "0.0.0.0/0"
      )
      or (
        .address == "aws_vpc_security_group_ingress_rule.cl_p2p_tcp"
        and $after.ip_protocol == "tcp" and $after.from_port == 9000 and $after.to_port == 9000
        and $after.cidr_ipv4 == "0.0.0.0/0"
      )
      or (
        .address == "aws_vpc_security_group_ingress_rule.cl_p2p_udp"
        and $after.ip_protocol == "udp" and $after.from_port == 9000 and $after.to_port == 9000
        and $after.cidr_ipv4 == "0.0.0.0/0"
      )
      or (
        .address == "aws_vpc_security_group_ingress_rule.cl_quic_udp"
        and $after.ip_protocol == "udp" and $after.from_port == 9001 and $after.to_port == 9001
        and $after.cidr_ipv4 == "0.0.0.0/0"
      )
      or (
        (.address | startswith("aws_vpc_security_group_ingress_rule.ssh_admin["))
        and $after.ip_protocol == "tcp" and $after.from_port == 22 and $after.to_port == 22
        and ($after.cidr_ipv4 | canonical_ipv4_host_cidr)
        and (($admin_cidrs | index($after.cidr_ipv4)) != null)
        and .address == ("aws_vpc_security_group_ingress_rule.ssh_admin[" + ($after.cidr_ipv4 | tojson) + "]")
      )
      or (
        .address == "aws_vpc_security_group_ingress_rule.wireguard_peer[\"peer\"]"
        and $after.ip_protocol == "udp" and $after.from_port == 51820 and $after.to_port == 51820
        and ($after.cidr_ipv4 | canonical_ipv4_host_cidr)
        and $backup_peer != null
        and ($backup_peer | canonical_ipv4)
        and $after.cidr_ipv4 == ($backup_peer + "/32")
      )
    );

def unapproved_ingress:
  .resource_changes[]?
  | if .type == "aws_vpc_security_group_ingress_rule" then
      select(approved_vpc_ingress == false)
    elif .type == "aws_security_group_rule" then
      select((.change.after.type // null) != "egress")
    elif .type == "aws_security_group" then
      select(((.change.after.ingress // []) | length) > 0 or ((.change.after_unknown // {}) | has("ingress")))
    else
      empty
    end;

def path_is_sensitive($sensitive; $path):
  [
    range(0; ($path | length) + 1) as $length
    | ($sensitive | try getpath($path[0:$length]) catch false)
  ]
  | any(. == true);

def unknown_sensitive:
  .resource_changes[]?
  | .change as $change
  | ($change.after_unknown // {}) as $unknown
  | ($change.after_sensitive // {}) as $sensitive
  | ($unknown | paths(scalars)) as $path
  | select(($unknown | getpath($path)) == true)
  | select(path_is_sensitive($sensitive; $path));

([destructive] | length == 0)
and ([unapproved_ingress] | length == 0)
and ([unknown_sensitive] | length == 0)
