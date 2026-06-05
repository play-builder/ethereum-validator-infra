#!/usr/bin/env python3
"""Fail-closed TCP boundary probe that works on macOS and Linux."""

from __future__ import annotations

import argparse
import ipaddress
import socket
import sys


def probe_port(host: str, port: int, timeout: float) -> str:
    connection = None
    try:
        connection = socket.create_connection((host, port), timeout=timeout)
    except ConnectionRefusedError:
        return "refused"
    except (socket.timeout, TimeoutError):
        return "timeout"
    except OSError as exc:
        return f"error:{exc.errno if exc.errno is not None else 'unknown'}"
    finally:
        if connection is not None:
            connection.close()
    return "open"


def matches_expectation(state: str, expectation: str) -> bool:
    if expectation == "refused":
        return state == "refused"
    if expectation == "closed":
        return state == "timeout"
    if expectation == "open":
        return state == "open"
    return False


def run_probe(host: str, ports: list[int], expectation: str, timeout: float) -> int:
    failures = 0
    for port in ports:
        state = probe_port(host, port, timeout)
        verdict = "PASS" if matches_expectation(state, expectation) else "FAIL"
        print(f"PORT_PROBE={verdict} host={host} port={port} state={state}")
        if verdict == "FAIL":
            failures += 1
    overall = "PASS" if failures == 0 else "FAIL"
    print(f"TCP_BOUNDARY_PROBE={overall} expect={expectation} ports={len(ports)}")
    return 0 if failures == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check that every TCP port has one exact network-boundary result."
    )
    parser.add_argument("--host", required=True)
    parser.add_argument("--ports", nargs="+", required=True, type=int)
    parser.add_argument("--expect", choices=("refused", "closed", "open"), required=True)
    parser.add_argument("--timeout", default=3.0, type=float)
    args = parser.parse_args()

    try:
        address = ipaddress.ip_address(args.host)
    except ValueError as exc:
        raise SystemExit("TCP_BOUNDARY_PROBE=FAIL reason=host_must_be_ip") from exc
    if address.version != 4 or not address.is_global:
        raise SystemExit("TCP_BOUNDARY_PROBE=FAIL reason=host_must_be_public_ipv4")
    if len(set(args.ports)) != len(args.ports) or any(not 1 <= port <= 65535 for port in args.ports):
        raise SystemExit("TCP_BOUNDARY_PROBE=FAIL reason=invalid_ports")
    if not 0 < args.timeout <= 30:
        raise SystemExit("TCP_BOUNDARY_PROBE=FAIL reason=invalid_timeout")

    sys.exit(run_probe(args.host, args.ports, args.expect, args.timeout))


if __name__ == "__main__":
    main()
