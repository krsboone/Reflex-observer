import sys
import os
import socket
import time
import subprocess
import configparser
import argparse
import threading
from pathlib import Path
from pubnub.pnconfiguration import PNConfiguration
from pubnub.pubnub import PubNub
from pubnub.callbacks import SubscribeCallback

# SDK bug fix: PNSubscriptionRegistryCallback.file() calls listener.file_message()
# but PubNubSubscription never defines that method, crashing the SubscribeMessageWorker
# thread on every file event. This patch bridges file_message → on_file so the
# crash is eliminated and the on_file attribute becomes functional.
from pubnub.models.subscription import PubNubSubscription as _PubNubSubscription
from pubnub.models.subscription import PubNubSubscriptionSet as _PubNubSubscriptionSet

def _file_message_bridge(self, event):
    cb = getattr(self, 'on_file', None)
    if callable(cb):
        cb(event)

for _cls in (_PubNubSubscription, _PubNubSubscriptionSet):
    if not hasattr(_cls, 'file_message'):
        _cls.file_message = _file_message_bridge

# Tracks which script stems (uppercase) should be skipped
ignore_set  = set()
ignore_lock = threading.Lock()



class IgnoreListener(SubscribeCallback):
    """Listens on ignore_chan and updates the local ignore_set.
    Only acts on messages whose 'host' matches this device's hostname.
    """
    def __init__(self, hostname):
        self._hostname = hostname

    def message(self, pubnub, event):
        msg = event.message
        # Only process messages addressed to this host
        if not isinstance(msg, dict) or msg.get("host") != self._hostname:
            return

        # Refresh command: re-publish current ignore list to hostname channel
        if msg.get("command") == "refresh":
            with ignore_lock:
                ignore_str = ", ".join(sorted(ignore_set))
            pubnub.publish().channel(self._hostname).message({
                "test": "ignore", "result": ignore_str
            }).sync()
            print(f"  Refresh requested — published ignore state: {ignore_str or 'none'}")
            return

        ignore_str = msg.get("ignore", "")
        with ignore_lock:
            ignore_set.clear()
            for name in ignore_str.split(","):
                name = name.strip().upper()
                if name:
                    ignore_set.add(name)
        active = sorted(ignore_set) or "none"
        print(f"  Ignore list updated: {active}")

    def status(self, pubnub, event):
        pass

    def presence(self, pubnub, event):
        pass

# Test data sent as (channel = hostname):
# {'test":"script name", "result":"value returned"}
# {'test":"ignore", "result":"NODE, PROC, PYTH"}
# Ignore data received as (channel = IGNORE_CHAN):
# {'host":"test1-us-east-1", "ignore":"NODE, PROC, PYTH"}
# Refresh payload (channel = IGNORE_CHAN):
# {'host":"test1-us-east-1", "command":"refresh"}

class ScriptFileListener(SubscribeCallback):
    """Listens on config_chan for new script file uploads and downloads them."""
    def __init__(self, config_chan, scripts_dir):
        self._config_chan = config_chan
        self._scripts_dir = scripts_dir

    def file(self, pubnub, event):
        # PNFileMessageResult has flat attributes: file_name, file_id, file_url
        try:
            file_name = event.file_name
            file_id   = event.file_id
        except AttributeError:
            return
        if not file_name.endswith(".py"):
            return
        try:
            dl = pubnub.download_file() \
                .channel(self._config_chan) \
                .file_name(file_name) \
                .file_id(file_id) \
                .sync()
            (self._scripts_dir / file_name).write_bytes(dl.result.data)
            print(f"  New script downloaded: {file_name}")
        except Exception as e:
            print(f"  Script download error ({file_name}): {e}")

    def message(self, _pubnub, _event):
        pass

    def status(self, _pubnub, _event):
        pass

    def presence(self, _pubnub, _event):
        pass


class MemberWrapper:
    def __init__(self, id_val):
        self.id_val = id_val
    def to_payload_dict(self):
        return {
            "uuid": {
                "id": self.id_val
            }
        }

def load_config():
    config_path = Path(__file__).parent / "client-config" / "config.ini"
    config = configparser.ConfigParser()
    with open(config_path) as f:
        config.read_string("[settings]\n" + f.read())
    section = config["settings"]
    return (section["pub"], section["sub"],
            section["scripts"], section["config_chan"],
            section["ignore_chan"])

def sync_scripts(pubnub, scripts_dir, config_chan):
    """
    Compare PubNub config channel files with local scripts directory.
    Download any files from PubNub that don't exist locally.
    """
    print(f"Syncing PubNub channel '{config_chan}' → '{scripts_dir.name}/'")

    # List files in the PubNub channel
    try:
        response = pubnub.list_files().channel(config_chan).sync()
        remote_files = {f["name"]: f["id"] for f in response.result.data}
        print(f"  Remote: {sorted(remote_files) or 'none'}")
    except Exception as e:
        print(f"  list_files error: {e}")
        return

    # List local .py files
    local_names = {f.name for f in scripts_dir.glob("*.py")}
    print(f"  Local:  {sorted(local_names) or 'none'}")

    # Download files that exist in PubNub but not locally
    missing = {name: fid for name, fid in remote_files.items() if name not in local_names}
    if not missing:
        print("  All scripts already present.")
        return

    for name, file_id in missing.items():
        try:
            dl = pubnub.download_file() \
                .channel(config_chan) \
                .file_name(name) \
                .file_id(file_id) \
                .sync()
            (scripts_dir / name).write_bytes(dl.result.data)
            print(f"  Downloaded: {name}")
        except Exception as e:
            print(f"  Download error ({name}): {e}")

def run_client_scripts(pubnub, hostname, scripts):
    scripts_dir = Path(__file__).parent / scripts
    with ignore_lock:
        current_ignore = set(ignore_set)
    for script in sorted(scripts_dir.glob("*.py")):
        if script.stem.upper() in current_ignore:
            continue
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True
        )
        output = result.stdout.strip() or result.stderr.strip() or "(no output)"
        print(f"{script.stem}: {output}")
        payload = {"test": script.stem, "result": output}
        try:
            pubnub.publish().channel(hostname).message(payload).sync()
        except Exception as e:
            print(f"Publish error ({script.stem}): {e}")

def initialize_pubnub_metadata(pn_client, channel_id, hostname):
    """
    Ensures both Channel and User (UUID) metadata exist.
    Required for brand-new keysets before 'joining' can work.
    """
    # 1. Register the Channel Metadata
    try:
        pn_client.set_channel_metadata() \
            .channel(channel_id) \
            .set_name("Device Availability Monitor") \
            .sync()
        print(f"✅ Channel '{channel_id}' initialized.")
    except Exception as e:
        print(f"Channel Metadata Note: {e}")

    # 2. Register the UUID (User) Metadata
    # On new keys, the User must exist before being added to a channel
    try:
        pn_client.set_uuid_metadata() \
            .uuid(hostname) \
            .set_name(f"Node: {hostname}") \
            .sync()
        print(f"✅ User/Device '{hostname}' initialized.")
    except Exception as e:
        print(f"User Metadata Note: {e}")

def main():
    parser = argparse.ArgumentParser(description="Reflex Observability Client")
    parser.add_argument('action', choices=['join', 'exit'], help="Action to perform")
    parser.add_argument('--name', help="Override the default hostname")
    args = parser.parse_args()

    pub, sub, scripts, config_chan, ignore_chan = load_config()
    hostname = args.name if args.name else socket.gethostname()

    pnconfig = PNConfiguration()
    pnconfig.publish_key = pub
    pnconfig.subscribe_key = sub
    pnconfig.user_id = hostname
    pubnub = PubNub(pnconfig)

    # Seed ignore_set from UUID metadata (App Context)
    try:
        envelope = pubnub.get_uuid_metadata(uuid=hostname, include_custom=True).sync()
        if envelope and envelope.result and envelope.result.data:
            custom = envelope.result.data.get("custom") or {}
            ignore_str = custom.get("ignore", "")
            for name in ignore_str.split(","):
                name = name.strip().upper()
                if name:
                    ignore_set.add(name)
        print(f"  Loaded ignore list from metadata: {sorted(ignore_set) or 'none'}")
    except Exception as e:
        if "404" in str(e):
            print("  No ignore list configured yet.")
        else:
            print(f"  Could not load ignore list from metadata: {e}")

    scripts_dir = Path(__file__).parent / scripts.rstrip("/")

    # Subscribe to ignore and config channels for live updates
    pubnub.add_listener(IgnoreListener(hostname))
    pubnub.add_listener(ScriptFileListener(config_chan, scripts_dir))
    pubnub.subscribe().channels([ignore_chan, config_chan]).execute()
    sync_scripts(pubnub, scripts_dir, config_chan)

    channel = "availability_monitor"

    # Updated: Now passing hostname to initialize user metadata too
    initialize_pubnub_metadata(pubnub, channel, hostname)

    if args.action == 'join':
        print(f"--- Joining Monitor Network as: {hostname} ---")
        try:
            member_wrapper = MemberWrapper(hostname)
            pubnub.set_channel_members() \
                .channel(channel) \
                .uuids([member_wrapper]) \
                .sync()
            print("Successfully registered as a Channel Member.")
        except Exception as e:
            print(f"Join Error: {e}")

        # Publish current ignore list so the dashboard can restore hyperlinks
        try:
            ignore_str = ", ".join(sorted(ignore_set))
            pubnub.publish().channel(hostname).message({
                "test": "ignore", "result": ignore_str
            }).sync()
            print(f"  Published ignore state: {ignore_str or 'none'}")
        except Exception as e:
            print(f"  Ignore publish error: {e}")

        try:
            while True:
                run_client_scripts(pubnub, hostname, scripts)
                time.sleep(2)
        except KeyboardInterrupt:
            print("\nMonitoring paused.")

    elif args.action == 'exit':
        print(f"--- Removing {hostname} from Monitor Network ---")
        try:
            member_wrapper = MemberWrapper(hostname)
            pubnub.remove_channel_members() \
                .channel(channel) \
                .uuids([member_wrapper]) \
                .sync()
            print("Successfully removed from membership.")
        except Exception as e:
            print(f"Removal Error: {e}")

if __name__ == "__main__":
    main()
