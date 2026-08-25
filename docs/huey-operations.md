# Huey Task Queue — Operations Guide

## Overview

Riptide uses [Huey](https://github.com/coleifer/huey) with a SQLite backend for background task processing. This replaces the previous thread-spawning model from the webhook handler.

## Architecture

```
GitHub Webhook → FastAPI (/webhook/github) → Huey enqueue → 200 OK
                                                          ↓
                                              Huey Consumer (separate process)
                                                          ↓
                                              ├─ process_pull_request_task
                                              ├─ process_issue_comment_task
                                              ├─ poll_deepthink_task (15min cron)
                                              └─ poll_proofshotter_task (10min cron)
```

## Quick Start

```bash
# Terminal 1: webhook server
python server.py

# Terminal 2: Huey consumer (background task processor)
python server.py --huey
```

## Production Deployment

### Option A: systemd (recommended)

Create `/etc/systemd/system/riptide-webhook.service`:

```ini
[Unit]
Description=Riptide Webhook Server
After=network.target

[Service]
Type=simple
User=sc
WorkingDirectory=/home/sc/workspace/riptide
ExecStart=/usr/bin/python3 server.py --prod
Restart=always
RestartSec=5
Environment=GITHUB_WEBHOOK_SECRET=your_secret
Environment=GITHUB_PRIVATE_KEY_PATH=/path/to/key.pem

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/riptide-huey.service`:

```ini
[Unit]
Description=Riptide Huey Consumer
After=network.target riptide-webhook.service
Requires=riptide-webhook.service

[Service]
Type=simple
User=sc
WorkingDirectory=/home/sc/workspace/riptide
ExecStart=/usr/bin/python3 server.py --huey
Restart=always
RestartSec=5
Environment=GITHUB_WEBHOOK_SECRET=your_secret
Environment=GITHUB_PRIVATE_KEY_PATH=/path/to/key.pem

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now riptide-webhook riptide-huey
sudo systemctl status riptide-webhook riptide-huey
```

### Option B: supervisord

Add to `/etc/supervisor/conf.d/riptide.conf`:

```ini
[program:riptide-webhook]
command=python3 server.py --prod
directory=/home/sc/workspace/riptide
user=sc
autostart=true
autorestart=true
stderr_logfile=/var/log/riptide-webhook.err.log
stdout_logfile=/var/log/riptide-webhook.out.log

[program:riptide-huey]
command=python3 server.py --huey
directory=/home/sc/workspace/riptide
user=sc
autostart=true
autorestart=true
stderr_logfile=/var/log/riptide-huey.err.log
stdout_logfile=/var/log/riptide-huey.out.log
```

## Monitoring

### Check consumer health

```bash
# View Huey task registry
python3 -c "
from riptide.huey_config import huey
from huey.storage import SqliteStorage
s = huey.storage
conn = s.conn
print('Pending tasks:', conn.execute('SELECT COUNT(*) FROM task_queue WHERE status=\"pending\"').fetchone()[0])
print('Recently executed:', conn.execute('SELECT COUNT(*) FROM task_history WHERE status=\"success\"').fetchone()[0])
print('Failed tasks:', conn.execute('SELECT COUNT(*) FROM task_history WHERE status=\"failed\"').fetchone()[0])
"
```

### Check delivery state machine

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('/home/sc/.local/share/riptide/state.db')
for row in conn.execute('SELECT status, COUNT(*) FROM deliveries GROUP BY status').fetchall():
    print(f'{row[0]}: {row[1]}')
"
```

### Key metrics to alert on

| Metric | Warning | Critical |
|--------|---------|----------|
| `processing` deliveries > 5min old | > 0 | > 5 |
| Huey `pending` task count | > 20 | > 50 |
| Huey `failed` task count | > 0 | > 5 |

## Troubleshooting

### Stuck processing deliveries

```bash
python3 -c "
import sqlite3, time
conn = sqlite3.connect('/home/sc/.local/share/riptide/state.db')
stale = conn.execute('SELECT delivery_id, received_at FROM deliveries WHERE status=\"processing\" AND received_at < ?', (time.time() - 300,)).fetchall()
print(f'Stale processing deliveries: {len(stale)}')
for d in stale:
    print(f'  {d[0][:20]}... {time.ctime(d[1])}')
"
```

### Huey consumer not processing

1. Check consumer is running: `systemctl status riptide-huey`
2. Check logs: `journalctl -u riptide-huey -f`
3. Check SQLite lock: `lsof ~/.local/share/riptide/huey.db`

### Task retry behavior

- Webhook tasks: 3 retries, 10s delay between retries
- Periodic tasks: run on schedule, no retry (next run picks up)
- Failed tasks logged to `task_history` table in `huey.db`

## Migration from thread-spawning model

| Before | After |
|--------|-------|
| `threading.Thread(target=_safe_run).start()` | `process_pull_request_task(args)` |
| Thread crash = silent failure | Task failure = logged + retried |
| No task state tracking | Full state machine in `deliveries` table |
| No periodic task management | Huey `@periodic_task` decorator |
| Manual process management | systemd/supervisord with auto-restart |

## Files

| File | Purpose |
|------|---------|
| `riptide/huey_config.py` | Huey instance with SQLite backend |
| `riptide/tasks.py` | Plain task functions (testable, reusable) |
| `riptide/huey_tasks.py` | Huey task wrappers + periodic tasks |
| `server.py` | Entry point (`--huey` flag for consumer) |
| `riptide/state.py` | Delivery state machine (schema v7) |
