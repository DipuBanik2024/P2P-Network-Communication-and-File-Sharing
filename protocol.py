"""
Application-level protocol for the P2P Network assignment.

TCP framing:
    [4-byte unsigned big-endian JSON length][JSON payload]

For file transfer:
    1. Send a framed JSON "file" message containing filename/filesize.
    2. Send exactly filesize raw bytes.
"""

import json
import struct
import socket

HEADER_SIZE = 4
MAX_JSON_SIZE = 1024 * 1024  # 1 MB


def send_json(sock: socket.socket, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_JSON_SIZE:
        raise ValueError("JSON message is too large.")
    header = struct.pack("!I", len(payload))
    sock.sendall(header + payload)


def recv_exact(sock: socket.socket, size: int) -> bytes:
    """Receive exactly size bytes, or raise ConnectionError."""
    chunks = []
    remaining = size

    while remaining:
        chunk = sock.recv(min(64 * 1024, remaining))
        if not chunk:
            raise ConnectionError("Peer disconnected during data transfer.")
        chunks.append(chunk)
        remaining -= len(chunk)

    return b"".join(chunks)


def recv_json(sock: socket.socket) -> dict:
    header = recv_exact(sock, HEADER_SIZE)
    payload_size = struct.unpack("!I", header)[0]

    if payload_size <= 0 or payload_size > MAX_JSON_SIZE:
        raise ValueError(f"Invalid JSON payload size: {payload_size}")

    payload = recv_exact(sock, payload_size)

    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid JSON message received.") from exc

    if not isinstance(data, dict):
        raise ValueError("Protocol message must be a JSON object.")

    return data
