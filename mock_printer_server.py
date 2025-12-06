#!/usr/bin/env python3
"""
Mock Thermal Printer Server for Testing

This server simulates a thermal printer by listening on port 9100 (standard ESC/POS port)
and logging all print jobs to the console. Perfect for testing without a physical printer!

Usage:
    python mock_printer_server.py

Then configure your app to use: http://YOUR_LOCAL_IP:9100
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
from datetime import datetime
import sys

class MockPrinterHandler(BaseHTTPRequestHandler):
    """Handler for mock print job requests"""
    
    def do_POST(self):
        """Handle POST requests (print jobs)"""
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b''
        
        # Log the print job with beautiful formatting
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        print("\n" + "="*70)
        print(f"🖨️  PRINT JOB RECEIVED @ {timestamp}")
        print("="*70)
        print(f"📍 Endpoint: {self.path}")
        print(f"📊 Content-Length: {content_length} bytes")
        
        # Print headers (excluding common ones)
        print("\n📋 Headers:")
        for header, value in self.headers.items():
            if header.lower() not in ['host', 'content-length', 'connection']:
                print(f"   {header}: {value}")
        
        # Print payload
        print("\n📄 Payload:")
        print("-" * 70)
        try:
            # Try to parse and pretty-print JSON
            data = json.loads(post_data)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            
            # If it's a KOT, print a summary
            if 'kot_no' in data or 'ticket_no' in data:
                print("\n📝 KOT Summary:")
                print(f"   KOT #: {data.get('kot_no') or data.get('ticket_no')}")
                if 'table' in data:
                    print(f"   Table: {data.get('table')}")
                if 'items' in data:
                    print(f"   Items: {len(data['items'])}")
                    for i, item in enumerate(data['items'], 1):
                        qty = item.get('qty', 1)
                        name = item.get('name', 'Unknown')
                        print(f"      {i}. {qty}x {name}")
        except json.JSONDecodeError:
            # Not JSON, print raw
            try:
                text = post_data.decode('utf-8', errors='replace')
                print(text)
            except:
                print(f"<Binary data: {len(post_data)} bytes>")
                print(post_data[:100].hex())  # Print first 100 bytes as hex
        
        print("-" * 70)
        print("="*70 + "\n")
        
        # Send success response
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        response = {
            "status": "success",
            "message": "Print job received by mock server",
            "timestamp": timestamp
        }
        self.wfile.write(json.dumps(response).encode('utf-8'))
    
    def do_GET(self):
        """Handle GET requests (status check)"""
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        response = {
            "status": "online",
            "type": "mock_printer",
            "version": "1.0",
            "port": PORT
        }
        self.wfile.write(json.dumps(response).encode('utf-8'))
    
    def do_OPTIONS(self):
        """Handle OPTIONS requests (CORS preflight)"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.end_headers()
    
    def log_message(self, format, *args):
        """Suppress default HTTP logging (we do our own)"""
        pass


def get_local_ip():
    """Get the local IP address"""
    import socket
    try:
        # Create a socket to get local IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except:
        return "localhost"


if __name__ == '__main__':
    PORT = 9100  # Standard thermal printer port (ESC/POS)
    
    try:
        server = HTTPServer(('0.0.0.0', PORT), MockPrinterHandler)
        local_ip = get_local_ip()
        
        print("\n" + "="*70)
        print("🖨️  Mock Thermal Printer Server")
        print("="*70)
        print(f"✅ Server running on port {PORT}")
        print(f"📍 Local URL: http://{local_ip}:{PORT}")
        print(f"📍 Localhost: http://localhost:{PORT}")
        print("\n💡 Configure your app to use one of these URLs")
        print("⏸️  Press Ctrl+C to stop")
        print("="*70 + "\n")
        print("Waiting for print jobs...\n")
        
        server.serve_forever()
        
    except KeyboardInterrupt:
        print("\n\n👋 Server stopped by user")
        sys.exit(0)
    except OSError as e:
        if e.errno == 48 or e.errno == 98:  # Address already in use
            print(f"\n❌ Error: Port {PORT} is already in use!")
            print(f"💡 Either:")
            print(f"   1. Stop the other service using port {PORT}")
            print(f"   2. Modify this script to use a different port")
            sys.exit(1)
        else:
            raise