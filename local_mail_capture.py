"""Capture MEATTRACK demo login emails within the local Docker network."""

import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        recipient = payload.get("to", [{}])[0].get("email", "unknown")
        code = re.search(r"\b\d{6}\b", payload.get("textContent", ""))
        print(f"LOCAL LOGIN OTP to={recipient} code={code.group(0) if code else 'missing'}", flush=True)
        body = b'{"messageId":"local-demo"}'
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


HTTPServer(("0.0.0.0", 8025), Handler).serve_forever()
