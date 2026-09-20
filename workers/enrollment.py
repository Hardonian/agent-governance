"""HX370 Node Enrollment Endpoint.
Validates pre-shared key and registers authenticated nodes.
Only accessible on localhost/Tailscale - not exposed publicly.
"""
import json
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from db import execute, fetchone, fetchall

# Pre-shared key for enrollment (must match HX370 config)
ENROLLMENT_KEY = "agent-gov-enroll-2026"  # Rotate in production
LISTEN_PORT = 9200


class EnrollmentHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/api/nodes/enroll":
            self._respond(404, {"error": "not found"})
            return
        
        # Read body
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._respond(400, {"error": "empty body"})
            return
        
        try:
            body = json.loads(self.rfile.read(content_length))
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid JSON"})
            return
        
        # Validate auth
        token = body.get("authentication_token", "")
        if token != ENROLLMENT_KEY:
            self._respond(401, {"error": "invalid authentication token"})
            return
        
        # Extract node info
        node_id = body.get("node_id", str(uuid.uuid4()))
        hostname = body.get("hostname", "unknown")
        node_type = body.get("node_type", "unknown")
        capabilities = body.get("capabilities", {})
        
        # Register worker
        try:
            execute("""
                INSERT INTO workers (worker_id, node, capabilities, status, last_heartbeat, created_at)
                VALUES (:wid, :node, :caps, 'idle', NOW(), NOW())
                ON CONFLICT (worker_id) DO UPDATE SET
                    node = EXCLUDED.node,
                    capabilities = EXCLUDED.capabilities,
                    status = 'idle',
                    last_heartbeat = NOW()
            """, {
                "wid": node_id,
                "node": hostname,
                "caps": json.dumps(capabilities),
            })
        except Exception as e:
            self._respond(500, {"error": f"registration failed: {str(e)}"})
            return
        
        self._respond(200, {
            "status": "enrolled",
            "node_id": node_id,
            "message": f"Node {hostname} enrolled successfully",
        })
    
    def do_GET(self):
        if self.path == "/api/nodes/health":
            workers = fetchall("SELECT worker_id, node, status, last_heartbeat FROM workers")
            self._respond(200, {"workers": workers})
        elif self.path == "/health":
            self._respond(200, {"status": "ok"})
        else:
            self._respond(404, {"error": "not found"})
    
    def _respond(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body, default=str).encode())
    
    def log_message(self, format, *args):
        print(f"[enrollment] {args[0]}")


def start_server():
    server = HTTPServer(("127.0.0.1", LISTEN_PORT), EnrollmentHandler)
    print(f"Enrollment endpoint listening on 127.0.0.1:{LISTEN_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    start_server()
