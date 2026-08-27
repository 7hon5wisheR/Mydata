# humidity.py
# -*- coding: utf-8 -*-
__version__ = "1.0.0(9) - GPIO simulation (no hardware)"

# =====================================================================================================================================
#      HUMIDITY
#      Name                       : HUMIDITY
#      Version                    : 0.0.0.1 - GPIO simulation (no hardware)
#      Date Created               : 20-05-2026
#      Author                     : Saifuddin
#      Notes                      : Hardware not yet available - GPIO simulation only
#                                   Random value between 89.9 - 90.0 % RH
#                                   Ready to replace with real GPIO implementation
#                                   when hardware is available (e.g. DHT22 / SHT31 / HTU21D)
# ======================================================================================================================================

import random
import threading
import time

# =====================================================
# GPIO PIN (reserved - not yet in use)
# Replace with the correct pin when hardware is available.
# If sharing the same sensor as temperature (e.g. DHT22),
# use the same pin as TEMPERATURE_GPIO = 4
# =====================================================
HUMIDITY_GPIO = 4   # reserved, not yet in use

# =====================================================
# SIMULATION STATE
# =====================================================
_simulated_humidity = round(random.uniform(89.9, 90.0), 1)
_sim_lock           = threading.Lock()


def _update_simulation():
    """
    Background thread: updates simulated value every 5 seconds
    to avoid a flat reading (realistic minor fluctuation).
    """
    global _simulated_humidity
    while True:
        time.sleep(5)
        with _sim_lock:
            _simulated_humidity = round(random.uniform(89.9, 90.0), 1)


_sim_thread = threading.Thread(target=_update_simulation, daemon=True)
_sim_thread.start()


# =====================================================
# PUBLIC API
# =====================================================

def get_humidity() -> float:
    """
    Read the current humidity value (% RH).

    When hardware is available, replace the body of this function
    with the actual GPIO read. The interface stays the same.

    Returns:
        float: Humidity in percent RH, e.g. 90.0
    """
    with _sim_lock:
        return _simulated_humidity


def get_humidity_status() -> str:
    """
    Return humidity as a formatted string with unit.
    Used by status.py, utilitys.py, rfid2.json.

    Returns:
        str: e.g. "90.0"
    """
    return f"{get_humidity()}"


def get_humidity_float() -> float:
    """
    Return humidity as a float (without unit).
    Useful when the consumer needs a numeric value for calculation.

    Returns:
        float: e.g. 90.0
    """
    return get_humidity()
