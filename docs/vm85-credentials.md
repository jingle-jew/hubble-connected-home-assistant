# Motorola VM85CONNECT manual setup

### Motorola VM85CONNECT (manual)

The manual VM85 flow is intended for owners who can view their own camera in
the official Motorola Nursery Android app. It asks for:

- the camera name and private LAN IPv4 address;
- its device-specific Orbweb SID and Orbweb password;
- the Orbweb account name used by the camera;
- its RTSP username and password.

The verified profile uses an owner-authenticated Orbweb tunnel to camera port
6667 and requests `/blinkhd`. The stream contains H.264 video and G.711 A-law
audio. Hardware version 1.2.03 with firmware 1.6.45 has been tested.

The camera must be on the same LAN as Home Assistant. Connection details are
stored in Home Assistant config-entry storage and are never included in
diagnostics. Protect the Home Assistant configuration directory and backups,
and never post these values in an issue or pull request. Automatic retrieval
through a Motorola/5GenCare account is outside the scope of this manual flow.

#### Install Python and ADB

Use [`extract_vm85_credentials.py`](../tools/extract_vm85_credentials.py) on
Linux, macOS, or Windows. The script is standalone: Python 3.10 or newer and
ADB are required; Home Assistant and Python packages are not needed on this
computer. Download this repository's source archive and extract it, or clone
it. Run the examples below from the repository root in a private terminal.

Install Python from your distribution or [python.org](https://www.python.org/downloads/).
On Windows, the examples use the Python launcher (`py -3`); `python` is also
suitable if it selects Python 3.10 or newer.

Install ADB using the appropriate option:

- **Fedora:** `sudo dnf install android-tools`.
  [Fedora package](https://packages.fedoraproject.org/pkgs/android-tools/)
- **Ubuntu/Debian:** `sudo apt update`, then
  `sudo apt install adb android-sdk-platform-tools-common`.
  [Ubuntu package](https://packages.ubuntu.com/adb) ·
  [Debian package](https://packages.debian.org/stable/adb)
- **macOS with Homebrew:** `brew install --cask android-platform-tools`.
  [Homebrew package](https://formulae.brew.sh/cask/android-platform-tools)
- **Windows:** download Google's
  [SDK Platform-Tools for Windows](https://developer.android.com/tools/releases/platform-tools),
  extract the ZIP (for example, to `C:\platform-tools`), then add that directory
  to PATH or supply `--adb-path` as shown below. Install the phone manufacturer's
  [ADB USB driver](https://developer.android.com/studio/run/oem-usb) if required.

Google also supplies standalone Platform-Tools downloads for Linux and macOS.
Check `adb version` (`C:\platform-tools\adb.exe version` when using an explicit
Windows path). The helper discovers ADB in PATH; unlike the legacy PowerShell
helper, it does not search the current directory or `C:\platform-tools`
automatically.

#### Prepare the Android phone

Use only a phone, account, and camera you own or are authorized to access.
Install the official **Motorola Nursery** Android app
(`com.fivegencare.com.motorola.nursery`), sign in normally, and confirm that
its camera live view works. The phone and camera should share the camera's
LAN so the app can log its local address.

1. Enable Android **Developer options** (usually tap **Build number** seven
   times under **Settings > About phone > Software information**).
2. Enable **USB debugging** in Developer options.
3. Connect an unlocked phone with a USB data cable and accept its **Allow USB
   debugging** prompt for your trusted computer.
4. Run `adb devices`. The selected phone must appear with state `device`.

Menu names vary by manufacturer. See Google's
[Android device setup](https://developer.android.com/studio/run/device).
No root access, app patching, firmware modification, or authentication bypass
is required. The helper checks the installed app, writes one diagnostic log
marker, and reads logcat; it does not clear logs or install or launch apps.

#### Run the helper

Linux and macOS:

```sh
python3 tools/extract_vm85_credentials.py
```

Windows PowerShell:

```powershell
py -3 .\tools\extract_vm85_credentials.py --adb-path "C:\platform-tools\adb.exe"
```

For multiple devices, select one explicitly using its serial from `adb devices`.
You may also provide the camera's private LAN IPv4 address from your router:

```sh
python3 tools/extract_vm85_credentials.py --serial DEVICE_SERIAL --camera-ip 192.168.1.50
python3 tools/extract_vm85_credentials.py --adb-path "/path/to/platform-tools/adb"
```

```powershell
py -3 .\tools\extract_vm85_credentials.py --serial DEVICE_SERIAL --camera-ip 192.168.1.50
```

Close Motorola Nursery when instructed, then press Enter to create a fresh
capture marker. Open the app manually and view **only the target camera**.
Wait for live video, return to the terminal, and press Enter again. Other
camera sessions may make the capture ambiguous; the helper refuses conflicting
SID, Orbweb password, or RTSP credential values instead of mixing them.
Repeated identical credentials on different local tunnel ports are accepted.

The helper prints the six Home Assistant values: camera LAN address, Orbweb
SID, Orbweb account (`orbweb_user`), Orbweb device password, RTSP username, and
RTSP password. The VM85 profile supplies port `6667` and path `/blinkhd`.
If the IP is not detected, the credentials are still shown: find the IP in
your router or retry with `--camera-ip`. Invalid/non-private IPv4 addresses
are rejected; multiple logged LAN addresses require an explicit IP.

Raw Android logs are held in memory only, never written to disk or uploaded.
The helper does not contact the camera or an external service. The official
app and ADB retain their own normal behavior; the app may use its cloud
services while opening the live view. Do not redirect logcat or helper output
into files, terminal recordings, shared sessions, issues, or diagnostics.
The displayed values grant camera access and remain in terminal scrollback.

Clipboard copying is **off by default**. Add `--copy-to-clipboard` only if you
explicitly want to copy all six values. This uses `pbcopy` on macOS, Windows
PowerShell on Windows, or `wl-copy`, `xclip`, or `xsel` on Linux. Linux copying
requires a graphical clipboard session and one of those tools; extraction
works without them, including in a headless terminal. Clipboard failures do
not hide the already displayed values. Disable clipboard history/cloud sync
before copying secrets and clear the clipboard after setup. Revoke USB
debugging authorization or disable USB debugging afterward if no longer needed.

Exit status is `0` on success (including an undetected IP), `2` for incomplete
credentials or invalid command-line usage, and `1` for other helper failures.

#### Legacy Windows helper and compatibility limits

The existing [`extract_vm85_credentials.ps1`](../tools/extract_vm85_credentials.ps1)
is unchanged and remains available for existing Windows users:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\extract_vm85_credentials.ps1 `
  -AdbPath C:\platform-tools\adb.exe
```

Its existing `-Serial`, `-CameraIp`, and opt-in `-CopyToClipboard` options still
apply. Python is the preferred implementation for future extraction changes;
a future PowerShell launcher could delegate to it after a separate migration
that accounts for existing Windows users without Python.

The original VM85CONNECT report tested hardware `1.2.03`, firmware `1.6.45`.
The Python helper preserves that log format and is covered by synthetic tests
on Linux; it has **not been tested with a physical phone/camera or executed
on macOS or Windows**. App versions that stop logging these fields cannot be
supported by this extraction method without new evidence.

**VM65X Connect is unverified.** The
[VM65X report (issue #3)](https://github.com/jingle-jew/hubble-connected-home-assistant/issues/3)
uses a different app (`com.hubble.care`) with email OTP authentication. It does
not establish support for Motorola Nursery logs, VM85 credentials, or this
manual stream profile. This helper neither implements OTP nor targets that app.

Automatic Motorola/5GenCare account login and email-OTP handling are not
implemented. A temporary camera share code is not a replacement for the
camera-specific owner credentials used by this flow.

## Troubleshooting

### The VM85 helper cannot find all values

Close Motorola Nursery and run the helper again. Open the app and one
camera's live view when the helper prompts you. Wait until video
is visible before pressing Enter. The phone must be authorized in `adb devices`.
If only the LAN address is missing, obtain it from the router or pass
`--camera-ip` explicitly (`-CameraIp` for PowerShell).

### ADB does not detect or authorize the phone

- **No devices:** use a data-capable cable and another USB port, unlock the
  phone, and confirm USB debugging is enabled. On Windows, check the OEM ADB
  driver; selecting file-transfer USB mode may help with device detection.
- **`unauthorized`:** accept the RSA authorization prompt on the unlocked
  phone. If it does not appear, revoke USB debugging authorizations in
  Developer options and reconnect, then authorize the trusted computer again.
- **`offline`:** reconnect the phone. If necessary, run `adb kill-server`,
  then `adb start-server` and `adb devices`; this interrupts other ADB sessions.
- **Linux permissions:** use your distribution's Android USB/udev rules.
  On Ubuntu, install `android-sdk-platform-tools-common`; if prompted about
  `plugdev`, add your account with `sudo usermod -aG plugdev "$LOGNAME"`, then
  log out and back in. Debian's `adb` package depends on Android udev rules.
  Do not run this helper as root to work around permissions.
- **Several devices:** pass `--serial DEVICE_SERIAL`; an explicit serial must
  be connected and authorized. Use the same ADB executable for diagnostics and
  the helper when multiple Platform-Tools versions are installed.
- **App absent:** Motorola Nursery must be installed in the active Android
  user/profile. Another Motorola/Hubble app is not interchangeable.
- **Marker missing or command failure:** reconnect, check `adb devices`, and
  restart the helper. Busy log buffers can evict the marker; complete the live
  view step promptly. No raw command output is printed because it may be private.
