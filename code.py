#import traceback  # DEBUG
#import sys  # DEBUG
#import supervisor  # DEBUG
#import os  # DEBUG
import gc  # Garbage Collector
import time  # Pretty important for a clock
import rtc  # for RTC; As above
import ssl  # For MQTT
import struct  # Primarily used by NTP
from microcontroller import watchdog as wd  # Watchdog
from watchdog import WatchDogMode
import socketpool
import wifi
import board  # LED and Accelerometer
import digitalio  # For red LED (D13) on the back of the board
import adafruit_lis3dh  # Accelerometer
import alarm
import neopixel
from adafruit_magtag.magtag import MagTag
import adafruit_minimqtt.adafruit_minimqtt as MQTT  # MQTT Client

wd.timeout = 30  # Set a timeout in seconds
wd.mode = WatchDogMode.RESET  # Set watchdog to reset mode
wd.feed()  # Feed watchdog

##################################
### The MagTag Clock by Woodsy ###
##################################
#
# Features:
#   Only updates screen if time has changed (every 60s usually)
#   Hourly chime between defined hours
#   Inverts screen between defined hours
#   Detects if the device is tapped and turns on neopixels for a defined time
#   Time synced initially from an NTP server
#   Display data from MQTT
#   Watchdog reset if things go wrong (e.g. MQTT connection lost)
#   Listen for live UDP packet and exec() the command
#   Red LED on the back of the board (D13) on when running code and off when sleeping

#   Notes:
#     https://learn.adafruit.com/adafruit-magtag
#     https://learn.adafruit.com/assets/102127  # pinout image
#     296x128 4-shade e-ink screen
#     SPEAKER GPIO17 ADC2_CH6 debug:U1TXD DAC_1
#     MagTag cannot play a WAV file with its buzzer - Don't waste hours trying like i did ;p

#   Freeware Fonts used from http://www.styleseven.com/
#     http://www.styleseven.com/php/get_product.php?product=Digital-7
#     http://www.styleseven.com/php/get_product.php?product=Thin%20Pixel-7
#     Converted using FontForge to BDF:
#       See: https://learn.adafruit.com/custom-fonts-for-pyportal-circuitpython-display
#            https://cdn-learn.adafruit.com/downloads/pdf/custom-fonts-for-pyportal-circuitpython-display.pdf
#     Digital-7-77.bdf
#     Digital-7-38.bdf
#     ThinPixel-7-20.bdf

############################
### Set your preferences ###
############################
sleep_time = 1  # how long to sleep between loops
# this will also multiply duration of other functions like tap_duration

hour_chime = 1  # chime hourly if enabled
hour_chime_start = 10  # start hour for chime
hour_chime_stop = 22  # stop hour for chime

tap_enable = 1  # enable tap detection
tap_duration = 5  # how long to keep the lights on after a tap
tap_threshold = 119  # 80 default, but i use 119 to avoid false positives. 127 is max

invert_enable = 1  # enable invert screen
invert_start = 18  # start hour for invert
invert_stop = 6  # stop hour for invert

tz_offset = 3600 * 10  # GMT+10 for me in Australia
ntp_server = "10.1.0.1"  # NTP server eg. au.pool.ntp.org
ntp_port = 123  # NTP UDP port defaults to 123

# other initial vars and constants that won't usually need to be changed
ntp_time_correction = 2_208_988_800  # epoch time in seconds offset
magtag = MagTag()
mqtt_sub_time = "00:00"
mqtt_sub_date = "00.00.00"
mqtt_sub_time_old = "xx:xx"
mqtt_sub_dowa = "xxx"  # Day of Week abbreviations Mon/Tue/Wed etc.
mqtt_sub_day = 00
mqtt_sub_moya = "xxx"  # Month of Year abbreviations Jan/Feb/Mar etc.
mqtt_sub_year2 = 00
mqtt_sub_month = 00
mqtt_sub_hour = 00
mqtt_sub_hour_old = 25
time_mono_last = time.monotonic()
lis = adafruit_lis3dh.LIS3DH_I2C(board.I2C(), address=0x19) # MagTag Accelerometer
tap_counter = 0

magtag.peripherals.neopixels.brightness = 1
magtag.peripherals.neopixel_disable = False
#magtag.peripherals.neopixels.fill((32, 32, 32))
#magtag.peripherals.neopixels.fill((1, 1, 1))
#magtag.peripherals.neopixels[3] = ((1, 0, 0))
#magtag.peripherals.neopixels[2] = ((0, 1, 0))
#magtag.peripherals.neopixels[1] = ((0, 0, 1))
#magtag.peripherals.neopixels[0] = ((1, 1, 0))
#magtag.peripherals.neopixel_disable = True # Also disables light sensor

#print(magtag.peripherals.battery) # Battery voltage
#print(magtag.peripherals.light) # My Light sensor range 556-52487
# percentage = 100 * (magtag.peripherals.light - 556) / (52487 - 556)

# Red LED on the back of the board
led = digitalio.DigitalInOut(board.D13)
led.direction = digitalio.Direction.OUTPUT
#led.value = True
#time.sleep(0.5)
#led.value = False
#time.sleep(0.5)

# Example to play tones
# #magtag.peripherals.speaker_disable = False
#magtag.peripherals.play_tone(1046.50, 0.125)  # C6
#magtag.peripherals.play_tone(1318.51, 0.125)  # E6
#magtag.peripherals.speaker_disable = True

# Another way to play tones
#import pwmio
#magtag.peripherals.speaker_disable = False
#pwm = pwmio.PWMOut(board.SPEAKER, frequency=1000, variable_frequency=True)
#pwm.duty_cycle = 2 ** 15
#time.sleep(sleep_time)

###############
### SECRETS ###
###############

#####################################
### Example content in secrets.py ###
#####################################
'''
secrets = {
    'ssid' : 'Super WIFI',
    'password' : 'WIFIPASSWORD',
    'aio_username' : 'Woodsy',
    'aio_key' : 'aio_ffffffffffffff',
    'timezone' : "Australia/Brisbane", # http://worldtimeapi.org/timezones
    'mac_addy' : "de:ad:be:ef:ca:fe",
    'mqtt_broker' : "10.1.0.1",
    'mqtt_port' : 1883,
    'mqtt_user' : "mqttusername",
    'mqtt_pass' : "mqttpassword"
    }
'''
# end of secrets.py example

try:
    from secrets import secrets
except ImportError:
    print("Secrets are kept in secrets.py, please add them there!")
    raise

# Set your Adafruit IO Username and Key in secrets.py
# (visit io.adafruit.com if you need to create an account,
# or if you need your Adafruit IO key.)
aio_username = secrets["aio_username"]
aio_key = secrets["aio_key"]
# Secrets end

#####################
### ACCELEROMETER ###
#####################
# Enable accel tap detection
lis.set_tap(1, threshold=tap_threshold, time_limit=10, time_latency=20, time_window=255)
### https://www.st.com/resource/en/design_tip/dm00069521-simple-screen-rotation-using-the-accelerometer-builtin-4d-detection-interrupt--stmicroelectronics.pdf
### lis._write_register_byte(0x20, 0x3F)  # low power mode with ODR = 25Hz
### lis._write_register_byte(0x22, 0x40)  # AOI1 interrupt generation is routed to INT1 pin
### lis._write_register_byte(0x23, 0x80)  # FS = ±2g low power mode with BDU bit enabled
### lis._write_register_byte(0x24, 0x0C)  # Interrupt signal on INT1 pin is latched with D4D_INT1 bit enabled
### lis._write_register_byte(0x32, 0x20)  # Threshold = 32LSBs * 15.625mg/LSB = 500mg. (~30 deg of tilt)
### lis._write_register_byte(0x33, 0x01)  # Duration = 1LSBs * (1/25Hz) = 0.04s
### # read to clear
### _ = lis._read_register_byte(0x31)
### # get current accel values
### _, y, _ = lis.acceleration
# Accelerometer end

############
### WIFI ###
############
print("MAC addr:", [hex(i) for i in wifi.radio.mac_address])
print("Available WiFi networks:")
for network in wifi.radio.start_scanning_networks():
    print("  %s\t\tRSSI: %d\tChannel: %d" % (str(network.ssid, "utf-8"), network.rssi, network.channel))
time.sleep(1)
wifi.radio.stop_scanning_networks()
print("Connecting to %s" % secrets["ssid"])
wifi.radio.connect(secrets["ssid"], secrets["password"], timeout=10)
print("Connected to %s!" % secrets["ssid"])
print(wifi.radio.ipv4_gateway)

# Create a socket pool
pool = socketpool.SocketPool(wifi.radio)
# WiFi end

#########################
### UDP packet listen ###
#########################
myip = wifi.radio.ipv4_address
udp_port = 808  # Port to listen on
udp_sock = pool.socket(pool.AF_INET,pool.SOCK_DGRAM)
udp_sock.bind((str(myip), udp_port))
packet = bytearray(1024)
udp_sock.setblocking(False)
#udp_sock.settimeout(0.1)
# UDP end

############
### MQTT ###
############
# MQTT setup on host server
#   apt install mosquitto mosquitto-clients
#   mosquitto_passwd -c /etc/mosquitto/pwfile mqtt
#   nano /etc/cron.d/mqtt-things
'''
# m h dom mon dow user  command
* *     * * *   root    /usr/bin/mosquitto_pub -u mqtt -P mqtt -r -t time/time  -m "$(date +\%H\:\%M)" > /dev/null 2>&1
* *     * * *   root    /usr/bin/mosquitto_pub -u mqtt -P mqtt -r -t time/min   -m "$(date +\%M)" > /dev/null 2>&1
0 *     * * *   root    /usr/bin/mosquitto_pub -u mqtt -P mqtt -r -t time/hour  -m "$(date +\%H)" > /dev/null 2>&1
0 0     * * *   root    /usr/bin/mosquitto_pub -u mqtt -P mqtt -r -t time/date  -m "$(date +\%d-\%m-\%Y)" > /dev/null 2>&1
0 0     * * *   root    /usr/bin/mosquitto_pub -u mqtt -P mqtt -r -t time/date2 -m "$(date +\%d.\%m.\%y)" > /dev/null 2>&1

0 0     * * *   root    /usr/bin/mosquitto_pub -u mqtt -P mqtt -r -t time/dow   -m "$(date +\%A)" > /dev/null 2>&1
0 0     * * *   root    /usr/bin/mosquitto_pub -u mqtt -P mqtt -r -t time/dowa  -m "$(date +\%a)" > /dev/null 2>&1

0 0     * * *   root    /usr/bin/mosquitto_pub -u mqtt -P mqtt -r -t time/moy   -m "$(date +\%B)" > /dev/null 2>&1
0 0     * * *   root    /usr/bin/mosquitto_pub -u mqtt -P mqtt -r -t time/moya  -m "$(date +\%b)" > /dev/null 2>&1

0 0     * * *   root    /usr/bin/mosquitto_pub -u mqtt -P mqtt -r -t time/day   -m "$(date +\%d)" > /dev/null 2>&1
0 0     * * *   root    /usr/bin/mosquitto_pub -u mqtt -P mqtt -r -t time/month -m "$(date +\%m)" > /dev/null 2>&1
0 0     * * *   root    /usr/bin/mosquitto_pub -u mqtt -P mqtt -r -t time/year  -m "$(date +\%Y)" > /dev/null 2>&1
0 0     * * *   root    /usr/bin/mosquitto_pub -u mqtt -P mqtt -r -t time/year2 -m "$(date +\%y)" > /dev/null 2>&1
#* *    * * *   root    /usr/bin/mosquitto_pub -u mqtt -P mqtt -r -t time/hour  -m "$(date +\%d-\%m-\%Y)" > /dev/null 2>&1
'''
# Then cron will populate the MQTT broker with the time
# note the -r MQTT flag that makes the values persistent

def connect(mqtt_client, userdata, flags, rc):
    print("Connected to MQTT Broker!")
    print("Flags: {0} RC: {1}".format(flags, rc))

def disconnect(mqtt_client, userdata, rc):
    print("Disconnected from MQTT Broker!")

def subscribe(mqtt_client, userdata, topic, granted_qos):
    print("Subscribed to {0} with QOS level {1}".format(topic, granted_qos))

def unsubscribe(mqtt_client, userdata, topic, pid):
    print("Unsubscribed from {0} with PID {1}".format(topic, pid))

def publish(mqtt_client, userdata, topic, pid):
    print("Published to {0} with PID {1}".format(topic, pid))

def message(client, topic, message):
    if topic == "time/time":
        global mqtt_sub_time
        global mqtt_sub_time_old
        mqtt_sub_time = message
        print("mqtt_sub_time:" + mqtt_sub_time)
    if topic == "time/date2":
        global mqtt_sub_date
        mqtt_sub_date = message
        print("mqtt_sub_date:" + mqtt_sub_date)
    if topic == "time/dowa":
        global mqtt_sub_dowa
        mqtt_sub_dowa = message
        print("mqtt_sub_dowa:" + mqtt_sub_dowa)
    if topic == "time/day":
        global mqtt_sub_day
        mqtt_sub_day = message
        print("mqtt_sub_day:" + mqtt_sub_day)
    if topic == "time/moya":
        global mqtt_sub_moya
        mqtt_sub_moya = message
        print("mqtt_sub_moya:" + mqtt_sub_moya)
    if topic == "time/year2":
        global mqtt_sub_year2
        mqtt_sub_year2 = message
        print("mqtt_sub_year2:" + mqtt_sub_year2)
    if topic == "time/month":
        global mqtt_sub_month
        mqtt_sub_month = message
        print("mqtt_sub_month:" + mqtt_sub_month)
    if topic == "time/hour":
        global mqtt_sub_hour
        mqtt_sub_hour = int(message)
        print("mqtt_sub_hour:", mqtt_sub_hour)

# Set up a MiniMQTT Client
mqtt_client = MQTT.MQTT(
    broker=secrets["mqtt_broker"],
    port=secrets["mqtt_port"],
    username=secrets["mqtt_user"],
    password=secrets["mqtt_pass"],
    socket_pool=pool,
    ssl_context=ssl.create_default_context(),
)

# Connect callback handlers to mqtt_client
mqtt_client.on_connect = connect
mqtt_client.on_disconnect = disconnect
mqtt_client.on_subscribe = subscribe
mqtt_client.on_unsubscribe = unsubscribe
mqtt_client.on_publish = publish
mqtt_client.on_message = message
#print("Attempting to connect to %s" % mqtt_client.broker)
mqtt_client.connect()
#print("Subscribing to %s" % mqtt_topic)
mqtt_client.subscribe("time/time", 1)
mqtt_client.subscribe("time/date2", 1)
mqtt_client.subscribe("time/dowa", 1)
mqtt_client.subscribe("time/day", 1)
mqtt_client.subscribe("time/moya", 1)
mqtt_client.subscribe("time/year2", 1)
mqtt_client.subscribe("time/month", 1)
mqtt_client.subscribe("time/hour", 1)
#print("Publishing to %s" % mqtt_topic)
#mqtt_client.publish(mqtt_topic, "Hello Broker!")
#print("Unsubscribing from %s" % mqtt_topic)
#mqtt_client.unsubscribe(mqtt_topic)
#print("Disconnecting from %s" % mqtt_client.broker)
#mqtt_client.disconnect()
# MQTT end

# Use this topic if you'd like to connect to a standard MQTT broker
#mqtt_topic = "time/time"
# Adafruit IO-style Topic
# Use this topic if you'd like to connect to io.adafruit.com
#mqtt_topic = secrets["aio_username"] + '/feeds/temperature'
# Define callback methods which are called when events occur
# MQTT end

###########
### NTP ###
###########
def get_ntp_time(pool):
    packet = bytearray(48)
    packet[0] = 0b00100011

    for i in range(1, len(packet)):
        packet[i] = 0

    with pool.socket(pool.AF_INET, pool.SOCK_DGRAM) as sock:
        sock.settimeout(None)
        sock.sendto(packet, (ntp_server, ntp_port))
        sock.recv_into(packet)
        destination = time.monotonic_ns()

    seconds = struct.unpack_from("!I", packet, offset=len(packet) - 8)[0]
    ntp_localtime_tz = seconds - ntp_time_correction + tz_offset
    # compensate is there's more than 1s since we retrieved the time
    return time.localtime(
        ntp_localtime_tz + (time.monotonic_ns() - destination) // 1_000_000_000
    )

try:
    now = get_ntp_time(pool)
except:
    print("NTP Broken")
rtc.RTC().datetime = now
print("  NTP", now)
# NTP end

################
### Graphics ###
################
magtag.graphics.set_background(0xffffff)
mid_x = magtag.graphics.display.width // 2 - 1
midl_x = magtag.graphics.display.width // 2 // 2 + 9
# text top large clock
magtag.add_text(
    text_font="/Digital-7-77.bdf",
    text_color=(0x000000),
    #text_position=(mid_x,6),
    text_position=(midl_x, 6),
    text_anchor_point=(0.485, 0.20),  # centre for scale 2
    text_scale=1,
    is_data=False,
)
# text bot large
magtag.add_text(
    text_font="/Digital-7-77.bdf",
    text_color=(0x000000),
    text_position=(mid_x, 68),
    text_anchor_point=(0.485, 0.20),  # centre for scale 2
    text_scale=1,
    is_data=False,
)
# text small a
magtag.add_text(
    text_font="/Digital-7-38.bdf",
    text_color=(0x000000),
    text_position=(((magtag.graphics.display.width // 4) * 3) + 12, 5),
    text_anchor_point=(0.5, 0.20),  # centre for scale 2
    text_scale=1,
    is_data=False,
)
# text small b
magtag.add_text(
    text_font="/Digital-7-38.bdf",
    text_color=(0x000000),
    text_position=(((magtag.graphics.display.width // 4) * 3) + 12, 35),
    text_anchor_point=(0.5, 0.20),  # centre for scale 2
    text_scale=1,
    is_data=False,
)
# text small c
magtag.add_text(
    text_font="/Digital-7-38.bdf",
    text_color=(0x000000),
    text_position=(mid_x, 67),
    text_anchor_point=(0.485, 0.20),  # centre for scale 2
    text_scale=1,
    is_data=False,
)
# text small d
magtag.add_text(
    text_font="/Digital-7-38.bdf",
    text_color=(0x000000),
    text_position=(mid_x, 97),
    text_anchor_point=(0.485, 0.20),  # centre for scale 2
    text_scale=1,
    is_data=False,
)
# text micro a
magtag.add_text(
    text_font="/ThinPixel-7-20.bdf",
    text_color=(0x000000),
    text_position=(1, 119),
    text_anchor_point=(0, 1),  # centre for scale 2
    text_scale=1,
    is_data=False,
)
# text micro b - Batt voltage
magtag.add_text(
    #text_font="/SmallestPixel-7-10.bdf",
    text_font="/ThinPixel-7-20.bdf",
    text_color=(0x000000),
    text_position=(magtag.graphics.display.width + 1, 120),
    text_anchor_point=(1, 1),  # centre for scale 2
    text_scale=1,
    is_data=False,
)
if invert_enable == 1:
    if mqtt_sub_hour <= invert_start and mqtt_sub_hour >= invert_stop:
        #print("DayVision")
        magtag.graphics.set_background(0xffffff)
        magtag.set_text_color(0x000000, index=0)
        magtag.set_text_color(0x000000, index=1)
        magtag.set_text_color(0x000000, index=2)
        magtag.set_text_color(0x000000, index=3)
        magtag.set_text_color(0x000000, index=4)
        magtag.set_text_color(0x000000, index=5)
        magtag.set_text_color(0x000000, index=6)
        magtag.set_text_color(0x000000, index=7)
    else:
        #print("NightVision")
        magtag.graphics.set_background(0x000000)
        magtag.set_text_color(0xffffff, index=0)
        magtag.set_text_color(0xffffff, index=1)
        magtag.set_text_color(0xffffff, index=2)
        magtag.set_text_color(0xffffff, index=3)
        magtag.set_text_color(0xffffff, index=4)
        magtag.set_text_color(0xffffff, index=5)
        magtag.set_text_color(0xffffff, index=6)
        magtag.set_text_color(0xffffff, index=7)
# Graphics end

# check if time.monotonic() was longer than 60s ago
def time_mono_sixty():
    global time_mono_last
    if time.monotonic() - time_mono_last > 60:
        time_mono_last = time.monotonic()
        return True
    return False

# convert bytes to KB/MB/GB etc.
def convert_bytes(num):
    step_unit = 1024
    for x in ['bytes', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']:
        if num < step_unit:
            return "%.3f %s" % (num, x)
        num /= step_unit

##################################################
### Make a noise to signify starting main loop ###
##################################################
magtag.peripherals.speaker_disable = False
magtag.peripherals.play_tone(1046.50, 0.125)  # C6
#time.sleep(0.125)
magtag.peripherals.play_tone(1318.51, 0.125)  # E6
magtag.peripherals.speaker_disable = True

wd.feed()  # Feed watchdog

while True:
    try:
        mqtt_client.loop()
    except (ValueError, RuntimeError) as e:
        print("Failed to get data, retrying\n", e)
        wifi.reset()
        mqtt_client.reconnect()
        continue

    try:
        data, addr = udp_sock.recvfrom_into(packet)  # Receive UDP data
        print("RX: Size:", data, "Data:", str(packet[:data]), "\nDecoded:", packet[:data - 1].decode("utf-8")) # Print received data
        exec(str(packet[:data - 1].decode("utf-8"))) # Execute received data
    except Exception as err:
        if "EAGAIN" in str(err): # If no data received, skip
            pass
        else: # If error, print
            print("Error:", err)

    if time_mono_sixty() == True: # If more than 60s since last update
        print("  Sixty seconds passed...")
        if invert_enable == 1:
            if mqtt_sub_hour <= invert_start and mqtt_sub_hour >= invert_stop:
                #print("DayVision")
                magtag.graphics.set_background(0xffffff)
                magtag.set_text_color(0x000000, index=0)
                magtag.set_text_color(0x000000, index=1)
                magtag.set_text_color(0x000000, index=2)
                magtag.set_text_color(0x000000, index=3)
                magtag.set_text_color(0x000000, index=4)
                magtag.set_text_color(0x000000, index=5)
                magtag.set_text_color(0x000000, index=6)
                magtag.set_text_color(0x000000, index=7)
            else:
                #print("NightVision")
                magtag.graphics.set_background(0x000000)
                magtag.set_text_color(0xffffff, index=0)
                magtag.set_text_color(0xffffff, index=1)
                magtag.set_text_color(0xffffff, index=2)
                magtag.set_text_color(0xffffff, index=3)
                magtag.set_text_color(0xffffff, index=4)
                magtag.set_text_color(0xffffff, index=5)
                magtag.set_text_color(0xffffff, index=6)
                magtag.set_text_color(0xffffff, index=7)
        try:
            print("    TimeNTP:", get_ntp_time(pool))
            print("    TimeRTC:", rtc.RTC().datetime)
        except:
            print("NTP BROKE")

    if mqtt_sub_time != mqtt_sub_time_old: # If time changed do stuff
        magtag.set_text(mqtt_sub_time, index=0, auto_refresh=False)
        magtag.set_text("", index=1, auto_refresh=False)
        magtag.set_text(mqtt_sub_dowa + " " + mqtt_sub_moya, index=2, auto_refresh=False)
        magtag.set_text(mqtt_sub_day + "." + mqtt_sub_month + "." + mqtt_sub_year2, index=3, auto_refresh=False,)
        magtag.set_text("Light: " + str(magtag.peripherals.light), index=4, auto_refresh=False)
        magtag.set_text(str(round(magtag.peripherals.battery, 2)) + "v", index=5, auto_refresh=False)
        magtag.set_text("L:" + str(int(round(100 * (magtag.peripherals.light - 556) / (52487 - 556), 0))) + "% " + str(magtag.peripherals.light), index=6, auto_refresh=False)
        magtag.set_text(str(round(magtag.peripherals.battery, 2)) + "v", index=7, auto_refresh=False)
        if int(round(100 * (magtag.peripherals.battery - 3.71) / (4.175 - 3.71), 0)) > 100:
            magtag.set_text("Chg " + str(int(round(100 * (magtag.peripherals.battery - 3.71) / (4.175 - 3.71), 0))) + "% " + str(round(magtag.peripherals.battery, 2)) + "v", index=7, auto_refresh=True)
        else:
            magtag.set_text(str(int(round(100 * (magtag.peripherals.battery - 3.71) / (4.175 - 3.71), 0))) + "% " + str(round(magtag.peripherals.battery, 2)) + "v", index=7, auto_refresh=True)
        print("  Updating screen...")

    if mqtt_sub_hour != mqtt_sub_hour_old and hour_chime == 1: # If hour changed do stuff
        if mqtt_sub_hour >= hour_chime_start and mqtt_sub_hour <= hour_chime_stop:
            print("  Hourchime!")
            magtag.peripherals.speaker_disable = False
            magtag.peripherals.play_tone(4096, 0.125)  # C8 minus 38 cents
            time.sleep(0.125)
            magtag.peripherals.play_tone(4096, 0.125)  # C8 minus 38 cents
            magtag.peripherals.speaker_disable = True
    mqtt_sub_time_old = mqtt_sub_time
    mqtt_sub_hour_old = mqtt_sub_hour

    if lis.tapped == True and tap_enable == 1: # If tap detected do stuff
        print("  LIS3DH tapped!")
        if tap_counter == 0:
            print("    Tap Beep!")
            magtag.peripherals.speaker_disable = False
            magtag.peripherals.play_tone(4096, 0.125)
            magtag.peripherals.speaker_disable = True
        tap_counter = tap_duration
        print("    Tap Lights on!")
        magtag.peripherals.neopixels.fill((1, 1, 1))
    if tap_counter > 0:
        tap_counter -= 1
        print("  Tap timer:", tap_counter)
        if tap_counter == 0:
            print("    Tap Lights off!")
            magtag.peripherals.neopixels.fill((0, 0, 0))

    ### Prepare for sleep ###
    #print("  RAM Used:", convert_bytes(gc.mem_alloc()))
    #print("  RAM Free:", convert_bytes(gc.mem_free()))
    print("  Light:", magtag.peripherals.light)
    print("  Batt: ", magtag.peripherals.battery, "v", sep='')
    #print("Sleep:", mqtt_sub_time, "%.3f" %(supervisor.ticks_ms() / 1_000))
    #print("Sleep:", mqtt_sub_time, "%.3f" %((supervisor.ticks_ms() / 1_000) - (time.monotonic_ns() / 1_000_000_000)))
    print("Sleep:", mqtt_sub_time, "%.3f" %(time.monotonic_ns() / 1_000_000_000), "\n")
    led.value = False  # Turn off LED to signify sleep
    gc.collect()  # Force garbage collection
    wd.feed()  # Feed watchdog
    #magtag.enter_light_sleep(sleep_time) # Turns off NeoPixels and Speaker
    time.sleep(sleep_time)
    ### Awaken ###
    wd.feed()  # Feed watchdog
    #print("Awake:", mqtt_sub_time, "%.3f" %(supervisor.ticks_ms() / 1_000))
    #print("Awake:", mqtt_sub_time, "%.3f" %((supervisor.ticks_ms() / 1_000) - (time.monotonic_ns() / 1_000_000_000)))
    print("Awake:", mqtt_sub_time, "%.3f" %(time.monotonic_ns() / 1_000_000_000))
    led.value = True  # Turn on LED to signify awake
# EOF
