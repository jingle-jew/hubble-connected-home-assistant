[CmdletBinding()]
param(
    [string]$AdbPath,
    [string]$Serial,
    [string]$CameraIp,
    [switch]$CopyToClipboard
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$PackageName = "com.fivegencare.com.motorola.nursery"
$OrbwebAccount = "orbweb_user"
$ScriptVersion = "4.0"

function Resolve-Adb {
    if ($AdbPath) {
        if (-not (Test-Path -LiteralPath $AdbPath -PathType Leaf)) {
            throw "adb was not found at: $AdbPath"
        }
        return (Resolve-Path -LiteralPath $AdbPath).Path
    }

    $command = Get-Command adb.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $candidates = @(
        (Join-Path (Get-Location) "adb.exe"),
        "C:\platform-tools\adb.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw "adb.exe was not found. Run this script from platform-tools or use -AdbPath C:\path\to\adb.exe."
}

function Invoke-Adb {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )

    $allArguments = @()
    if ($script:SelectedSerial) {
        $allArguments += @("-s", $script:SelectedSerial)
    }
    $allArguments += $Arguments
    & $script:AdbExecutable @allArguments
}

function Get-LastGroupValue {
    param(
        [string]$Text,
        [string]$Pattern,
        [string]$GroupName
    )

    $found = [regex]::Matches(
        $Text,
        $Pattern,
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase -bor
            [System.Text.RegularExpressions.RegexOptions]::Singleline
    )
    if ($found.Count -eq 0) {
        return $null
    }
    return $found[$found.Count - 1].Groups[$GroupName].Value.Trim()
}

function ConvertFrom-UrlCredential {
    param([string]$Value)

    if (-not $Value) {
        return $null
    }
    try {
        return [Uri]::UnescapeDataString($Value)
    }
    catch {
        return $Value
    }
}

try {
    $script:AdbExecutable = Resolve-Adb
    $script:SelectedSerial = $Serial

    if (-not $Serial) {
        $deviceOutput = & $script:AdbExecutable devices
        $connected = @(
            $deviceOutput |
                Select-Object -Skip 1 |
                Where-Object { $_ -match "^([^\s]+)\s+device$" } |
                ForEach-Object { ([regex]::Match($_, "^([^\s]+)")).Groups[1].Value }
        )

        if ($connected.Count -eq 0) {
            throw "No authorized Android device was found. Enable USB debugging, reconnect the phone, and accept the authorization prompt."
        }
        if ($connected.Count -gt 1) {
            throw "More than one Android device is connected. Run the script again with -Serial DEVICE_SERIAL (see: adb devices)."
        }
        $script:SelectedSerial = $connected[0]
    }

    $state = (Invoke-Adb -Arguments @("get-state") 2>&1 | Out-String).Trim()
    if ($state -ne "device") {
        throw "Android device '$script:SelectedSerial' is not ready (state: $state)."
    }

    $packagePath = (
        Invoke-Adb -Arguments @("shell", "pm", "path", $PackageName) 2>&1 |
            Out-String
    ).Trim()
    if ($packagePath -notmatch "package:") {
        throw "The Motorola Nursery app ($PackageName) is not installed on the connected phone."
    }

    Write-Host "Motorola VM85CONNECT setup helper v$ScriptVersion" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "This reads the official app's diagnostic output from your own phone."
    Write-Host "It does not contact the camera or save a log file."
    Write-Host ""

    $marker = "HA_VM85_$([Guid]::NewGuid().ToString('N'))"
    Invoke-Adb -Arguments @(
        "shell", "log", "-t", "HA_VM85_SETUP", $marker
    ) 2>&1 | Out-Null

    Write-Host "On the phone:" -ForegroundColor Yellow
    Write-Host "  1. Open the Motorola Nursery app manually."
    Write-Host "  2. Open the VM85CONNECT camera."
    Write-Host "  3. Wait until the live video is visible."
    Write-Host "  4. Return here and press Enter."
    [void][Console]::ReadLine()

    Write-Host "Reading the relevant connection details..."
    $logText = (
        Invoke-Adb -Arguments @("logcat", "-d", "-v", "threadtime") 2>&1 |
            Out-String
    )
    $markerPosition = $logText.LastIndexOf($marker, [StringComparison]::Ordinal)
    if ($markerPosition -ge 0) {
        $logText = $logText.Substring($markerPosition)
    }

    $sid = Get-LastGroupValue $logText `
        'startM2M\s+sid=(?<sid>[A-Za-z0-9._-]+)\s+china=' 'sid'

    $orbwebDetails = [regex]::Matches(
        $logText,
        'startM2M:\s*executing.*?sid:(?<sid>\S+)\s+password:(?<password>\S+)\s+initReady:',
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase -bor
            [System.Text.RegularExpressions.RegexOptions]::Singleline
    )
    $orbwebPassword = $null
    if ($orbwebDetails.Count -gt 0) {
        $latestOrbweb = $orbwebDetails[$orbwebDetails.Count - 1]
        if (-not $sid) {
            $sid = $latestOrbweb.Groups['sid'].Value.Trim()
        }
        $orbwebPassword = $latestOrbweb.Groups['password'].Value.Trim()
    }

    # Android's JSON log escapes URL slashes as \/. Normalizing those makes the
    # URL easier and safer to parse without relying on a particular local port.
    $normalizedLog = $logText.Replace('\/', '/')
    $rtspDetails = [regex]::Matches(
        $normalizedLog,
        'rtsp://(?<username>[^:/\s"]+):(?<password>.+?)@127\.0\.0\.1:(?<port>\d+)/',
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
    )
    $rtspUsername = $null
    $rtspPassword = $null
    if ($rtspDetails.Count -gt 0) {
        $latestRtsp = $rtspDetails[$rtspDetails.Count - 1]
        $rtspUsername = ConvertFrom-UrlCredential $latestRtsp.Groups['username'].Value
        $rtspPassword = ConvertFrom-UrlCredential $latestRtsp.Groups['password'].Value
    }

    if (-not $CameraIp) {
        $CameraIp = Get-LastGroupValue $logText `
            '(?:connect|connected)\s+to\s+(?<ip>(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}))\s*:\s*77' 'ip'
    }

    $missing = @()
    if (-not $sid) { $missing += "Orbweb SID" }
    if (-not $orbwebPassword) { $missing += "Orbweb device password" }
    if (-not $rtspUsername) { $missing += "RTSP username" }
    if (-not $rtspPassword) { $missing += "RTSP password" }

    if ($missing.Count -gt 0) {
        Write-Host ""
        Write-Host "Not all values were found:" -ForegroundColor Red
        foreach ($name in $missing) {
            Write-Host "  - $name"
        }
        Write-Host ""
        Write-Host "Make sure the live stream is visible in the app, then run the script again."
        exit 2
    }

    $result = [ordered]@{
        "Camera LAN IP address" = if ($CameraIp) { $CameraIp } else { "NOT DETECTED - check your router" }
        "Orbweb SID" = $sid
        "Orbweb account" = $OrbwebAccount
        "Orbweb device password" = $orbwebPassword
        "RTSP username" = $rtspUsername
        "RTSP password" = $rtspPassword
    }

    Write-Host ""
    Write-Host "Values for the Home Assistant config flow" -ForegroundColor Green
    Write-Host "Keep these values private; they provide access to your camera." -ForegroundColor Yellow
    Write-Host ""
    foreach ($item in $result.GetEnumerator()) {
        Write-Host ("{0,-28}: {1}" -f $item.Key, $item.Value)
    }
    Write-Host ""
    Write-Host "The VM85 profile supplies RTSP port 6667 and path /blinkhd automatically."

    if ($CopyToClipboard) {
        $clipboardText = ($result.GetEnumerator() | ForEach-Object {
            "{0}: {1}" -f $_.Key, $_.Value
        }) -join [Environment]::NewLine
        Set-Clipboard -Value $clipboardText
        Write-Host "The six config-flow values were copied to the Windows clipboard." -ForegroundColor Green
    }
}
catch {
    Write-Host ""
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 1
}
