#!/usr/bin/python3

import pigpio
import os
import subprocess
import board
import neopixel
import requests
from time import sleep

enable = 1          # Enable stepper
disable = 0         # Disable stepper

pi = pigpio.pi()

# Pin assignments. All numbers are BCM, not physical pin number.
run_pin     =   2   # Run button
sd_pin      =   3   # Shutdown button
ena_pin     =   24  # Stepper enable via relay switch
servo_pin   =   27  # Servo controlling measure plate, PWM at 50Hz
dc_pin      =   4   # DC vibration motor

# Set up pins and ensure motors are disabled at startup
pi.set_mode(run_pin, pigpio.INPUT)
pi.set_pull_up_down(run_pin, pigpio.PUD_UP)
pi.set_mode(sd_pin, pigpio.INPUT)
pi.set_pull_up_down(sd_pin, pigpio.PUD_UP)
pi.set_mode(ena_pin, pigpio.OUTPUT)
pi.write(ena_pin, disable)
pi.set_mode(servo_pin, pigpio.OUTPUT)
pi.set_servo_pulsewidth(servo_pin, 0)
pi.set_mode(dc_pin, pigpio.OUTPUT)
pi.write(dc_pin, 1)

# GPIO 18 must be used with neopixels
pixels = neopixel.NeoPixel(board.D18, 8, brightness=0.5, auto_write=False)
pixels.fill((0,0,0))
pixels.show()

# Set hue of led bar (0-255)
rgb = (255,200,0)

url = 'http://clients3.google.com/generate_204'

def pulse(wait):
    global rgb
    
    for i in range(0,255,2):
        pixels.fill((rgb[0]*i//255,rgb[1]*i//255,rgb[2]*i//255))
        pixels.show()
        sleep(wait)
    sleep(1)
    
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 204:
            rgb = (40,255,0)
    except:
        rgb = (255,0,0)
    
    for i in range(255,-1,-2):
        pixels.fill((rgb[0]*i//255,rgb[1]*i//255,rgb[2]*i//255))
        pixels.show()
        sleep(wait)

def shutdown_callback(gpio, level, tick):
    for i in range(20):
        sleep(0.1)
        if pi.read(sd_pin): return
    pixels.fill((0,0,0))
    pixels.show()
    sleep(0.5)
    pi.stop()
    os.system("sudo shutdown now -h")

def run_callback(gpio, level, tick):
    for i in range(5):
        sleep(0.1)
        if pi.read(run_pin): return
    subprocess.call(['/usr/bin/python3', '/home/pi/cvt60/cart.py'])

# Set button callbacks
cb1 = pi.callback(sd_pin, pigpio.FALLING_EDGE, shutdown_callback)
cb2 = pi.callback(run_pin, pigpio.FALLING_EDGE, run_callback)

try:
    while True:
        pulse(0.02)     # Pulse the led bar with an argument of wait time

except:
    pixels.fill((0,0,0))
    pixels.show()

