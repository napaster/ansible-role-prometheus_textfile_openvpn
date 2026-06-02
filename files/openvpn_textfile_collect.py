#!/usr/bin/env python3
"""
openvpn_textfile_collect.py — parses /run/openvpn-server/*.status (v2 CSV)
and writes Prometheus textfile metrics matching natrontech/openvpn-exporter
naming, so the same Grafana dashboard works for both standalone exporter
(collectors with local Prometheus) and textfile-based VMs.

Output: /var/lib/prometheus-node-exporter/textfile_collector/openvpn.prom

Usage:
    openvpn_textfile_collect.py [STATUS_GLOB] [OUTPUT_FILE]

Defaults:
    STATUS_GLOB = /run/openvpn-server/*.status
    OUTPUT_FILE = /var/lib/prometheus-node-exporter/textfile_collector/openvpn.prom
"""
import csv
import glob
import os
import sys
import time
import tempfile

DEFAULT_STATUS_GLOB = "/run/openvpn-server/*.status"
DEFAULT_OUTPUT = "/var/lib/prometheus-node-exporter/textfile_collector/openvpn.prom"

STALE_THRESHOLD_SEC = 300  # status file older than 5min → openvpn_up=0


def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def parse_status(path: str):
    """Return dict with status_update_ts, clients list, parse_ok bool."""
    out = {
        "status_path": path,
        "ts": 0,
        "clients": [],
        "ok": False,
    }
    if not os.path.isfile(path):
        return out

    out["mtime"] = os.path.getmtime(path)

    try:
        with open(path, "r") as f:
            reader = csv.reader(f)
            for row in reader:
                if not row:
                    continue
                tag = row[0]
                if tag == "TIME" and len(row) >= 3:
                    try:
                        out["ts"] = int(row[2])
                    except ValueError:
                        pass
                elif tag == "CLIENT_LIST" and len(row) >= 9:
                    # CLIENT_LIST,cn,real,virt,virt6,rx,tx,since,since_t,username,...
                    out["clients"].append({
                        "cn": row[1],
                        "real": row[2],
                        "virt": row[3],
                        "rx": int(row[5] or 0),
                        "tx": int(row[6] or 0),
                        "since_t": int(row[8] or 0),
                        "username": row[9] if len(row) > 9 else "UNDEF",
                    })
        out["ok"] = True
    except Exception:
        pass
    return out


def emit(lines, name, type_, help_, samples):
    """Append HELP/TYPE/sample lines."""
    lines.append(f"# HELP {name} {help_}")
    lines.append(f"# TYPE {name} {type_}")
    lines.extend(samples)


def render(status_files):
    lines = []
    now = time.time()

    # openvpn_up
    up_samples = []
    update_samples = []
    count_samples = []
    rx_samples = []
    tx_samples = []
    route_samples = []

    for s in status_files:
        path = esc(s["status_path"])
        if s["ok"] and s.get("mtime", 0) > 0 and (now - s["mtime"]) < STALE_THRESHOLD_SEC:
            up = 1
        else:
            up = 0

        up_samples.append(f'openvpn_up{{status_path="{path}"}} {up}')
        if s["ts"]:
            update_samples.append(f'openvpn_status_update_time_seconds{{status_path="{path}"}} {s["ts"]}')
        count_samples.append(f'openvpn_server_connected_clients{{status_path="{path}"}} {len(s["clients"])}')

        for c in s["clients"]:
            cn = esc(c["cn"])
            real = esc(c["real"])
            virt = esc(c["virt"])
            since_t = c["since_t"]
            username = esc(c["username"])
            lbls = (
                f'common_name="{cn}",connection_time="{since_t}",real_address="{real}",'
                f'status_path="{path}",username="{username}",virtual_address="{virt}"'
            )
            rx_samples.append(f'openvpn_server_client_received_bytes_total{{{lbls}}} {c["rx"]}')
            tx_samples.append(f'openvpn_server_client_sent_bytes_total{{{lbls}}} {c["tx"]}')
            route_lbls = (
                f'common_name="{cn}",real_address="{real}",'
                f'status_path="{path}",virtual_address="{virt}"'
            )
            route_samples.append(
                f'openvpn_server_route_last_reference_time_seconds{{{route_lbls}}} {since_t}'
            )

    emit(lines, "openvpn_up", "gauge",
         "1 if the status file is fresh (mtime < 5min), 0 otherwise", up_samples)
    emit(lines, "openvpn_status_update_time_seconds", "gauge",
         "Unix timestamp of the last update written to the status file", update_samples)
    emit(lines, "openvpn_server_connected_clients", "gauge",
         "Number of currently connected clients per status file", count_samples)
    emit(lines, "openvpn_server_client_received_bytes_total", "counter",
         "Bytes received per OpenVPN client (counter; resets on reconnect)", rx_samples)
    emit(lines, "openvpn_server_client_sent_bytes_total", "counter",
         "Bytes sent per OpenVPN client", tx_samples)
    emit(lines, "openvpn_server_route_last_reference_time_seconds", "gauge",
         "Unix timestamp of last reference to the route per client", route_samples)

    return "\n".join(lines) + "\n"


def atomic_write(out_path: str, content: str):
    out_dir = os.path.dirname(out_path)
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".openvpn.", suffix=".prom", dir=out_dir)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.chmod(tmp, 0o644)
        os.replace(tmp, out_path)
    except Exception:
        try: os.unlink(tmp)
        except: pass
        raise


def main():
    status_glob = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_STATUS_GLOB
    out_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUTPUT

    files = sorted(glob.glob(status_glob))
    parsed = [parse_status(p) for p in files]
    atomic_write(out_path, render(parsed))


if __name__ == "__main__":
    main()
