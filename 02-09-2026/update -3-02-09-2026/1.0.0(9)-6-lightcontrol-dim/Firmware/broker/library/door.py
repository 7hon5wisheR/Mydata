# door.py (versi hardware pull-up)
import RPi.GPIO as GPIO
__version__ = "1.0.0(9) - normalize status title-case, hardware pull-up"
# =====================================================================================================================================
#      DOOR
#      Name                       : DOOR
#      Version                    : 1.0.0(9)
#      Date Created               : 08-05-2026
#      Author                     : Saifuddin
# ======================================================================================================================================
DOOR1_GPIO = 17
DOOR2_GPIO = 27
GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)
GPIO.setup(DOOR1_GPIO, GPIO.IN)
GPIO.setup(DOOR2_GPIO, GPIO.IN)
def get_door_status(pin):
    return "Open" if GPIO.input(pin) == 1 else "Close"
def get_door1_status():
    return get_door_status(DOOR1_GPIO)
def get_door2_status():
    return get_door_status(DOOR2_GPIO)