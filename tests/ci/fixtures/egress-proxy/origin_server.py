from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socketserver
import threading


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class ConnectorHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.sendall(b"connector-ready")


if __name__ == "__main__":
    for port in (22, 5432):
        server = socketserver.ThreadingTCPServer(("0.0.0.0", port), ConnectorHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 80), Handler).serve_forever()
