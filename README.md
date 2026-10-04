# Moonwatch monitor

Checks the Moonvale Episode Tracker (plus the Story Hub, Duskwood Side Story, Support/FAQ pages and the Everbyte YouTube feed) every ~15 minutes on GitHub's servers and pushes alerts to your phone with **ntfy**. Works even when your phone is off or Moonwatch is closed.

## Files

```
monitor.py                      the monitor (standard library only)
.github/workflows/monitor.yml   runs it every 15 minutes
state.json                      created automatically on the first run
```

## Setup (about 5 minutes, all doable from a phone)

1. **ntfy app:** install it, tap +, subscribe to a topic with a long random name, for example `moonwatch-k8f3q9x2z7`. Anyone who knows the name can read it, so don't share it.
2. **New GitHub repo:** create a **public** repo (public repos get free Actions minutes). Upload `monitor.py`. Then use "Add file → Create new file", type `.github/workflows/monitor.yml` as the file name, and paste the workflow contents.
3. **Secret:** repo → Settings → Secrets and variables → Actions → New repository secret. Name: `NTFY_TOPIC`. Value: your topic name.
4. **First run:** Actions tab → "Moonvale monitor" → Run workflow. It saves a baseline (no alert). From then on it runs every ~15 minutes.
5. **Test the alert path:** Actions → "Moonvale monitor" → Run workflow → tick **"Send a test notification"** → Run. Your phone should buzz within a few seconds. If it doesn't, the topic name in the secret doesn't match the one you subscribed to.

## What alerts look like

| Priority | When |
|----------|------|
| High (4) | Tracker episode line, any progress %, new/removed feature section, any visible tracker text edit, new YouTube video |
| Normal (3) | Visible text edit on a sibling page, monitor problem, recovery |
| Low (2) | Invisible edit (builder or layout only), only when `PARANOID` is on (default) |

Failures: after 3 failed checks in a row for a source you get one "Monitor problem" alert, and one "Recovered" alert later. Nothing repeats in between.

## Good to know

- **First run is a baseline:** no alerts until something changes after it.
- **Timing:** GitHub often starts scheduled runs a few minutes late.
- **Inactivity:** GitHub pauses scheduled workflows in public repos with no activity for 60 days. The script refreshes a heartbeat in `state.json` monthly, which should keep the repo active. If alerts ever stop, open the Actions tab and re-enable the workflow.
- **Unverified sources:** the sibling-page slugs and the YouTube feed URL are unconfirmed. If a source can't be reached, you'll get a "Monitor problem" alert naming it. Edit the `SOURCES` list in `monitor.py` to fix or remove it.
- **Settings:** `PARANOID=0` (workflow env) silences invisible-edit alerts. `FAIL_ALERT_AFTER` changes the failure threshold.
- **Be polite:** don't schedule faster than every 5 minutes.
