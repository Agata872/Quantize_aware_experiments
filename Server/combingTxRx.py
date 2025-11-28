#!/usr/bin/env python3
import subprocess
import threading
import sys
import yaml


def load_inventory(inventory_file):
    """Load the inventory.yaml file"""
    try:
        with open(inventory_file, "r") as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"❌ Failed to load {inventory_file}: {e}")
        sys.exit(1)


def extract_hosts_from_group(inventory, group_name):
    """Extract the list of hostnames under the specified group"""
    children = inventory.get("all", {}).get("children", {})
    group = children.get(group_name, {})
    hosts = group.get("hosts", {})
    return list(hosts.keys())


def main():
    inventory_file = "inventory.yaml"
    inventory = load_inventory(inventory_file)

    # ✅ 修改1：原 TX_NAME 改为 TX1_NAME，并添加 TX2_NAME
    TX1_NAME = "T05"
    RX_GROUP_NAME = "Test"
    RX_NAMES = extract_hosts_from_group(inventory, RX_GROUP_NAME)

    global_user = inventory.get("all", {}).get("vars", {}).get("ansible_user", "pi")
    all_hosts = inventory.get("all", {}).get("hosts", {})

    # ✅ 获取 TX1 信息
    if TX1_NAME not in all_hosts:
        print(f"❌ Transmitter {TX1_NAME} not found in inventory")
        sys.exit(1)
    tx1_ip = all_hosts[TX1_NAME].get("ansible_host")
    if not tx1_ip:
        print(f"❌ Transmitter {TX1_NAME} is missing the ansible_host attribute")
        sys.exit(1)
    tx1_target = f"{global_user}@{tx1_ip}"

    TX1_SCRIPT_PATH = "~/Quantize_aware_experiments/client/pilot.py"
    RX_SCRIPT_PATH = "~/Quantize_aware_experiments/client/pilot.py"

    threads = []
    processes = []

    def run_and_store(target, path):
        """Wrap run_remote_script and store Popen process"""
        remote_cmd = (
            'cd ~/Quantize_aware_experiments '
            # 'export PYTHONPATH="/usr/local/lib/python3/dist-packages:$PYTHONPATH"; '
            'export PYTHONPATH="/usr/local/lib/python3.11/site-packages:$PYTHONPATH"; '
            f'python3 -u {path}'
        )
        cmd = ["ssh", target, remote_cmd]
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            processes.append(process)

            for line in process.stdout:
                print(f"[{target}] Output: {line}", end='')
            process.wait()
            stderr_output = process.stderr.read()
            if stderr_output:
                print(f"[{target}] Error Output:\n{stderr_output}")
        except Exception as e:
            print(f"❌ Failed to run script on {target}: {e}")

    try:
        # ✅ Launch TX1
        print(f"🚀 Starting transmitter {TX1_NAME} ({tx1_target}) ...")
        tx1_thread = threading.Thread(target=run_and_store, args=(tx1_target, TX1_SCRIPT_PATH))
        threads.append(tx1_thread)
        tx1_thread.start()

        # ✅ Launch RX
        for rx_name in RX_NAMES:
            if rx_name not in all_hosts:
                print(f"⚠️ Skipping receiver {rx_name}, host not found in inventory")
                continue
            rx_ip = all_hosts[rx_name].get("ansible_host")
            if not rx_ip:
                print(f"⚠️ Skipping receiver {rx_name}, missing ansible_host")
                continue
            rx_target = f"{global_user}@{rx_ip}"
            print(f"📡 Starting receiver {rx_name} ({rx_target}) ...")
            rx_thread = threading.Thread(target=run_and_store, args=(rx_target, RX_SCRIPT_PATH))
            threads.append(rx_thread)
            rx_thread.start()

        # ✅ Waiting for all process
        for t in threads:
            t.join()

    except KeyboardInterrupt:
        print("\n🛑 Ctrl+C detected. Terminating all SSH processes...")
        for proc in processes:
            if proc.poll() is None:  # still running
                proc.terminate()
        print("✅ All remote scripts terminated.")

    finally:
        print("🔚 Coordination script finished.")


if __name__ == "__main__":
    main()
