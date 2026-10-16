# Snip

Onion URL shortener over Tor. No accounts.

- Paste a long .onion URL, get a short link
- Click counter per link
- Only .onion URLs accepted

## Stack

Python stdlib only. SQLite (WAL). ThreadingHTTPServer.

## Run

```
python3 app.py  # 127.0.0.1:18096
```

Deploy as a Tor hidden service pointing at port 18096.
