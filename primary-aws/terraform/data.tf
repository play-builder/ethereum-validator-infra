# 슬라이스 T1(기반)부터 모든 영역이 공유하는 조회 데이터 소스.
data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}
