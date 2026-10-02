"""
Core P2P networking layer.

Each peer is BOTH:
    - a TCP server: accepts incoming peer connections
    - a TCP client: connects to other peers

No central server is used.
"""

import os
import socket
import threading
import uuid
from typing import Callable, Optional

from protocol import send_json, recv_json, recv_exact


CHUNK_SIZE = 64 * 1024


class PeerNode:
    def __init__(
        self,
        peer_name: str,
        port: int,
        event_callback: Optional[Callable[[str], None]] = None,
        peer_callback: Optional[Callable[[], None]] = None,
        downloads_dir: str = "downloads",
    ):
        self.peer_name = peer_name.strip()
        self.port = int(port)
        self.peer_id = uuid.uuid4().hex[:8]

        self.event_callback = event_callback or (lambda message: None)
        self.peer_callback = peer_callback or (lambda: None)

        self.downloads_dir = downloads_dir
        os.makedirs(self.downloads_dir, exist_ok=True)

        self.server_socket: Optional[socket.socket] = None
        self.running = False

        # peer_id -> peer information
        self.peers = {}
        self.peers_lock = threading.Lock()

        self.state_lock = threading.Lock()

    def log(self, message: str) -> None:
        self.event_callback(message)

    def start(self) -> None:
        with self.state_lock:
            if self.running:
                raise RuntimeError("Peer is already running.")

            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("0.0.0.0", self.port))
            server.listen(20)
            server.settimeout(1.0)

            self.server_socket = server
            self.running = True

        thread = threading.Thread(
            target=self._accept_loop,
            name="accept-loop",
            daemon=True,
        )
        thread.start()

        self.log(
            f"[STARTED] {self.peer_name} | ID: {self.peer_id} | "
            f"Listening on 0.0.0.0:{self.port}"
        )

    def stop(self) -> None:
        with self.state_lock:
            if not self.running:
                return
            self.running = False

        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass
            self.server_socket = None

        with self.peers_lock:
            connections = [
                info["socket"] for info in self.peers.values()
            ]
            self.peers.clear()

        for sock in connections:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

        self.peer_callback()
        self.log("[STOPPED] Peer stopped.")

    def _accept_loop(self) -> None:
        while self.running:
            try:
                connection, address = self.server_socket.accept()
                connection.settimeout(None)

                thread = threading.Thread(
                    target=self._handle_connection,
                    args=(connection, address, False),
                    daemon=True,
                )
                thread.start()

            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as exc:
                self.log(f"[ERROR] Accept failed: {exc}")

    def connect_to_peer(self, ip: str, port: int) -> None:
        if not self.running:
            raise RuntimeError("Start your peer before connecting.")

        ip = ip.strip()
        port = int(port)

        if not ip:
            raise ValueError("IP address is required.")

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(8)

        try:
            sock.connect((ip, port))
            sock.settimeout(None)

            # We initiate the connection, so send our HELLO first.
            send_json(sock, self._hello_message())

            # The remote peer sends its HELLO in response.
            hello = recv_json(sock)
            self._register_peer(sock, hello, ip, port)

            thread = threading.Thread(
                target=self._receive_loop,
                args=(sock, hello.get("peer_id", "")),
                daemon=True,
            )
            thread.start()

            self.log(
                f"[CONNECTED] {hello.get('peer_name', 'Unknown')} "
                f"({hello.get('peer_id', '?')}) at {ip}:{port}"
            )

        except Exception:
            try:
                sock.close()
            except OSError:
                pass
            raise

    def _handle_connection(
        self,
        sock: socket.socket,
        address,
        initiated_by_us: bool,
    ) -> None:
        remote_ip, remote_port = address[0], address[1]

        try:
            # Incoming connection: receive remote HELLO first.
            hello = recv_json(sock)

            # Reply with our HELLO acknowledgement.
            send_json(sock, self._hello_message())

            peer_id = hello.get("peer_id")
            if not peer_id:
                raise ValueError("HELLO message has no peer_id.")

            self._register_peer(sock, hello, remote_ip, remote_port)

            self.log(
                f"[CONNECTED] {hello.get('peer_name', 'Unknown')} "
                f"({peer_id}) at {remote_ip}:{remote_port}"
            )

            self._receive_loop(sock, peer_id)

        except Exception as exc:
            self.log(f"[ERROR] Incoming connection failed: {exc}")
            try:
                sock.close()
            except OSError:
                pass

    def _receive_loop(self, sock: socket.socket, peer_id: str) -> None:
        try:
            while self.running:
                message = recv_json(sock)
                message_type = message.get("type")

                if message_type == "text":
                    self._handle_text(message)

                elif message_type == "file":
                    self._handle_file(sock, peer_id, message)

                elif message_type == "hello":
                    # Normally handled during connection setup.
                    send_json(sock, self._hello_message())

                elif message_type == "hello_ack":
                    pass

                else:
                    self.log(
                        f"[WARNING] Unknown message type: {message_type}"
                    )

        except ConnectionError:
            self.log(f"[DISCONNECTED] {self._peer_display(peer_id)}")
        except (OSError, ValueError) as exc:
            self.log(
                f"[DISCONNECTED] {self._peer_display(peer_id)}: {exc}"
            )
        except Exception as exc:
            self.log(f"[ERROR] Receive loop: {exc}")
        finally:
            self._remove_peer(peer_id, sock)

    def _handle_text(self, message: dict) -> None:
        sender_name = message.get("sender_name", "Unknown")
        text = message.get("message", "")
        self.log(f"[MESSAGE] {sender_name}: {text}")

    def _handle_file(
        self,
        sock: socket.socket,
        peer_id: str,
        message: dict,
    ) -> None:
        filename = os.path.basename(str(message.get("filename", "")))
        filesize = message.get("filesize")

        if not filename:
            raise ValueError("Received file has no filename.")

        if not isinstance(filesize, int) or filesize < 0:
            raise ValueError("Received file has an invalid size.")

        safe_name = self._unique_download_name(filename)
        destination = os.path.join(self.downloads_dir, safe_name)

        remaining = filesize
        try:
            with open(destination, "wb") as file:
                while remaining > 0:
                    chunk = sock.recv(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise ConnectionError(
                            "Peer disconnected before file completed."
                        )
                    file.write(chunk)
                    remaining -= len(chunk)

        except Exception:
            try:
                os.remove(destination)
            except OSError:
                pass
            raise

        sender_name = self._peer_name(peer_id)
        self.log(
            f"[FILE RECEIVED] {sender_name}: {safe_name} "
            f"({filesize:,} bytes) -> downloads/"
        )

    def send_text(self, peer_id: str, message: str) -> None:
        message = message.strip()
        if not message:
            raise ValueError("Message cannot be empty.")

        info = self._get_peer(peer_id)

        data = {
            "type": "text",
            "sender_id": self.peer_id,
            "sender_name": self.peer_name,
            "message": message,
        }

        try:
            send_json(info["socket"], data)
            self.log(f"[YOU -> {info['name']}] {message}")
        except Exception as exc:
            self._remove_peer(peer_id, info["socket"])
            raise ConnectionError(
                f"Could not send message: {exc}"
            ) from exc

    def send_file(self, peer_id: str, filepath: str) -> None:
        if not os.path.isfile(filepath):
            raise FileNotFoundError(filepath)

        filesize = os.path.getsize(filepath)
        filename = os.path.basename(filepath)
        info = self._get_peer(peer_id)

        metadata = {
            "type": "file",
            "sender_id": self.peer_id,
            "sender_name": self.peer_name,
            "filename": filename,
            "filesize": filesize,
        }

        try:
            send_json(info["socket"], metadata)

            with open(filepath, "rb") as file:
                while True:
                    chunk = file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    info["socket"].sendall(chunk)

            self.log(
                f"[FILE SENT] {filename} ({filesize:,} bytes) "
                f"-> {info['name']}"
            )

        except Exception as exc:
            self._remove_peer(peer_id, info["socket"])
            raise ConnectionError(
                f"Could not send file: {exc}"
            ) from exc

    def _hello_message(self) -> dict:
        return {
            "type": "hello",
            "peer_id": self.peer_id,
            "peer_name": self.peer_name,
            "port": self.port,
        }

    def _register_peer(
        self,
        sock: socket.socket,
        hello: dict,
        ip: str,
        port: int,
    ) -> None:
        peer_id = str(hello.get("peer_id", ""))
        peer_name = str(hello.get("peer_name", "Unknown"))

        if not peer_id:
            raise ValueError("Peer ID is missing.")

        # Avoid registering ourselves.
        if peer_id == self.peer_id:
            sock.close()
            raise ValueError("Attempted to connect to this peer itself.")

        with self.peers_lock:
            # If the same peer reconnects, replace the old connection.
            old = self.peers.get(peer_id)
            self.peers[peer_id] = {
                "socket": sock,
                "name": peer_name,
                "ip": ip,
                "port": port,
            }

        if old and old["socket"] is not sock:
            try:
                old["socket"].close()
            except OSError:
                pass

        self.peer_callback()

    def _remove_peer(self, peer_id: str, sock: socket.socket) -> None:
        with self.peers_lock:
            current = self.peers.get(peer_id)
            if current and current["socket"] is sock:
                del self.peers[peer_id]
                removed = True
            else:
                removed = False

        try:
            sock.close()
        except OSError:
            pass

        if removed:
            self.peer_callback()

    def _get_peer(self, peer_id: str) -> dict:
        with self.peers_lock:
            info = self.peers.get(peer_id)

        if not info:
            raise ValueError("Selected peer is no longer connected.")

        return info

    def get_peers(self) -> list:
        with self.peers_lock:
            return [
                {
                    "id": peer_id,
                    "name": info["name"],
                    "ip": info["ip"],
                    "port": info["port"],
                }
                for peer_id, info in self.peers.items()
            ]

    def _peer_name(self, peer_id: str) -> str:
        with self.peers_lock:
            info = self.peers.get(peer_id)
            return info["name"] if info else peer_id

    def _peer_display(self, peer_id: str) -> str:
        with self.peers_lock:
            info = self.peers.get(peer_id)
            if info:
                return f"{info['name']} [{peer_id}]"
        return peer_id

    def _unique_download_name(self, filename: str) -> str:
        base, extension = os.path.splitext(filename)
        candidate = filename
        counter = 1

        while os.path.exists(os.path.join(self.downloads_dir, candidate)):
            candidate = f"{base}_{counter}{extension}"
            counter += 1

        return candidate
