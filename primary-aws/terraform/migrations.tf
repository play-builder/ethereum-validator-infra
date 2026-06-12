# 이전 단일 WireGuard 규칙 주소를 keyed collection 주소로 보존한다.
moved {
  from = aws_vpc_security_group_ingress_rule.wireguard_peer
  to   = aws_vpc_security_group_ingress_rule.wireguard_peer["peer"]
}
