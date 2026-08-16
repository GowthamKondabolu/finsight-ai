# Load smoke test

The bounded load smoke checks operations-route availability and latency without invoking a model or claiming production performance.

```bash
python tests/load/load_smoke.py \
  --base-url http://127.0.0.1:8000 \
  --path /health \
  --requests 200 \
  --concurrency 20 \
  --max-p95-ms 500
```

Use `/ready` to include database connectivity. For an authenticated `/v1` route, load `FINSIGHT_API_AUTH_TOKEN` from the ignored deployment environment; the script never prints it. Do not point this tool at systems you do not own or operate.
