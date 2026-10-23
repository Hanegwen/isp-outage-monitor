# Runs on the PC every 5 minutes (scheduled task "Comcast monitor - Steam report", see pc/install-task.ps1).
# Reads Steam's download log and copies a small summary to the Pi, so the data-usage monitor can tell a
# Steam game update apart from unexplained heavy use.
#
# Summary (JSON) -> pi:/var/lib/comcast-monitor/steam.json
#   generated   unix time this file was written
#   events      [[unix time, bytes], ...]  bytes Steam downloaded, one entry per log "stats" line, last 48 h
#   apps        [[unix time, "game name"], ...]  updates started in the last 48 h
#   downloading true if Steam's latest app state says it is downloading right now
param([string]$PiHost = "pi", [string]$SteamDir = "C:\Program Files (x86)\Steam")

$ErrorActionPreference = "Stop"
$since = (Get-Date).AddHours(-48)
$logs = @("$SteamDir\logs\content_log.previous.txt", "$SteamDir\logs\content_log.txt") | Where-Object { Test-Path $_ }

function UnixTime([datetime]$t) { [int64]([DateTimeOffset]$t).ToUnixTimeSeconds() }

# Game names: appmanifest_<id>.acf in every Steam library folder
$names = @{}
$libraries = @($SteamDir) + (Select-String -Path "$SteamDir\steamapps\libraryfolders.vdf" -Pattern '"path"\s+"(.+)"' |
    ForEach-Object { $_.Matches[0].Groups[1].Value -replace '\\\\', '\' })
foreach ($lib in ($libraries | Select-Object -Unique)) {
    Get-ChildItem "$lib\steamapps\appmanifest_*.acf" -ErrorAction SilentlyContinue | ForEach-Object {
        $m = Select-String -Path $_.FullName -Pattern '"name"\s+"(.+)"' | Select-Object -First 1
        if ($m) { $names[($_.BaseName -replace 'appmanifest_', '')] = $m.Matches[0].Groups[1].Value }
    }
}

$events = [System.Collections.Generic.List[object]]::new()
$apps = [System.Collections.Generic.List[object]]::new()
$downloading = $false
$stamp = '^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] '
foreach ($line in (Get-Content $logs)) {
    if ($line -notmatch $stamp) { continue }
    $t = [datetime]::ParseExact($Matches[1], "yyyy-MM-dd HH:mm:ss", $null)
    if ($t -lt $since) { continue }
    # "(Invalid, 0)" stats lines repeat the other servers' total, so only CDN and SteamCache lines count
    if ($line -match 'stats: \((CDN|SteamCache),.*?: (\d+) Bytes') {
        if ([int64]$Matches[2] -gt 0) { $events.Add(@((UnixTime $t), [int64]$Matches[2])) }
    } elseif ($line -match 'AppID (\d+) update started') {
        $id = $Matches[1]
        $apps.Add(@((UnixTime $t), $(if ($names[$id]) { $names[$id] } else { "Steam app $id" })))
    } elseif ($line -match 'AppID \d+ App update changed : (.*)') {
        $downloading = $Matches[1] -match 'Downloading'
    } elseif ($line -match 'AppID \d+ state changed : (.*)' -and $Matches[1] -notmatch 'Update Running') {
        $downloading = $false
    }
}

$json = [ordered]@{ generated = (UnixTime (Get-Date)); pc = $env:COMPUTERNAME; events = $events; apps = $apps;
                    downloading = $downloading } | ConvertTo-Json -Compress -Depth 4
$json | & "$env:SystemRoot\System32\OpenSSH\ssh.exe" -o BatchMode=yes -o ConnectTimeout=15 $PiHost `
    "cat > /var/lib/comcast-monitor/steam.json.tmp && mv /var/lib/comcast-monitor/steam.json.tmp /var/lib/comcast-monitor/steam.json"
if ($LASTEXITCODE -ne 0) { throw "could not copy the Steam summary to $PiHost (ssh exit $LASTEXITCODE)" }
