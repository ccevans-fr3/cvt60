#!/usr/bin/python3

import math
import datetime
import pigpio
import sys
import os
import threading
from time import sleep
from subprocess import call

# Serial number of CVT60 unit
unit_number = '001'

# Step adjustment to bring axes parallel with wall when homed
# (positive = toward wall, negative = away from wall)
stepper_cal_1, stepper_cal_2 = 5, 10

# Number of microsteps per full step (e.g. half step = 2)
step_mode = 8

# Time to wait between individual steps in seconds
wait = (8 / step_mode) / 1000

# Number of columns and rows of jars on cart
jar_cols, jar_rows = 11, 7

# Diameter of jars in mm
jar_diam = 78

# Location of arm shoulder axis relative to jars in mm
ori_x = jar_cols*jar_diam / 2
ori_y = -6

# Length of arm sections in mm
arm_1, arm_2 = 330, 330

# Coefficient for converting degrees to steps
# (pulley tooth count/motor tooth count * steps per revolution/360)
stepper_1_deg_to_step = 116/20 * 200/360 * step_mode
stepper_2_deg_to_step = 80/20 * 200/360 * step_mode

# Number of steps over which to implement easing function
ease_count = 20 * step_mode

# Assign pigpio to Raspberry Pi
pi = pigpio.pi()

# Initialize stepper positions
stepper_1 = stepper_2 = 0

CW = 0                  # Clockwise stepper movement
CCW = 1                 # Counterclockwise stepper movement
enable = 1              # Enable stepper
disable = 0             # Disable stepper

# Pin assignments. All numbers are BCM, not physical pin number.
step_pin_1   =    21    # First axis stepper movement
dir_pin_1    =    13    # First axis stepper direction
step_pin_2   =    12    # Second axis stepper movement
dir_pin_2    =    20    # Second axis stepper direction
ena_pin      =    24    # Stepper enable via relay switch

servo_pin    =    27    # Servo controlling measure plate, PWM at 50Hz
dc_pin       =    4     # DC vibration motor
lmt_pin_1    =    23    # Limit switch for homing first axis
lmt_pin_2    =    22    # Limit switch for homing second axis
stop_pin     =    2     # Stop button for halting program

pi.set_mode(step_pin_1, pigpio.OUTPUT)
pi.write(step_pin_1, 0)
pi.set_mode(dir_pin_1, pigpio.OUTPUT)
pi.write(dir_pin_1, 0)
pi.set_mode(step_pin_2, pigpio.OUTPUT)
pi.write(step_pin_2, 0)
pi.set_mode(dir_pin_2, pigpio.OUTPUT)
pi.write(dir_pin_2, 0)
pi.set_mode(ena_pin, pigpio.OUTPUT)
pi.write(ena_pin, enable)

pi.set_mode(servo_pin, pigpio.OUTPUT)
pi.set_PWM_frequency(servo_pin, 50)
pi.set_mode(dc_pin, pigpio.OUTPUT)
pi.write(dc_pin, 1)
pi.set_mode(lmt_pin_1, pigpio.INPUT)
pi.set_pull_up_down(lmt_pin_1, pigpio.PUD_UP)
pi.set_mode(lmt_pin_2, pigpio.INPUT)
pi.set_pull_up_down(lmt_pin_2, pigpio.PUD_UP)
pi.set_mode(stop_pin, pigpio.INPUT)
pi.set_pull_up_down(stop_pin, pigpio.PUD_UP)

# Loading and dispensing angles for measure servo (degrees)
load_angle = {
    1:0,
    2:31,
    3:64.8,
    4:101.3,
    5:140.2,
    }
dispense_angle = {
    1:15.5,
    2:47.9,
    3:83,
    4:120.7,
    5:160.9,
    }
# Offset for measuring disc (degrees)
offset = 6


"""
Returns feeding day (1-5) when given day of week
"""
def get_day(i):
    switcher={
        0:5,    # Monday    (day 5)
        1:1,    # Tuesday   (demo/testing)
        2:1,    # Wednesday (demo/testing)
        3:1,    # Thursday  (day 1)
        4:2,    # Friday    (day 2)
        5:3,    # Saturday  (day 3)
        6:4,    # Sunday    (day 4)
        }
    return switcher.get(i, "invalid day")

"""
Quadratic ease in/out function.
This eases the steppers up to full speed and back to rest
to reduce strain and prevent missed steps.
"""
def easeinout(t):
    b = wait*4      # initial wait time
    c = wait - b    # change in wait time
    d = ease_count  # number of steps over which to change wait time
    
    t /= d/2
    if t < 1:
        return c/2*t*t+b
    t = t-1
    return -c/2*(t*(t-2)-1)+b

"""
Homing function.
The CVT uses limit switches on a single circuit to find home position.
Once one arm is homed, it must back off the limit switch to open the
circuit before homing the next arm.
"""
def home():
    backup_degrees = -10    # Degrees to back up each arm
    axis_1_degrees = 190    # Degrees to move first axis before failing
    axis_2_degrees = 370    # Degrees to move second axis before failing

    # Backup both axes before homing    
    start_steps(int(backup_degrees*stepper_1_deg_to_step), 
                int(backup_degrees*stepper_2_deg_to_step))
    sleep(1)
    
    # Home second axis
    for i in range(int(axis_2_degrees*stepper_2_deg_to_step)):
        if pi.read(lmt_pin_2): break    # Axis homed
        elif i < (int(axis_2_degrees*stepper_2_deg_to_step)):
            step(step_pin_2, CCW)
            sleep(wait)                 # Extra wait slows motor for homing
        elif i == (int(axis_2_degrees*stepper_2_deg_to_step))-1:
            shutdown("STEPPER 2 HOMING FAILED")
    sleep(1)

    # Home first axis
    for i in range(int(axis_1_degrees*stepper_1_deg_to_step)):
        if pi.read(lmt_pin_1): break     # Axis homed
        elif i < (int(axis_1_degrees*stepper_1_deg_to_step)):
            step(step_pin_1, CW)
            sleep(wait)                 # Extra wait slows motor for homing
        elif i == (int(axis_1_degrees*stepper_1_deg_to_step))-1:
            shutdown("STEPPER 1 HOMING FAILED")
    sleep(1)
    
    # Add calibration adjustment to both axes
    start_steps(stepper_cal_1*step_mode, stepper_cal_2*step_mode)
    sleep(1)

"""
Initiate stepper movement.
The coordinates of the next jar in mm is calculated, then this
is passed to the function to calculate the number of stepper motor
steps to reach this position.
"""
def goto_coords(x, y):
    # Add radius to get center of jar and subtract arm origin offset
    x_coord = x*jar_diam + jar_diam/2 - ori_x
    y_coord = y*jar_diam + jar_diam/2 - ori_y
    get_step_counts(x_coord, y_coord)

"""
A reference is held to the current angle of steppers in degrees.
The next stepper positions are calculated from the jar coordinates
in mm and the difference between angles calculated.
"""
def get_step_counts(x, y):
    global stepper_1, stepper_2
    current_stepper_1, current_stepper_2 = stepper_1, stepper_2
    
    if x > 0:
        stepper_1 = 180 - (math.degrees(math.atan(y/x)) \
                    + math.degrees(math.acos((x*x + y*y + arm_1*arm_1 - arm_2*arm_2) \
                    / (2*math.sqrt(y*y + x*x)*arm_1))))
        stepper_2 = math.degrees(math.acos((arm_2*arm_2 + arm_1*arm_1 - x*x - y*y) \
                    / (2*arm_1*arm_2)))
    elif x < 0:
        stepper_1 = math.degrees(math.atan(y/abs(x))) \
                    + math.degrees(math.acos((x*x + y*y + arm_1*arm_1 - arm_2*arm_2) \
                    / (2*math.sqrt(y*y + x*x)*arm_1)))
        stepper_2 = 360 - math.degrees(math.acos((arm_2*arm_2 + arm_1*arm_1 - x*x - y*y) \
                    / (2*arm_1*arm_2)))
    elif x == 0:
        stepper_1 = math.degrees(math.atan(y/1)) \
                    + math.degrees(math.acos((1 + y*y + arm_1*arm_1 - arm_2*arm_2) \
                    / (2*math.sqrt(y*y + 1)*arm_1)))
        stepper_2 = 360 - math.degrees(math.acos((arm_2*arm_2 + arm_1*arm_1 - x*x - y*y) \
                    / (2*arm_1*arm_2)))
    
    step_count_1 = int(stepper_1_deg_to_step * (current_stepper_1 - stepper_1))
    step_count_2 = int(stepper_2_deg_to_step * (current_stepper_2 - stepper_2))
    
#    print("x=" + str(x) + ", y=" + str(y))
#    print("angle_1=" + str(stepper_1) + ", angle_2=" + str(stepper_2))
#    print("---------------------------------------------")
    
    start_steps(step_count_1, step_count_2)

def start_steps(step_count_1, step_count_2):
    # Create stepper threads
    t1 = threading.Thread(target=step_thread, args=(1, step_count_1))
    t2 = threading.Thread(target=step_thread, args=(2, step_count_2))

    # Start stepper threads
    t1.start()
    t2.start()

    # Wait until both threads have finished
    t1.join()
    t2.join()

def step_thread(stepper, step_count):
    i = 0           # Current step number

    for s in range(abs(step_count)):
        if stepper == 1:
            if step_count > 0:
                step(step_pin_1, CW)
            elif step_count < 0:
                step(step_pin_1, CCW)
        elif stepper == 2:
            if step_count > 0:
                step(step_pin_2, CCW)
            elif step_count < 0:
                step(step_pin_2, CW)
        
        i += 1      # Increment step counter
        
        # Ease into and out of movement
        if i <= ease_count and i < abs(step_count)/2:
            sleep(easeinout(i)-wait)
        elif i >= abs(step_count)-ease_count:
            sleep(easeinout(abs(step_count)-i)-wait)

def step(stepper, direction):
    if stepper is step_pin_1: pi.write(dir_pin_1, direction)
    elif stepper is step_pin_2: pi.write(dir_pin_2, direction)
    
    pi.write(stepper, 1)
    pi.write(stepper, 0)
    sleep(wait)

def set_servo_angle(angle):
    servo_wait = 70 / 1000
    pw = angle * 2000/180 + 500
    pi.set_servo_pulsewidth(servo_pin, pw)
    sleep(servo_wait)
    
def vibrate(seconds):
    pi.write(dc_pin, 0)
    sleep(seconds)
    pi.write(dc_pin, 1)
    sleep(0.5)
    
def dispense(i):
    # Load
    set_servo_angle(load_angle[i] + offset)
    sleep(0.5)
    vibrate(0.5)

    # Dispense
    set_servo_angle(dispense_angle[i] + offset)
    sleep(0.5)
    vibrate(0.5)

def stop_callback(gpio, level, tick):
    for i in range(5):
        sleep(0.1)
        if pi.read(27): return
            
    shutdown("STOP BUTTON PRESSED")

def shutdown(result):    
    # Release motors
    pi.write(ena_pin, disable)
    sleep(1)        # Extra time before pigpio focus returns to daemon
    pi.set_servo_pulsewidth(servo_pin, 0)
    pi.write(dc_pin, 1)
    pi.stop()       # Stop pigpio and return button input focus to daemon
    
    # Print report
    print(str(datetime.datetime.now()) + ": " + result)
    file = open("/home/pi/log.txt", "a+")
    file.write("\n" + str(datetime.datetime.now()) + ": " + result)
    file.close()
    
    # Log report on Google Sheets
    call(['/usr/bin/python3', '/home/pi/cvt60/logger.py', unit_number, result])
    
    print("Shutdown complete.")
    sleep(2)
    sys.exit()


try:
    # Sleep to prevent start switch input from triggering stop callback
    sleep(2)
    
    # Set callback to check for stop button press
    cb = pi.callback(stop_pin, pigpio.FALLING_EDGE, stop_callback)
    
    # Get current feeding day
    day = get_day(datetime.date.today().weekday())

    # Home motors before beginning
    home()
    
    # Go to predefined start position (x,y) before continuing cycle
    # This is implemented to avoid dispenser hitting wall on the way to jar(0,0)
    goto_coords(4, 4)

    # Run main dispensing procedure
    for x in range(jar_cols):
        if x % 2 == 0:
            for y in range(jar_rows):               # Forward for even-numbered columns
                goto_coords(x, y)                   # Goto x/y coordinates of next jar
                dispense(day)                       # Accepts day integer 1-5
        else:
            for y in range(jar_rows-1, -1, -1):     # Reverse for odd-numbered columns
                goto_coords(x, y)                   # Go to x/y coordinates of next jar
                dispense(day)                       # Accepts day integer 1-5

    # Return steppers to home position
    goto_coords(6, 4)
    home()
    
    # Execute process cleanup and pass result as argument
    shutdown("SUCCESS")
    
except:
    shutdown(str(sys.exc_info()))

