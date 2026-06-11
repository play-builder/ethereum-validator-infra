resource "aws_security_group" "node" {
  name        = "${local.project}-${var.network}-node"
  description = "Ethereum node: P2P open, admin/WG pinned, no public RPC"
  vpc_id      = aws_vpc.this.id
}

# --- P2P (공개가 정상) ---
resource "aws_vpc_security_group_ingress_rule" "el_p2p_tcp" {
  security_group_id = aws_security_group.node.id
  description       = "EL devp2p"
  from_port         = 30303
  to_port           = 30303
  ip_protocol       = "tcp"
  cidr_ipv4         = "0.0.0.0/0"
}
resource "aws_vpc_security_group_ingress_rule" "el_p2p_udp" {
  security_group_id = aws_security_group.node.id
  description       = "EL discovery"
  from_port         = 30303
  to_port           = 30303
  ip_protocol       = "udp"
  cidr_ipv4         = "0.0.0.0/0"
}
resource "aws_vpc_security_group_ingress_rule" "cl_p2p_tcp" {
  security_group_id = aws_security_group.node.id
  description       = "CL libp2p"
  from_port         = 9000
  to_port           = 9000
  ip_protocol       = "tcp"
  cidr_ipv4         = "0.0.0.0/0"
}
resource "aws_vpc_security_group_ingress_rule" "cl_p2p_udp" {
  security_group_id = aws_security_group.node.id
  description       = "CL discovery"
  from_port         = 9000
  to_port           = 9000
  ip_protocol       = "udp"
  cidr_ipv4         = "0.0.0.0/0"
}
resource "aws_vpc_security_group_ingress_rule" "cl_quic_udp" {
  security_group_id = aws_security_group.node.id
  description       = "CL QUIC"
  from_port         = 9001
  to_port           = 9001
  ip_protocol       = "udp"
  cidr_ipv4         = "0.0.0.0/0"
}

# --- 관리 (고정 IP만) ---
resource "aws_vpc_security_group_ingress_rule" "ssh_admin" {
  for_each          = toset(var.admin_cidrs)
  security_group_id = aws_security_group.node.id
  description       = "SSH admin"
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
  cidr_ipv4         = each.value
}

# --- WireGuard: 확정된 백업 peer 공인 IP만 (D9) ---
resource "aws_vpc_security_group_ingress_rule" "wireguard_peer" {
  for_each          = var.backup_peer_public_ip == null ? {} : { peer = var.backup_peer_public_ip }
  security_group_id = aws_security_group.node.id
  description       = "WireGuard from configured backup peer only"
  from_port         = 51820
  to_port           = 51820
  ip_protocol       = "udp"
  cidr_ipv4         = "${each.value}/32"
}

# 주의: 8545(EL RPC), 8551(engine), 5052(BN API), 9090(Prom), 5054/5064(metrics)는
# 공개 규칙이 존재하지 않는다. 내부/터널 트래픽은 호스트 nftables가 통제한다.

resource "aws_vpc_security_group_egress_rule" "all_out" {
  security_group_id = aws_security_group.node.id
  description       = "outbound open (P2P dial-out, apt, checkpoint sync)"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}
