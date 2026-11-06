# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Stop every booth process left running from an earlier start.

    uv run stop.py              stop them
    uv run stop.py --dry-run    only list what would be stopped

Use it when a start fails with "WinError 10048" / "address already in use": an earlier
photobooth-app (or run.py, the uploader, the gesture trigger) is still running, usually
because the terminal it ran in was closed instead of stopped with Ctrl+C.

Only processes of this repo are touched: a command line that runs photobooth(.exe), main.py,
gesture_trigger.py or run.py *and* points into this folder. Editors, language servers and
other Python programs are left alone. If something else holds photobooth-app's port, it is
reported, not killed.
"""

import argparse, json, os, re, signal, subprocess, sys, time
from pathlib import Path
from urllib.parse import urlsplit

from run import ROOT, read_env, setting, url_answers

TARGETS = {"photobooth", "photobooth.exe", "main.py", "gesture_trigger.py", "run.py"}
TOKEN = re.compile(r'"([^"]*)"|(\S+)')


def processes():
    """{pid: (parent_pid, command_line)} for every process we can see."""
    if os.name == "nt":
        script = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine"
                  " | ConvertTo-Json -Compress")
        raw = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                             capture_output=True, text=True, check=True).stdout
        rows = json.loads(raw or "[]")
        return {r["ProcessId"]: (r["ParentProcessId"], r["CommandLine"] or "") for r in rows}
    raw = subprocess.run(["ps", "-eo", "pid=,ppid=,args="], capture_output=True, text=True,
                         check=True).stdout
    procs = {}
    for line in raw.splitlines():
        pid, ppid, *cmd = line.split(None, 2)
        procs[int(pid)] = (int(ppid), cmd[0] if cmd else "")
    return procs


def is_booth(cmd):
    """True if this command line runs one of the booth's programs from this repo."""
    root = str(ROOT).lower().replace("/", "\\") if os.name == "nt" else str(ROOT)
    norm = cmd.lower().replace("/", "\\") if os.name == "nt" else cmd
    if root not in norm:
        return False
    names = {Path(a or b).name.lower() for a, b in TOKEN.findall(cmd)}
    return bool(names & TARGETS)


def ancestors(pid, procs):
    seen = set()
    while pid in procs and pid not in seen:
        seen.add(pid)
        pid = procs[pid][0]
    return seen


def port_holders(port):
    """PIDs listening on this TCP port."""
    if os.name != "nt":
        out = subprocess.run(["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
                             capture_output=True, text=True).stdout
        return {int(p) for p in out.split()}
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 5 and parts[3] == "LISTENING" and parts[1].rsplit(":", 1)[-1] == str(port):
            pids.add(int(parts[4]))
    return pids


def kill(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass


def short(cmd, width=110):
    cmd = " ".join(cmd.split())
    return cmd if len(cmd) <= width else cmd[:width - 3] + "..."


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stop booth processes left over from an earlier start.")
    parser.add_argument("--dry-run", action="store_true", help="only list what would be stopped")
    args = parser.parse_args(argv)

    url = setting(read_env(ROOT / ".env"), "PHOTOBOOTH_URL", "http://localhost:8000").rstrip("/")
    port = urlsplit(url).port or 80

    procs = processes()
    me = ancestors(os.getpid(), procs)                      # never this script or its `uv run`
    booth = {pid for pid, (_, cmd) in procs.items() if pid not in me and is_booth(cmd)}
    # Kill only the top of each tree: taskkill /T takes the children with it.
    roots = sorted(pid for pid in booth if not (ancestors(procs[pid][0], procs) & booth))

    if not roots:
        print("No booth processes running.")
    for pid in roots:
        print(f"{'would stop' if args.dry_run else 'stopping'} {pid}: {short(procs[pid][1])}")
        if not args.dry_run:
            kill(pid)

    if args.dry_run:
        return 0

    deadline = time.monotonic() + 5
    while url_answers(url) and time.monotonic() < deadline:
        time.sleep(0.25)

    others = port_holders(port) - booth
    if others:
        for pid in sorted(others):
            cmd = procs.get(pid, (0, ""))[1] or "(command line not visible)"
            print(f"port {port} is still held by {pid}, not a booth process, left alone: {short(cmd)}")
            if os.name == "nt":
                print(f"  stop it yourself if it's safe:  taskkill /T /F /PID {pid}")
        return 1
    print(f"port {port} is free.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
