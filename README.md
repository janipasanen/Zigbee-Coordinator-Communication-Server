### Zigbee Coordinator/Communication Server

A python application that uses the ZBT-1, or other Zigbee 3.0 antenna, to pair and communicate with Zigbee 3.0 devices.

Used for pairing with devices and fetching data.

## Create venv

python3 -m venv myenv

python3 -m venv ZigbeeCoordinatorCommunicationServer



## activate venv

source myenv/bin/activate

source ZigbeeCoordinatorCommunicationServer/bin/activate

# if installing library for the user use below but if installing in venv it cannot be used as user space installed libraries cannot be accessed in a venv.
#pip install --user pyserial

# after activiting the venv run 
pip install pyserial