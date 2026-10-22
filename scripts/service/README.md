# Keeping ECHOS alive (the layer above the watchdog)

Restarts are layered: the supervisor restarts dead agent processes, the **watchdog** restarts the runtime process, and an **OS service manager**
(launchd on macOS, systemd on Linux) restarts the whole `scripts/run_api.sh` tree, including the watchdog. Nothing here is installed for you: copy
the file, replace the placeholders and load it yourself.

- macOS: `com.echos.plist.template` -> `~/Library/LaunchAgents/com.echos.plist`, then `launchctl load ~/Library/LaunchAgents/com.echos.plist`
- Linux: `echos.service` -> `/etc/systemd/system/echos.service`, then `systemctl daemon-reload && systemctl enable --now echos`

Both start `scripts/run_api.sh --dry` (dry-run mail). Remove `--dry` only when real mail is intended and the SMTP and
address variables are set in `.env`. State lives in SQLite, so a restart loses nothing (see docs/METHOD.md, Limitations). Untested on a real host:
the files are templates, and a service manager's own failures (reboot, disk full) are outside what ECHOS can recover from.
