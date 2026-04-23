# Deploying the Saturn viewer on dr.eamer.dev

The viewer ships as an optional extra (`pip install 'saturn-dissect[web]'`). Local use needs nothing beyond `saturn serve`. Server deployment wires it into `sm` and Caddy.

## Local dev

```bash
pip install -e '.[web]'
saturn serve --dir ./findings --port 5043
open http://127.0.0.1:5043
```

## Server deployment

### 1. `sm` registration

Add an entry to `~/service_manager.py` `SERVICES` dict:

```python
'saturn-viewer': {
    'name': 'Saturn Viewer',
    'script': '/home/coolhand/projects/saturn/saturn/scripts/start.sh',
    'working_dir': '/home/coolhand/projects/saturn/saturn',
    'port': 5043,
    'health_endpoint': 'http://localhost:5043/health',
    'start_timeout': 15,
    'description': 'Live saturn findings viewer (WCAG 2.2 AA)',
}
```

### 2. `scripts/start.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
source venv/bin/activate
export PYTHONPATH=/home/coolhand/shared:$PYTHONPATH
exec gunicorn \
    --bind 127.0.0.1:5043 \
    --workers 2 --threads 4 \
    'saturn.viewer.app:create_app(findings_dir="/home/coolhand/saturn-findings")'
```

`chmod +x scripts/start.sh`, then `pip install gunicorn` inside the venv.

### 3. Caddy

Path-stripped reverse proxy (the viewer expects to be mounted at root of whatever prefix Caddy hands it):

```caddyfile
handle_path /saturn/* { reverse_proxy localhost:5043 }
```

The viewer uses `url_for` for every internal link, so path-strip works without further config.

### 4. Verify

```bash
sm start saturn-viewer
sm logs saturn-viewer
curl -s https://dr.eamer.dev/saturn/health
```

### Notes

- Do not edit `~/service_manager.py` or `/etc/caddy/Caddyfile` by hand — coordinate via `@geepers_orchestrator_deploy` and `@geepers_caddy`.
- The viewer is read-only. It reads JSON findings files; it does not run saturn.
- To update findings, re-run `saturn analyze …` and drop the new JSON into the findings directory — the index picks it up on the next request (no restart).
