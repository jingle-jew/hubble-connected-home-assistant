"""Constants for the Hubble Connected integration."""

from __future__ import annotations

DOMAIN = "hubble_connected"

CONF_LOCAL_CAMERAS = "local_cameras"
CONF_CLOUD_LOGIN = "cloud_login"
CONF_CLOUD_PASSWORD = "cloud_password"
CONF_CLOUD_CAMERA_IDS = "cloud_camera_ids"
CONF_PROVIDER = "provider"
CONF_ORBWEB_HOST = "orbweb_host"
CONF_ORBWEB_SID = "orbweb_sid"
CONF_ORBWEB_ACCOUNT = "orbweb_account"
CONF_ORBWEB_PASSWORD = "orbweb_password"
CONF_RTSP_USERNAME = "rtsp_username"
CONF_RTSP_PASSWORD = "rtsp_password"

PROVIDER_HUBBLE = "hubble"
PROVIDER_MANUAL_VM85 = "manual_vm85"
VM85_ORBWEB_ACCOUNT = "orbweb_user"

DEFAULT_NAME = "Hubble camera"
DEFAULT_LOCAL_CAMERAS = ""
DEFAULT_CLOUD_CAMERA_IDS = ""
DEFAULT_SCAN_INTERVAL = 60

# Verified legacy Hubble LAN RTSP service. Discovery only sends OPTIONS and
# creates an entity when the camera itself answers with RTSP.
DIRECT_RTSP_PORT = 6667
DIRECT_RTSP_PATH = "/blinkhd"
DIRECT_RTSP_USERNAME = "user"
DIRECT_RTSP_PASSWORD = "pass"

PLATFORMS = ["binary_sensor", "camera", "number", "select", "sensor", "switch"]

BITRATE_OPTIONS = (100, 200, 300, 400, 600, 800, 1000)
ORBWEB_3667_BITRATE_OPTIONS = (100, 300, 600, 1000)
NIGHT_VISION_OPTIONS = {
    0: "auto",
    1: "on",
    2: "off",
}

MODEL_NAMES = {
    "1667": "MBP167",
}
