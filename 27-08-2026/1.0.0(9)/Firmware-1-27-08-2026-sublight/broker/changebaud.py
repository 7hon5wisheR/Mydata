import serial
import time
import RPi.GPIO as GPIO

SHUTDOWN_GPIO = 23
PORT = '/dev/ttyAMA0'

# CHANGE THIS to whatever the scanner found!
CURRENT_BAUD = 115200  # ← Put the baud rate you found here
TARGET_BAUD = 921600

GPIO.setmode(GPIO.BCM)
GPIO.setup(SHUTDOWN_GPIO, GPIO.OUT)
GPIO.output(SHUTDOWN_GPIO, GPIO.LOW)
time.sleep(0.5)

print("="*70)
print(f"CHANGING SIM7300 BAUD RATE")
print(f"From: {CURRENT_BAUD} → To: {TARGET_BAUD}")
print("="*70)

# Commands
CMD_START_FIRMWARE = bytes.fromhex("FF00041D0B")
CMD_SET_BAUD_921600 = bytes.fromhex("FF14AA4D6F64756C6574656368AA400601000E10000FBB799F")
CMD_GET_VERSION = bytes.fromhex("FF00031D0C")

# STEP 1: Connect at current baud rate
print(f"\nSTEP 1: Connecting at current baud rate ({CURRENT_BAUD})...")
try:
    ser = serial.Serial(PORT, CURRENT_BAUD, timeout=1)
    print(f"✓ Connected at {CURRENT_BAUD} baud")
except Exception as e:
    print(f"✗ Failed to connect: {e}")
    GPIO.cleanup()
    exit(1)

# STEP 2: Start firmware
print("\nSTEP 2: Starting firmware...")
ser.write(CMD_START_FIRMWARE)
time.sleep(1)
resp = ser.read_all()
if resp:
    print(f"✓ Firmware started: {resp.hex().upper()}")
else:
    print("⚠️  No response (might be okay)")

# STEP 3: Send baud rate change command
print(f"\nSTEP 3: Sending 'Set Baud {TARGET_BAUD}' command...")
ser.reset_input_buffer()
ser.write(CMD_SET_BAUD_921600)
time.sleep(0.5)
resp = ser.read_all()

if resp:
    print(f"Response: {resp.hex().upper()}")
    if len(resp) >= 5:
        status = (resp[3] << 8) | resp[4]
        if status == 0:
            print("✓ Baud change command accepted")
        else:
            print(f"✗ Command failed with status 0x{status:04X}")
else:
    print("✗ No response to baud change command")

print("\n⚠️  IMPORTANT: Baud rate change takes effect AFTER firmware restart")
print("   Closing connection and restarting firmware at new baud rate...")

# STEP 4: Close connection
ser.close()
print("✓ Closed connection")
time.sleep(1)

# STEP 5: Reconnect at NEW baud rate
print(f"\nSTEP 4: Reconnecting at NEW baud rate ({TARGET_BAUD})...")
try:
    ser = serial.Serial(PORT, TARGET_BAUD, timeout=1)
    print(f"✓ Opened port at {TARGET_BAUD} baud")
except Exception as e:
    print(f"✗ Failed to open at {TARGET_BAUD}: {e}")
    GPIO.cleanup()
    exit(1)

# STEP 6: Start firmware at new baud rate
print("\nSTEP 5: Starting firmware at new baud rate...")
ser.write(CMD_START_FIRMWARE)
time.sleep(1)
resp = ser.read_all()

if resp:
    print(f"✓ Firmware responded: {resp.hex().upper()}")
    print(f"\n✅ SUCCESS! SIM7300 is now at {TARGET_BAUD} baud")
else:
    print("✗ No response at new baud rate")
    print("\nBaud change might not have worked. Reader may still be at old baud rate.")
    print(f"Try reconnecting at {CURRENT_BAUD} and check if command was accepted.")

# STEP 7: Verify with GET VERSION
print("\nSTEP 6: Verifying with GET VERSION command...")
ser.reset_input_buffer()
ser.write(CMD_GET_VERSION)
time.sleep(0.5)
resp = ser.read_all()

if resp and len(resp) >= 22:
    print(f"✓ Version response received ({len(resp)} bytes)")
    print(f"Response: {resp.hex().upper()}")
    
    idx = 5
    print("\nVersion info:")
    print(f"  Bootloader: {resp[idx:idx+4].hex().upper()}")
    print(f"  Hardware:   {resp[idx+4:idx+8].hex().upper()}")
    print(f"  Firmware:   {resp[idx+8:idx+12].hex().upper()}")
    
    print(f"\n🎉 PERFECT! SIM7300 is working at {TARGET_BAUD} baud")
    print("\nYour code will now work with:")
    print(f"  ser = serial.Serial('/dev/ttyAMA0', {TARGET_BAUD}, timeout=1)")
else:
    print("✗ No version response")

# STEP 8: Make it permanent (save to flash)
print("\n" + "="*70)
print("OPTIONAL: Make Baud Rate Permanent")
print("="*70)
print("\nThe baud rate change is temporary (lost on power cycle).")
print("To make it permanent, we need to save it to flash.")
print("\nDo you want to save 921600 as the default baud rate? (y/n)")

choice = input("> ").strip().lower()

if choice == 'y':
    print("\nSaving default baud rate to flash...")
    
    # Command to save default baud rate (from protocol doc section 9.7.2)
    # Format: FF [len] AA [prefix] AA 67 [baud_value] [subcrc] BB [crc]
    # Baud value for 921600 = 0x0E1000 (from baud rate table)
    CMD_SAVE_DEFAULT_BAUD = bytes.fromhex("FF14AA4D6F64756C6574656368AA6701000E10000FBBF89C")
    
    ser.reset_input_buffer()
    ser.write(CMD_SAVE_DEFAULT_BAUD)
    time.sleep(0.5)
    resp = ser.read_all()
    
    if resp:
        print(f"Response: {resp.hex().upper()}")
        if len(resp) >= 5:
            status = (resp[3] << 8) | resp[4]
            if status == 0:
                print("✅ Default baud rate saved to flash!")
                print("   SIM7300 will now use 921600 baud after every power cycle")
            else:
                print(f"✗ Save failed with status 0x{status:04X}")
    else:
        print("✗ No response to save command")
else:
    print("\nSkipping permanent save.")
    print("⚠️  Remember: Baud rate will reset to factory default after power cycle")
    print("   You'll need to run this script again after power cycling")

# Cleanup
ser.close()
GPIO.output(SHUTDOWN_GPIO, GPIO.HIGH)
GPIO.cleanup()

print("\n" + "="*70)
print("BAUD RATE CHANGE COMPLETE")
print("="*70)
