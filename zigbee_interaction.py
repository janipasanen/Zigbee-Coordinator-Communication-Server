import asyncio
import argparse
from bellows.zigbee.application import ControllerApplication as BellowsApplication


# Configuration variables
# MacOS device path: /dev/cu.usbserial-1A1230
#DEVICE_PATH = '/dev/cu.usbserial-1A1230'

# Linux devie path /dev/ttyUSB0
DEVICE_PATH = '/dev/ttyUSB0'
CHANNEL = 15

# Zigbee Cluster IDs for Temperature and Humidity
TEMPERATURE_CLUSTER_ID = 0x0402
HUMIDITY_CLUSTER_ID = 0x0405

async def pair_device():
    config = {
        'device': {
            'path': DEVICE_PATH,
        },
    }

    print("Starting ZBT-1 and initializing...")

    app = BellowsApplication(config)
    await app.connect()

    try:
        print("Trying to form a new Zigbee network...")
        await app.form_network()
        print("Network formed successfully!")
    except Exception as e:
        print(f"Could not form network (maybe already exists?): {e}")

    print("Starting the application...")
    await app.initialize(auto_form=False)

    print("Permitting joins for 60 seconds...")
    await app.permit(time_s=60)

    print("ZBT-1 is now in pairing mode. Activate pairing on your SNZB-02D sensor.")
    await asyncio.sleep(60)

    print("Pairing mode has ended.")
    await app.shutdown()


async def discover_sleepy_devices(app):
    #print("Starting sleepy device discovery...")
    network = app.devices

    print("Current devices known by coordinator:")
    for nwk, device in network.items():
        if isinstance(nwk, int):
            nwk_str = f"0x{nwk:04X}"
        else:
            nwk_str = str(nwk)

        ieee_str = str(device.ieee) if device.ieee else "<unknown>"
        print(f"NWK: {nwk_str}, IEEE: {ieee_str}")

    for nwk, device in network.items():
        if device.ieee is None:
            try:
                print(f"Sending Simple Descriptor Request to 0x{nwk:04X}...")
                await app.zdo.request_simple_desc(nwk, 1)
            except Exception as e:
                print(f"Failed to request descriptor from 0x{nwk:04X}: {e}")

async def periodic_discover(app):
    while True:
        await discover_sleepy_devices(app)
        await asyncio.sleep(60)  # Repeat every 60 seconds

async def listen_for_data():
    config = {
        'device': {
            'path': DEVICE_PATH,
        },
    }

    print("Connecting to ZBT-1 to listen for data...")
    app = BellowsApplication(config)

    await app.connect()
    await app.initialize(auto_form=False)

    print("Listening for incoming Zigbee events...")

    def attribute_updated(device, cluster, attribute, value):
        if cluster.cluster_id == TEMPERATURE_CLUSTER_ID:
            temperature = value / 100
            print(f"🌡️ Temperature: {temperature:.1f} °C (from {device.ieee})")
        elif cluster.cluster_id == HUMIDITY_CLUSTER_ID:
            humidity = value / 100
            print(f"💧 Humidity: {humidity:.1f}% (from {device.ieee})")
        else:
            print(f"Device {device.ieee}: Cluster 0x{cluster.cluster_id:04X} Attribute {attribute} Value {value}")

    def device_joined(device):
        print(f"🎉 Device joined: {device.ieee}")

    def device_initialized(device):
        print(f"✅ Device initialized: {device.ieee}")

    app.add_listener(
        type(
            "Listener",
            (object,),
            {
                "attribute_updated": attribute_updated,
                "device_joined": device_joined,
                "device_initialized": device_initialized,
            },
        )()
    )

    try:
        asyncio.create_task(periodic_discover(app))
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        print("Exiting...")
    finally:
        await app.shutdown()

def main():
    parser = argparse.ArgumentParser(description="ZBT-1 CLI tool for pairing and data listening.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("pair", help="Put ZBT-1 into pairing mode.")
    subparsers.add_parser("listen", help="Listen for incoming Zigbee messages.")

    args = parser.parse_args()

    if args.command == "pair":
        asyncio.run(pair_device())
    elif args.command == "listen":
        asyncio.run(listen_for_data())

if __name__ == "__main__":
    main()
