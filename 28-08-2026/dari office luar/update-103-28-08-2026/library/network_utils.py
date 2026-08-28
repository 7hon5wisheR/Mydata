# network_utils.py
# -*- coding: utf-8 -*-
"""
Shared network utility functions to avoid circular imports
"""
__version__ = "0.0.0.1"



# =====================================================================================================================================
#      NETWORK UTILS
#      Name                       : NETWORK UTILS 
#      Version                    : 0.0.0.1 - Shared network utility functions to avoid circular imports
#      Date Created               : 08-05-2026
#      Author                     : Saifuddin
# ======================================================================================================================================

import socket
import subprocess
import uuid


def get_ip(ifname):
    """Get IP address for given network interface"""
    try:
        res = subprocess.check_output(
            f"ip addr show {ifname}", shell=True
        ).decode()

        for line in res.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                return line.split()[1].split('/')[0]

    except Exception as e:
        print(f"get_ip error: {e}")

    return "0.0.0.0"


def get_local_ip():
    """Get local IP address by connecting to external server"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_mac_address(ifname="eth0"):
    """Get MAC address for given network interface"""
    try:
        with open(f"/sys/class/net/{ifname}/address") as f:
            return f.read().strip().lower()
    except Exception:
        mac = uuid.getnode()
        return ':'.join(
            [f"{(mac >> ele) & 0xff:02x}" for ele in range(0, 8 * 6, 8)][::-1]
        )


def get_hostname():
    """Get system hostname"""
    return socket.gethostname()
