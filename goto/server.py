import argparse
import hashlib
import http.server
import socketserver
from collections import OrderedDict
from pathlib import Path
from threading import Thread
from urllib.parse import ParseResult, urlparse


def file_location(algorithm: str, length: int, hash_digest: str) -> Path:
    path = Path(algorithm) / f"l{str(length)}"
    for i in range(0, length - 2, 2):
        path = path / hash_digest[i : i + 2]
    path = path / hash_digest
    return path


class LRU:
    def __init__(self, max_size: int):
        self.max_size = max_size
        self.cache = OrderedDict()

    def __contains__(self, key):
        return key in self.cache

    def __getitem__(self, key):
        self.cache.move_to_end(key)
        return self.cache[key]

    def __setitem__(self, key, value):
        self.cache[key] = value
        self.cache.move_to_end(key)
        if len(self.cache) >= self.max_size:
            self.cache.popitem(last=False)


class ContentHasher:
    def __init__(
        self,
        hash_algorithm: str = "sha256",
        hash_length: int = 5,
        state_dir: Path = Path("goto_state"),
        cache_size: int = 100,
    ):
        self.hash_algorithm = hash_algorithm
        self.hash_length = hash_length
        self.state_dir = state_dir
        self.cache = LRU(cache_size)

    def hash(self, content: bytes) -> str:
        hash = hashlib.new(self.hash_algorithm)
        hash.update(content)
        return hash.hexdigest()[: self.hash_length]

    def save(self, content: bytes) -> str:
        content_hash = self.hash(content)
        self.cache[content_hash] = content
        file_path = self.state_dir / file_location(
            self.hash_algorithm, self.hash_length, content_hash
        )
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)
        return content_hash


class CustomHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    hasher: ContentHasher

    def __init__(self, *args, hasher: ContentHasher, **kwargs):
        self.hasher = hasher
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        # Extract the requested URL
        parsed_url: ParseResult = urlparse(self.path)

        content_id = parsed_url.path.split("/")[-1]
        try:
            content = self.hasher.cache[content_id]
        except KeyError:
            try:
                content = (
                    self.hasher.state_dir
                    / file_location(
                        self.hasher.hash_algorithm, self.hasher.hash_length, content_id
                    )
                ).read_bytes()
                self.hasher.cache[content_id] = content
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                return
        self.send_response(302)
        self.send_header("Location", content.decode())
        self.end_headers()

    def do_POST(self) -> None:
        content_length = int(self.headers["Content-Length"])
        content = self.rfile.read(content_length)
        # Validate URL
        url = urlparse(content.decode()).geturl()
        if url:
            content_hash = self.hasher.save(url.encode())
            print(f"headers: {self.headers}")
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(f'http://{self.headers["Host"]}/{content_hash}'.encode())
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Invalid URL")


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Handle requests in a separate thread."""


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Run a simple multithreaded HTTP server."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port number for the server to listen on (default: 8080).",
    )
    parser.add_argument(
        "--hash-algorithm",
        type=str,
        default="sha256",
        help="Hash algorithm to use for content hashing (default: sha256).",
    )
    parser.add_argument(
        "--hash-length",
        type=int,
        default=5,
        help="Length of the hash to generate (default: 5).",
    )
    parser.add_argument(
        "--state-dir",
        type=str,
        default="goto_state",
        help="Location where to store the shortened urls",
    )
    parser.add_argument(
        "--cache-size",
        type=int,
        default=100,
        help="how many urls to cache in memory",
    )
    return parser.parse_args()


def run_server(
    port: int = 8080,
    hash_algorithm: str = "sha256",
    hash_length: int = 5,
    state_dir: Path = Path("goto_state"),
    cache_size: int = 100,
) -> None:
    """Run the multithreaded HTTP server on the specified port."""
    server_address: tuple[str, int] = ("", port)

    hasher = ContentHasher(
        hash_algorithm=hash_algorithm,
        hash_length=hash_length,
        state_dir=state_dir,
        cache_size=cache_size,
    )

    # Create the server with the custom handler
    def handler(*args, **kwargs):
        return CustomHTTPRequestHandler(*args, hasher=hasher, **kwargs)

    httpd: ThreadedHTTPServer = ThreadedHTTPServer(server_address, handler)

    print(f"Serving on port {server_address[1]}")

    # Start the server in a separate thread
    server_thread: Thread = Thread(target=httpd.serve_forever)
    server_thread.daemon = (
        True  # Allows the program to exit if the main thread terminates
    )
    server_thread.start()
    print("Server running in a separate thread.")
    server_thread.join()
    httpd.shutdown()


def main() -> None:
    args = parse_arguments()
    run_server(
        port=args.port,
        hash_algorithm=args.hash_algorithm,
        hash_length=args.hash_length,
        state_dir=Path(args.state_dir),
        cache_size=args.cache_size,
    )


if __name__ == "__main__":
    main()
