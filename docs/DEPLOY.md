# Deploying the saturn viewer on dr.eamer.dev

The viewer ships as an optional extra (`pip install 'saturn-dissect[web]'`). Local use needs nothing beyond `saturn serve`. Server deployment is wired through `sm` and Caddy.

## Local dev

```bash
pip install -e '.[web]'
saturn serve --dir ./findings --port 5043
open http://127.0.0.1:5043
```

## Server deployment (dr.eamer.dev)

### 1. `sm` registration (already live)

`~/service_manager.py` has a `saturn-viewer` entry at port 5043:

```python
'saturn-viewer': {
    'name': 'Saturn Viewer',
    'script': '/home/coolhand/projects/saturn/saturn/scripts/start.sh',
    'working_dir': '/home/coolhand/projects/saturn/saturn',
    'port': 5043,
    'health_endpoint': 'http://localhost:5043/health',
    'start_timeout': 15,
    'description': 'Live saturn findings viewer (read-only, WCAG 2.2 AA, reads /home/coolhand/saturn-findings)'
}
```

Manage with the usual verbs:

```bash
sm status                 # confirm Running
sm restart saturn-viewer
sm logs saturn-viewer
sm stop saturn-viewer
```

### 2. `scripts/start.sh` (already committed)

Activates the saturn venv, puts `~/shared` on `PYTHONPATH`, runs gunicorn against the Flask app factory:

```bash
exec gunicorn \
    --bind "${HOST}:${PORT}" \
    --workers "$WORKERS" --threads "$THREADS" \
    --access-logfile - --error-logfile - \
    "saturn.viewer.app:create_app(findings_dir='${FINDINGS_DIR}')"
```

Environment knobs: `SATURN_FINDINGS_DIR`, `SATURN_PORT`, `SATURN_HOST`, `SATURN_WORKERS`, `SATURN_THREADS`. Defaults: `/home/coolhand/saturn-findings`, `5043`, `127.0.0.1`, `2`, `4`.

### 3. Findings directory

`/home/coolhand/saturn-findings/` is the single source. Drop any `*.json` file saturn produces (profile or compare) into it and the index picks it up on the next request. The matching `*.html` file can live alongside the JSON but the viewer doesn't need it.

Data flow:

```
saturn analyze <source> \
    --out     ~/saturn-findings/<name>.html \
    --findings ~/saturn-findings/<name>.json
```

Then refresh the viewer in the browser. No restart needed. The index sorts newest-first by mtime.

### 4. Caddy route (needs `@geepers_caddy`)

Append to `/etc/caddy/Caddyfile` inside the `dr.eamer.dev { ... }` block. Use the path-stripped pattern (same as other React/Vite-style mounts like `/io/chat`), and send `X-Forwarded-Prefix` upstream so Flask's `url_for` can prepend it:

```caddyfile
handle_path /saturn/* {
    reverse_proxy localhost:5043 {
        header_up X-Forwarded-Prefix /saturn
    }
}
```

The `header_up` is load-bearing. Without it, the viewer's HTML links drop the `/saturn/` prefix and follow-ups 404. The app honors the header only when `SATURN_TRUST_FORWARDED_PREFIX=1` is set (scripts/start.sh sets it on the sm-managed deployment), so a direct-to-gunicorn caller cannot spoof a prefix.

Apply via `@geepers_caddy` (sole authority — do not hand-edit). Then:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
curl -s https://dr.eamer.dev/saturn/health
```

Expected: `{"findings_dir":"/home/coolhand/saturn-findings","status":"ok"}`.

## Security posture

- **Read-only.** No POST/PUT/DELETE routes. No auth intentionally — findings are treated like other public `~/html/` content.
- **Path-traversal guard.** `_safe_findings_path` in `saturn/viewer/app.py` resolves every `<id>` against the configured findings dir and 404s anything that escapes. Belt and suspenders; Flask's default string converter already forbids `/`.
- **No PII by design.** saturn's findings are aggregates (counts, rates, alerts, top values, language mix). Still: treat the findings dir like any public directory. Don't drop findings from a dataset you can't share. `top_values` on a free-text column can surface snippets of actual content.
- **Rate limiting.** Not configured in-app. If traffic ever matters, add Caddy's `rate_limit` plugin at the path.
- **Concurrent writes.** `saturn analyze` writes non-atomically; viewer can land on a half-written file. The loader's JSON decode catches this and the index hides the row until the next request. No corrupted state.

## Verification

```bash
sm status | grep saturn                         # expect Running + healthy
curl -s http://127.0.0.1:5043/health            # expect {"status":"ok",...}
curl -s http://127.0.0.1:5043/ | grep -c Find   # expect 1+ (Findings heading)
# after Caddy applied:
curl -s https://dr.eamer.dev/saturn/health
curl -sI https://dr.eamer.dev/saturn/ | head -1  # expect HTTP/2 200
```
