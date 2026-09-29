"""Azure's IPv4 subnet gateway is network address + 1 (constant memory)."""
import ipaddress
import sys


def gateway(cidr):
    network = ipaddress.IPv4Network(cidr, strict=True)
    if network.prefixlen > 29:
        raise ValueError("Azure subnets must be /29 or larger")
    return str(network.network_address + 1)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("Usage: get_nic_gw.py <IPv4 subnet CIDR>")
        print(gateway(sys.argv[1]))
    except ValueError as exc:
        sys.exit(str(exc))
