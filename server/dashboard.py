import io
import json
import configparser
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse
from pubnub.pnconfiguration import PNConfiguration
from pubnub.pubnub import PubNub

BASE_DIR = Path(__file__).parent

# --- Load config ---
config = configparser.ConfigParser()
with open(BASE_DIR / "server-config" / "config.ini") as f:
    config.read_string("[settings]\n" + f.read())

settings    = config["settings"]
PUB_KEY     = settings["pub"]
SUB_KEY     = settings["sub"]
SCRIPTS_DIR = BASE_DIR / settings["scripts"].rstrip("/")
CONFIG_CHAN  = settings["config_chan"]
IGNORE_CHAN  = settings["ignore_chan"]

# --- Shared PubNub client ---
_pnconfig = PNConfiguration()
_pnconfig.publish_key = PUB_KEY
_pnconfig.subscribe_key = SUB_KEY
_pnconfig.user_id = "dashboard-server"
pubnub = PubNub(_pnconfig)


def sync_scripts():
    """
    Compare local scripts/ dir with files stored in the PubNub config channel.
    Upload any .py files that are missing from the channel.
    """
    print(f"Syncing '{SCRIPTS_DIR.name}/' → PubNub channel '{CONFIG_CHAN}'")

    # List files already in the channel
    try:
        response = pubnub.list_files().channel(CONFIG_CHAN).sync()
        remote_names = {f["name"] for f in response.result.data}
        print(f"  Remote: {sorted(remote_names) or 'none'}")
    except Exception as e:
        print(f"  list_files error: {e}")
        remote_names = set()

    # List local .py files
    local_files = sorted(SCRIPTS_DIR.glob("*.py"))
    local_names  = {f.name for f in local_files}
    print(f"  Local:  {sorted(local_names) or 'none'}")

    # Upload anything missing
    missing = [f for f in local_files if f.name not in remote_names]
    if not missing:
        print("  All scripts already synced.")
        return

    for script in missing:
        try:
            with open(script, "rb") as fh:
                pubnub.send_file() \
                    .channel(CONFIG_CHAN) \
                    .file_name(script.name) \
                    .file_object(fh) \
                    .sync()
            print(f"  Uploaded: {script.name}")
        except Exception as e:
            print(f"  Upload error ({script.name}): {e}")


def get_members():
    try:
        response = pubnub.get_channel_members().channel("availability_monitor").sync()
        return [m["uuid"]["id"] for m in response.result.data]
    except Exception as e:
        print(f"Members fetch error: {e}")
        return []


class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path in ("/", "/index.html"):
            self._serve_file("dashboard.html", "text/html")
        elif path == "/api/config":
            self._serve_json({"pub": PUB_KEY, "sub": SUB_KEY, "ignore_chan": IGNORE_CHAN})
        elif path == "/api/members":
            self._serve_json({"members": get_members()})
        elif path.startswith("/images/"):
            filename = path.lstrip("/")
            mime = "image/png" if filename.endswith(".png") else "image/jpeg"
            self._serve_file(filename, mime)
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_file(self, filename, content_type):
        try:
            content = (BASE_DIR / filename).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

    def _serve_json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/api/upload-script":
            filename = self.headers.get("X-Filename", "")
            # Basic safety: must be a .py file with no path separators
            if not filename.endswith(".py") or "/" in filename or ".." in filename:
                self._serve_json({"ok": False, "error": "Invalid filename"})
                return

            length = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(length)

            # Save to local scripts directory
            dest = SCRIPTS_DIR / filename
            dest.write_bytes(data)
            print(f"  Saved locally: {filename}")

            # Upload to PubNub config channel
            try:
                pubnub.send_file() \
                    .channel(CONFIG_CHAN) \
                    .file_name(filename) \
                    .file_object(io.BytesIO(data)) \
                    .sync()
                print(f"  Uploaded to PubNub: {filename}")
                self._serve_json({"ok": True})
            except Exception as e:
                print(f"  PubNub upload error ({filename}): {e}")
                self._serve_json({"ok": False, "error": str(e)})
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress default request logs


if __name__ == "__main__":
    sync_scripts()

    port = 8080
    server = HTTPServer(("localhost", port), Handler)
    print(f"Dashboard → http://localhost:{port}")
    server.serve_forever()
