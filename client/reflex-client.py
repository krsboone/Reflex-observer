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

# Tracks which script stems (uppercase) should be skipped
ignore_set  = set()
ignore_lock = threading.Lock()


def update_config_ignore_list(config_path, ignore_str):
    """Persist the new ignore_list value back to config/config.ini."""
    lines = config_path.read_text().splitlines()
    updated = []
    found = False
    for line in lines:
        if line.strip().startswith("ignore_list"):
            updated.append(f"ignore_list = {ignore_str}")
            found = True
        else:
            updated.append(line)
    if not found:
        updated.append(f"ignore_list = {ignore_str}")
    config_path.write_text("\n".join(updated) + "\n")


class IgnoreListener(SubscribeCallback):
    """Listens on ignore_chan and updates the local ignore_set.
    Only acts on messages whose 'host' matches this device's hostname.
    """
    def __init__(self, hostname, config_path):
        self._hostname  = hostname
        self._config_path = config_path

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
        # Persist to config.ini so the list survives restarts
        update_config_ignore_list(self._config_path, ignore_str)

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
            section["ignore_chan"], section.get("ignore_list", ""))

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

    pub, sub, scripts, config_chan, ignore_chan, ignore_list = load_config()
    hostname = args.name if args.name else socket.gethostname()

    pnconfig = PNConfiguration()
    pnconfig.publish_key = pub
    pnconfig.subscribe_key = sub
    pnconfig.user_id = hostname
    pubnub = PubNub(pnconfig)

    # Seed ignore_set from config.ini ignore_list
    for name in ignore_list.split(","):
        name = name.strip().upper()
        if name:
            ignore_set.add(name)

    # Subscribe to ignore channel to receive live deactivation messages
    config_path = Path(__file__).parent / "client-config" / "config.ini"
    pubnub.add_listener(IgnoreListener(hostname, config_path))
    pubnub.subscribe().channels([ignore_chan]).execute()

    scripts_dir = Path(__file__).parent / scripts.rstrip("/")
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
                #pubnub.publish().channel(channel).message({
                #    "type": "heartbeat", "id": hostname
                #}).sync()
                #print(f"Heartbeat sent [{time.strftime('%H:%M:%S')}]", end="\r")
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
