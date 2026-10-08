# The dashboard (Control Room)

[← Back to the README](../README.md)


A web page for running Cozmo without a terminal: start/stop him in any mode,
live log and conversation, history, files, and a `.env` editor. Separate from
`cozmo_brain/` and standard-library only.

![The Control Room's Control page](images/dashboard.jpg)

```bash
./dashboard.sh                    # then open http://localhost:30540
```

Listens on this machine only by default; to open it from another computer
you must set `DASHBOARD_HOST` and `DASHBOARD_TOKEN`. There is no default
token; make one with
`python3 -c "import secrets; print(secrets.token_urlsafe(24))"`. Full guide (pages,
security, token, notes): [cozmo_dashboard/README.md](../cozmo_dashboard/README.md).
