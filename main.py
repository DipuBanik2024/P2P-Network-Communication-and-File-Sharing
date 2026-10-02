"""
GUI for the P2P Network assignment.

Uses only Python standard-library modules (Tkinter + sockets).
"""

import os
import queue
import socket
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from p2p_node import PeerNode


class P2PApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("P2P Network - Communication & File Sharing")
        self.geometry("1000x720")
        self.minsize(900, 650)

        self.node = None
        self.event_queue = queue.Queue()

        self.name_var = tk.StringVar(value="Peer")
        self.port_var = tk.StringVar(value="5000")
        self.remote_ip_var = tk.StringVar(value="127.0.0.1")
        self.remote_port_var = tk.StringVar(value="5001")
        self.message_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Not started")

        self._build_ui()
        self.after(100, self._process_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        title = ttk.Label(
            self,
            text="Peer-to-Peer Network",
            font=("Segoe UI", 20, "bold"),
        )
        title.pack(pady=(12, 4))

        subtitle = ttk.Label(
            self,
            text="TCP • Direct Peer Communication • Text & Binary File Transfer",
        )
        subtitle.pack(pady=(0, 12))

        # Local peer
        local_frame = ttk.LabelFrame(self, text="My Peer", padding=10)
        local_frame.pack(fill="x", padx=15, pady=6)

        ttk.Label(local_frame, text="Peer Name:").grid(
            row=0, column=0, sticky="w", padx=5, pady=5
        )
        ttk.Entry(
            local_frame, textvariable=self.name_var, width=20
        ).grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(local_frame, text="Listening Port:").grid(
            row=0, column=2, sticky="w", padx=5, pady=5
        )
        ttk.Entry(
            local_frame, textvariable=self.port_var, width=10
        ).grid(row=0, column=3, padx=5, pady=5)

        self.start_button = ttk.Button(
            local_frame,
            text="Start Peer",
            command=self.start_peer,
        )
        self.start_button.grid(row=0, column=4, padx=8)

        self.stop_button = ttk.Button(
            local_frame,
            text="Stop",
            command=self.stop_peer,
            state="disabled",
        )
        self.stop_button.grid(row=0, column=5, padx=5)

        ttk.Label(
            local_frame,
            textvariable=self.status_var,
        ).grid(row=1, column=0, columnspan=6, sticky="w", padx=5, pady=5)

        # Connect
        connect_frame = ttk.LabelFrame(
            self, text="Connect to Another Peer", padding=10
        )
        connect_frame.pack(fill="x", padx=15, pady=6)

        ttk.Label(connect_frame, text="Remote IP:").grid(
            row=0, column=0, padx=5, pady=5
        )
        ttk.Entry(
            connect_frame, textvariable=self.remote_ip_var, width=20
        ).grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(connect_frame, text="Remote Port:").grid(
            row=0, column=2, padx=5, pady=5
        )
        ttk.Entry(
            connect_frame, textvariable=self.remote_port_var, width=10
        ).grid(row=0, column=3, padx=5, pady=5)

        self.connect_button = ttk.Button(
            connect_frame,
            text="Connect",
            command=self.connect_peer,
            state="disabled",
        )
        self.connect_button.grid(row=0, column=4, padx=8)

        # Main area
        main_frame = ttk.Frame(self)
        main_frame.pack(fill="both", expand=True, padx=15, pady=6)

        peers_frame = ttk.LabelFrame(
            main_frame, text="Connected Peers", padding=8
        )
        peers_frame.pack(side="left", fill="y", padx=(0, 8))

        self.peer_list = tk.Listbox(
            peers_frame,
            width=36,
            height=20,
            exportselection=False,
            font=("Consolas", 10),
        )
        self.peer_list.pack(fill="both", expand=True)

        ttk.Label(
            peers_frame,
            text="Select a peer before sending.",
        ).pack(pady=(8, 0))

        log_frame = ttk.LabelFrame(
            main_frame, text="Messages / Events", padding=8
        )
        log_frame.pack(side="left", fill="both", expand=True)

        self.log_text = tk.Text(
            log_frame,
            wrap="word",
            state="disabled",
            font=("Consolas", 10),
        )
        self.log_text.pack(side="left", fill="both", expand=True)

        log_scroll = ttk.Scrollbar(
            log_frame,
            orient="vertical",
            command=self.log_text.yview,
        )
        log_scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=log_scroll.set)

        # Send text
        message_frame = ttk.LabelFrame(
            self, text="Send Text", padding=10
        )
        message_frame.pack(fill="x", padx=15, pady=6)

        message_entry = ttk.Entry(
            message_frame,
            textvariable=self.message_var,
        )
        message_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        message_entry.bind("<Return>", lambda event: self.send_text())

        self.send_button = ttk.Button(
            message_frame,
            text="Send",
            command=self.send_text,
            state="disabled",
        )
        self.send_button.pack(side="right")

        # File transfer
        file_frame = ttk.Frame(self)
        file_frame.pack(fill="x", padx=15, pady=(0, 12))

        self.file_button = ttk.Button(
            file_frame,
            text="Choose File & Send",
            command=self.send_file,
            state="disabled",
        )
        self.file_button.pack(side="left")

        ttk.Label(
            file_frame,
            text="Received files are saved in the downloads/ folder.",
        ).pack(side="left", padx=12)

    # ---------- UI helpers ----------

    def log(self, message: str):
        self.event_queue.put(("log", message))

    def _process_events(self):
        try:
            while True:
                event_type, data = self.event_queue.get_nowait()

                if event_type == "log":
                    self._append_log(data)

                elif event_type == "peers":
                    self._refresh_peer_list()

        except queue.Empty:
            pass

        self.after(100, self._process_events)

    def _append_log(self, message: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _peer_callback(self):
        self.event_queue.put(("peers", None))

    def _refresh_peer_list(self):
        if not self.node:
            return

        current_selection = self.peer_list.curselection()
        selected_id = None

        if current_selection:
            selected_id = self.peer_list.get(current_selection[0]).split("|")[0].strip()

        self.peer_list.delete(0, "end")

        for peer in sorted(
            self.node.get_peers(),
            key=lambda item: item["name"].lower(),
        ):
            display = (
                f"{peer['id']} | {peer['name']} | "
                f"{peer['ip']}:{peer['port']}"
            )
            self.peer_list.insert("end", display)

        if selected_id:
            for index in range(self.peer_list.size()):
                if self.peer_list.get(index).startswith(selected_id + " |"):
                    self.peer_list.selection_set(index)
                    break

    def _selected_peer_id(self):
        selection = self.peer_list.curselection()

        if not selection:
            raise ValueError("Please select a connected peer.")

        line = self.peer_list.get(selection[0])
        return line.split("|")[0].strip()

    # ---------- Peer operations ----------

    def start_peer(self):
        try:
            name = self.name_var.get().strip()
            port = int(self.port_var.get())

            if not name:
                raise ValueError("Peer name is required.")

            if not (1 <= port <= 65535):
                raise ValueError("Port must be between 1 and 65535.")

            self.node = PeerNode(
                name,
                port,
                event_callback=self.log,
                peer_callback=self._peer_callback,
                downloads_dir="downloads",
            )
            self.node.start()

            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")
            self.connect_button.configure(state="normal")
            self.send_button.configure(state="normal")
            self.file_button.configure(state="normal")

            self.status_var.set(
                f"Running as {name} | port {port} | ID {self.node.peer_id}"
            )

        except ValueError as exc:
            messagebox.showerror("Invalid Configuration", str(exc))
        except OSError as exc:
            messagebox.showerror(
                "Could Not Start Peer",
                f"Could not bind to the selected port.\n\n{exc}",
            )
            self.node = None
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            self.node = None

    def stop_peer(self):
        if self.node:
            self.node.stop()

        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.connect_button.configure(state="disabled")
        self.send_button.configure(state="disabled")
        self.file_button.configure(state="disabled")

        self.peer_list.delete(0, "end")
        self.status_var.set("Not started")

    def connect_peer(self):
        if not self.node:
            return

        ip = self.remote_ip_var.get().strip()

        try:
            port = int(self.remote_port_var.get())
            if not (1 <= port <= 65535):
                raise ValueError("Port must be between 1 and 65535.")
        except ValueError as exc:
            messagebox.showerror("Invalid Port", str(exc))
            return

        def worker():
            try:
                self.node.connect_to_peer(ip, port)
                self.event_queue.put(("peers", None))
            except ValueError as exc:
                self.log(f"[ERROR] {exc}")
            except (OSError, ConnectionError) as exc:
                self.log(f"[ERROR] Connection failed: {exc}")
            except Exception as exc:
                self.log(f"[ERROR] Connection failed: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def send_text(self):
        if not self.node:
            return

        message = self.message_var.get().strip()

        try:
            peer_id = self._selected_peer_id()
            self.node.send_text(peer_id, message)
            self.message_var.set("")
        except Exception as exc:
            messagebox.showerror("Send Message", str(exc))

    def send_file(self):
        if not self.node:
            return

        try:
            peer_id = self._selected_peer_id()
        except ValueError as exc:
            messagebox.showwarning("Select Peer", str(exc))
            return

        filepath = filedialog.askopenfilename(
            title="Select a file to send"
        )

        if not filepath:
            return

        # File sending is done in a worker so the GUI remains responsive.
        def worker():
            try:
                self.node.send_file(peer_id, filepath)
            except Exception as exc:
                self.log(f"[ERROR] File transfer failed: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _on_close(self):
        try:
            if self.node:
                self.node.stop()
        finally:
            self.destroy()


if __name__ == "__main__":
    app = P2PApp()
    app.mainloop()
