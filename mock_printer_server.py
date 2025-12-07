#!/usr/bin/env python3
"""
Mock Thermal Printer Server for Testing

This server simulates a thermal printer by listening on port 8000 (standard ESC/POS port)
and logging all print jobs to the console. Perfect for testing without a physical printer!

Usage:
    python mock_printer_server.py           # uses default port 8000
    python mock_printer_server.py 9200      # custom port

Then configure your app to use: http://YOUR_LOCAL_IP:PORT
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
from datetime import datetime
import sys
import socket


class MockPrinterHandler(BaseHTTPRequestHandler):
    """Handler for mock print job requests"""

    def _current_port(self) -> int:
        """Get the actual port the server is bound to (no global needed)."""
        # server_address = (host, port)
        return self.server.server_address[1]

    def do_POST(self):
        """Handle POST requests (print jobs)"""
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b""

        # Log the print job with beautiful formatting
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print("\n" + "=" * 70)
        print(f"🖨️  PRINT JOB RECEIVED @ {timestamp}")
        print("=" * 70)
        print(f"📍 Endpoint: {self.path}")
        print(f"🌐 From: {self.client_address[0]}:{self.client_address[1]}")
        print(f"📊 Content-Length: {content_length} bytes")

        # Print headers (excluding common ones)
        print("\n📋 Headers:")
        for header, value in self.headers.items():
            if header.lower() not in ["host", "content-length", "connection"]:
                print(f"   {header}: {value}")

        # Print payload
        print("\n📄 Payload:")
        print("-" * 70)
        try:
            # Try to parse and pretty-print JSON (bytes are allowed in json.loads)
            data = json.loads(post_data)
            print(json.dumps(data, indent=2, ensure_ascii=False))

            # If it's a KOT, print a summary
            if isinstance(data, dict) and ("kot_no" in data or "ticket_no" in data):
                print("\n📝 KOT Summary:")
                print(f"   KOT #: {data.get('kot_no') or data.get('ticket_no')}")
                if "table" in data:
                    print(f"   Table: {data.get('table')}")
                if "items" in data and isinstance(data["items"], list):
                    print(f"   Items: {len(data['items'])}")
                    for i, item in enumerate(data["items"], 1):
                        qty = item.get("qty", 1)
                        name = item.get("name", "Unknown")
                        print(f"      {i}. {qty}x {name}")
        except json.JSONDecodeError:
            # Not JSON, print raw
            try:
                text = post_data.decode("utf-8", errors="replace")
                print(text)
            except Exception:
                print(f"<Binary data: {len(post_data)} bytes>")
                print(post_data[:100].hex())  # Print first 100 bytes as hex

        print("-" * 70)
        print("=" * 70 + "\n")

        # Send success response
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        response = {
            "status": "success",
            "message": "Print job received by mock server",
            "timestamp": timestamp,
        }
        self.wfile.write(json.dumps(response).encode("utf-8"))

    def do_GET(self):
        """Handle GET requests (status check)"""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        response = {
            "status": "online",
            "type": "mock_printer",
            "version": "1.0",
            "port": self._current_port(),  # ✅ no global PORT dependency
        }
        self.wfile.write(json.dumps(response).encode("utf-8"))

    def do_OPTIONS(self):
        """Handle OPTIONS requests (CORS preflight)"""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        # You can also restrict headers if you want, e.g. "Content-Type"
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def log_message(self, format, *args):
        """Suppress default HTTP logging (we do our own)"""
        pass


def get_local_ip():
    """Get the local IP address"""
    try:
        # Create a socket to get local IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # We don't actually send data to 8.8.8.8; just used to pick an interface
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        return "localhost"


def main():
    # Default port 8000, allow override via CLI
    default_port = 8000
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print(f"⚠️  Invalid port {sys.argv[1]!r}, falling back to {default_port}")
            port = default_port
    else:
        port = default_port

    try:
        server = HTTPServer(("0.0.0.0", port), MockPrinterHandler)
        local_ip = get_local_ip()

        print("\n" + "=" * 70)
        print("🖨️  Mock Thermal Printer Server")
        print("=" * 70)
        print(f"✅ Server running on port {port}")
        print(f"📍 Local URL:    http://{local_ip}:{port}")
        print(f"📍 Localhost:   http://localhost:{port}")
        print("\n💡 Configure your app to use one of these URLs")
        print("⏸️  Press Ctrl+C to stop")
        print("=" * 70 + "\n")
        print("Waiting for print jobs...\n")

        server.serve_forever()

    except KeyboardInterrupt:
        print("\n\n👋 Server stopped by user")
        sys.exit(0)
    except OSError as e:
        # Cross-platform-ish handling for "address already in use"
        # Linux/Mac often use errno 98/48, Windows uses 10048
        if e.errno in (48, 98, 10048):
            print(f"\n❌ Error: Port {port} is already in use!")
            print("💡 Either:")
            print(f"   1. Stop the other service using port {port}")
            print(f"   2. Run: python {sys.argv[0]} <other_port>")
            sys.exit(1)
        else:
            raise


if __name__ == "__main__":
    main()
