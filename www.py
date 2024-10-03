import serial

ser = serial.Serial("COM3", timeout=.1)

# data = "Hello"

# ser.write(bytes(data, "utf-8"))

while True:
    print(ser.readline())