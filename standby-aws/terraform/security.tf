resource "aws_security_group" "node" {
  name        = "${local.name}-node"
  description = "Hoodi P2P plus approved management paths; no public RPC or metrics"
  vpc_id      = aws_vpc.standby.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-node" }
}

resource "aws_vpc_security_group_ingress_rule" "ssh_admin" {
  for_each          = toset(var.admin_cidrs)
  security_group_id = aws_security_group.node.id
  description       = "SSH from approved operator /32"
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  cidr_ipv4         = each.value
}

resource "aws_vpc_security_group_ingress_rule" "el_p2p_tcp" {
  security_group_id = aws_security_group.node.id
  description       = "Nethermind Hoodi P2P TCP"
  ip_protocol       = "tcp"
  from_port         = 30303
  to_port           = 30303
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "el_p2p_udp" {
  security_group_id = aws_security_group.node.id
  description       = "Nethermind Hoodi P2P UDP"
  ip_protocol       = "udp"
  from_port         = 30303
  to_port           = 30303
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "cl_p2p_tcp" {
  security_group_id = aws_security_group.node.id
  description       = "Lighthouse Hoodi P2P TCP"
  ip_protocol       = "tcp"
  from_port         = 9000
  to_port           = 9000
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "cl_p2p_udp" {
  security_group_id = aws_security_group.node.id
  description       = "Lighthouse Hoodi P2P UDP"
  ip_protocol       = "udp"
  from_port         = 9000
  to_port           = 9000
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "cl_quic_udp" {
  security_group_id = aws_security_group.node.id
  description       = "Lighthouse Hoodi QUIC UDP"
  ip_protocol       = "udp"
  from_port         = 9001
  to_port           = 9001
  cidr_ipv4         = "0.0.0.0/0"
}
