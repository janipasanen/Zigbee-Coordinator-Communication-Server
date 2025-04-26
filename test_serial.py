import serial

def test_serial(port):
    try:
        # Open the serial port
        ser = serial.Serial(port, baudrate=115200, timeout=1)  # Adjust baudrate if needed
        print(f"Opened {port} successfully.")

        # Test communication by sending the 'AT' command
        ser.write(b'AT\r\n')
        response = ser.readline().decode().strip()
        print(f"Response: {response}")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()

if __name__ == "__main__":
    test_serial('/dev/ttyUSB0')  # Replace with your device