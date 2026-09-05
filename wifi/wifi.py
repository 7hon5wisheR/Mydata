import tkinter as tk
import subprocess

def get_wifi_passwords():
    try:
        # Get all WiFi profiles
        result = subprocess.run(['netsh', 'wlan', 'show', 'profiles'], capture_output=True, text=True)
        profiles = [line.split(":")[1].strip() for line in result.stdout.splitlines() if "All User Profile" in line]

        wifi_passwords = {}
        for profile in profiles:
            # Get password for each WiFi profile
            try:
                result = subprocess.run(['netsh', 'wlan', 'show', 'profile', profile, 'key=clear'], capture_output=True, text=True)
                password_lines = result.stdout.splitlines()
                password = next((line.split(":")[1].strip() for line in password_lines if "Key Content" in line), "Password not found")
                wifi_passwords[profile] = password
            except subprocess.CalledProcessError:
                wifi_passwords[profile] = "Error retrieving password"

        return wifi_passwords

    except subprocess.SubprocessError as e:
        return {"Error": str(e)}

def display_wifi_passwords():
    wifi_passwords = get_wifi_passwords()

    root = tk.Tk()
    root.title("WiFi Passwords")

    if "Error" in wifi_passwords:
        tk.Label(root, text="Error: Could not retrieve WiFi passwords.").pack()
    else:
        for profile, password in wifi_passwords.items():
            tk.Label(root, text=f"WiFi: {profile}, Password: {password}").pack()

    root.mainloop()

display_wifi_passwords()