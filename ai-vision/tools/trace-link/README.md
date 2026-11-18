# Trace Link Tools

Utility sample for generating AI Vision trace and observation deep links.

Files:

- `gen_trace_link.py`: simple Python sample script with constants edited in file.

Usage:

```bash
python3 tools/trace-link/gen_trace_link.py
```

Generate an iframe-friendly trace link:

```bash
python3 tools/trace-link/gen_trace_link.py --embed
```

Generate an observation link:

```bash
python3 tools/trace-link/gen_trace_link.py --kind observation --observation-id <observation-id>
```

Generate an iframe-friendly observation link:

```bash
python3 tools/trace-link/gen_trace_link.py --kind observation --observation-id <observation-id> --embed
```
