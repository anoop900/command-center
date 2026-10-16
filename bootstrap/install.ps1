<#
Command Center installer for Windows 10/11 (PowerShell 5.1 or later). No admin rights needed, except that Windows may ask permission once to install LibreOffice.
Most people should double-click "Install Command Center.cmd" in the folder above this one; it runs this script.
  .\bootstrap\install.ps1                 install from the folder this script sits in
  .\bootstrap\install.ps1 -Source <path>  install from another folder (a clone or an unzipped download)
  .\bootstrap\install.ps1 -DryRun         print what it would do and change nothing
Steps: find Python 3.11+ (install it with winget for this user only if missing), install pipx for this user,
install cc with pipx, then run `cc start --reinstall`, which sets Command Center to run in the background, starts it
and opens the wizard in your browser. The window can then be closed.
It does not download or run anything except through winget and pip, and it edits PATH only through `pipx ensurepath`.
The file is plain ASCII on purpose: Windows PowerShell 5.1 misreads other characters in a script without a BOM.
#>
param(
    [string]$Source = (Split-Path -Parent $PSScriptRoot),
    [switch]$DryRun,
    [int]$AssumeFreeMB = -1   # tests only: pretend this much free disk space
)
$ErrorActionPreference = 'Stop'
$script:Log = New-Object System.Collections.Generic.List[string]
$script:Total = 5
$script:Clock = [System.Diagnostics.Stopwatch]::StartNew()
function Elapsed($sw) { $t = $sw.Elapsed; '{0}:{1:00}' -f [int][math]::Floor($t.TotalMinutes), $t.Seconds }

function Say($m, $color) {
    if ($color) { Write-Host $m -ForegroundColor $color } else { Write-Host $m }
    $script:Log.Add("$m")
}
function Heading($n, $what) {
    Say ''
    Say "Step $n of $script:Total - $what (time so far $(Elapsed $script:Clock))" 'Cyan'
    if (-not $DryRun) { Write-Progress -Activity 'Installing Command Center' -Status "Step $n of $script:Total - $what. Please do not close this window." -PercentComplete (($n - 1) * 100 / $script:Total) }
}
function Ok($m) { Say "  [ok] $m" 'Green' }

# Every failure ends here: say what happened in plain words, say what to do next, and keep a log the person can send.
function Save-Log([switch]$Quiet) {
    $dir = if ($env:CC_HOME) { $env:CC_HOME } else { Join-Path $HOME '.commandcenter' }
    try {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $file = Join-Path $dir 'install-log.txt'
        Set-Content -LiteralPath $file -Value $script:Log -Encoding UTF8
        if (-not $Quiet) {
            Say "A log of this run was saved to: $file" 'DarkGray'
            Say '(It lists folder names on this computer, so look it over before sharing it.)' 'DarkGray'
        }
    } catch { if (-not $Quiet) { Say 'The log could not be saved.' 'DarkGray' } }
}
function Fail($what, $fix, $detail) {
    Say ''
    Say "Something went wrong: $what" 'Red'
    if ($fix) { Say "What to do next: $fix" 'Yellow' }
    if ($detail) {
        Say 'Technical details (last lines):' 'DarkGray'
        foreach ($l in @($detail | Select-Object -Last 6)) { Say "    $l" 'DarkGray' }
    }
    Save-Log
    exit 1
}

# Run one external command quietly, keep its output for the log, and stop with a plain message if it fails.
function Step($what, [scriptblock]$do, $failWhat, $failFix) {
    if ($DryRun) { Say "WOULD: $what"; return }
    Say "  ... $what"
    Say '      Working. This can take a few minutes with no new text; please do not close this window.' 'DarkGray'
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 0
    $out = @(); $code = 0
    try { $out = @(& $do 2>&1 | ForEach-Object { "$_" }); $code = $LASTEXITCODE } catch { $out = @("$_"); $code = 1 } finally { $ErrorActionPreference = $old }
    foreach ($l in $out) { $script:Log.Add($l) }
    $script:Log.Add("      took $(Elapsed $sw)")
    if ($code -ne 0) {
        $fix = $failFix
        if (($out -join ' ') -match 'Access is denied|PermissionError') { $fix = "$fix A setting on this PC points Python at a folder it cannot read; the installer cannot fix that. Searching the web for the error text above will find it." }
        Fail $failWhat $fix $out
    }
    Say "      done in $(Elapsed $sw)" 'DarkGray'
}

# Returns "major.minor" or $null, and never throws. The Windows Store "python" alias prints to stderr and exits
# non-zero, which would otherwise stop the script under $ErrorActionPreference = 'Stop' in PowerShell 5.1.
function Get-PyVersion($exe, $extra) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $v = & $exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and "$v" -match '^\d+\.\d+$') { return "$v" }
    } catch { } finally { $ErrorActionPreference = $old }
    return $null
}

function Test-PyNew($v) { return ($v -match '^(\d+)\.(\d+)$' -and ([int]$Matches[1] -gt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 11))) }

# Candidates in order: the py launcher, python on PATH, then the folder winget installs Python into for this user
# (found by path so a stale PATH in this window does not matter).
function Get-PyCandidates {
    $list = @()
    foreach ($c in @(@('py', '-3'), @('python'))) {
        $exe = Get-Command $c[0] -ErrorAction SilentlyContinue
        if ($exe) { $list += , (@($exe.Source) + @($c | Select-Object -Skip 1)) }
    }
    $root = Join-Path $env:LOCALAPPDATA 'Programs\Python'
    foreach ($d in @(Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending)) {
        $p = Join-Path $d.FullName 'python.exe'
        if (Test-Path -LiteralPath $p) { $list += , @($p) }
    }
    return $list
}

function Find-Python {
    foreach ($c in @(Get-PyCandidates)) {
        if (Test-PyNew (Get-PyVersion $c[0] @($c | Select-Object -Skip 1))) { return , @($c) }
    }
    return $null
}

function Update-PathFromSystem {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}

# Remember what this installer added to the PC, so uninstall.ps1 removes only that and never software that was already there.
# A re-run never turns a recorded item back off.
function Save-Record($name) {
    if ($DryRun) { return }
    try {
        $dir = Join-Path $env:LOCALAPPDATA 'CommandCenter'
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $file = Join-Path $dir 'install-record.json'
        $rec = @{}
        if (Test-Path -LiteralPath $file) { (Get-Content -LiteralPath $file -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $rec[$_.Name] = $_.Value } }
        $rec[$name] = $true
        Set-Content -LiteralPath $file -Value ($rec | ConvertTo-Json) -Encoding ASCII
    } catch { $script:Log.Add("Could not save the install record: $_") }
}

Say 'Command Center installer' 'Cyan'
Say 'This takes a few minutes. Command Center installs for your user only. LibreOffice (for PDF resumes) is the one exception: Windows may ask your permission to install it.'
Say 'If anything stops, you can simply run it again; running it twice is safe.'
Say 'Please do not close this window until it says ALL DONE.' 'Yellow'

# A leftover PYTHONPATH (for example one pointing at another user's folder) can make pip crash with "Access is
# denied". Clear it for this script and the programs it starts; the system setting is not changed.
if ($env:PYTHONPATH) {
    Say 'A setting on this PC (PYTHONPATH) can confuse Python, so the installer ignores it during this run. Nothing for you to do; your setting is not changed.' 'DarkGray'
    if (-not $DryRun) { Remove-Item Env:PYTHONPATH }
}

if ($PSScriptRoot -match '\.zip[\\/]') {
    Fail 'This installer was opened from inside the zip file.' 'Close this window, right-click the zip file, choose Extract All, then open the extracted folder and double-click "Install Command Center" there.'
}
if (-not (Test-Path -LiteralPath (Join-Path $Source 'pyproject.toml'))) {
    Fail "No pyproject.toml in '$Source', so this does not look like the Command Center folder." 'Unzip the download first (right-click, Extract All) and run the installer from the unzipped folder. Advanced: pass -Source with the folder.'
}

# LibreOffice makes the PDF resume. Its installer may show a Windows permission prompt; accept it.
function Find-Soffice {
    if (Get-Command soffice -ErrorAction SilentlyContinue) { return $true }
    foreach ($r in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, (Join-Path $env:LOCALAPPDATA 'Programs'))) {
        if ($r -and (Test-Path -LiteralPath (Join-Path $r 'LibreOffice\program\soffice.exe'))) { return $true }
    }
    return $false
}

# Free space on a drive in MB, or $null when it cannot be read (for example a network folder).
function Get-FreeMB($root) {
    if ($AssumeFreeMB -ge 0) { return $AssumeFreeMB }
    try { return [int]((New-Object System.IO.DriveInfo($root)).AvailableFreeSpace / 1MB) } catch { return $null }
}
function Format-GB($mb) { if ($mb -ge 10240) { '{0:N0} GB' -f ($mb / 1024) } else { '{0:N1} GB' -f ($mb / 1024) } }

Heading 1 'Checking this PC'
$py = Find-Python
$pipxBefore = $false
if ($py) {
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & $py[0] @($py | Select-Object -Skip 1) -m pipx --version *> $null
    $pipxBefore = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $old
}
$haveSoffice = Find-Soffice
$script:Log.Add("Check: Python = $(if ($py) { ($py -join ' ') } else { 'not found' }); pipx = $pipxBefore; LibreOffice = $haveSoffice; winget = $([bool](Get-Command winget -ErrorAction SilentlyContinue))")
$script:Log.Add("Check: Command Center already installed = $([bool](Get-Command cc -ErrorAction SilentlyContinue))")
try { $script:Log.Add("Check: port 8791 in use = $([bool](Get-NetTCPConnection -LocalPort 8791 -State Listen -ErrorAction SilentlyContinue))") } catch { }

# Only the parts that are missing count: the program and its libraries, the download cache, then any missing tool
# (sizes measured on a clean PC; the Python and pipx figures are estimates), plus 20 percent headroom.
$needMB = 120 + 250
if (-not $py) { $needMB += 150 }
if (-not $pipxBefore) { $needMB += 10 }
if (-not $haveSoffice) { $needMB += 1050 }
$needMB = [int][math]::Ceiling($needMB * 1.2)
$dataDir = if ($env:CC_HOME) { $env:CC_HOME } else { Join-Path $HOME '.commandcenter' }
$roots = @($env:LOCALAPPDATA, $dataDir)
if (-not $haveSoffice -and $env:SystemDrive) { $roots += ($env:SystemDrive + '') }
$freeMB = $null
foreach ($r in @($roots | Where-Object { $_ } | ForEach-Object { [System.IO.Path]::GetPathRoot($_) } | Sort-Object -Unique)) {
    $f = Get-FreeMB $r
    if ($null -ne $f -and ($null -eq $freeMB -or $f -lt $freeMB)) { $freeMB = $f }
}
if ($null -eq $freeMB) {
    Say "This install needs about $(Format-GB $needMB) of free disk space."
} else {
    Say "This install needs about $(Format-GB $needMB) of free disk space. You have $(Format-GB $freeMB) free."
    if ($freeMB -lt $needMB) {
        Say ''
        Say 'There is not enough free disk space. Please make some space first (for example, empty the Recycle Bin or remove files you no longer need), then run the installer again. Nothing was changed.' 'Red'
        Save-Log
        exit 1
    }
}

Heading 2 'Python'
if ($py) {
    $pyV = Get-PyVersion $py[0] @($py | Select-Object -Skip 1)
    Say "  [skip] Python $pyV is already installed; nothing to do" 'Green'
} else {
    foreach ($c in @(Get-PyCandidates)) {
        $v = Get-PyVersion $c[0] @($c | Select-Object -Skip 1)
        if ($v) { Say "  Found Python $v, which is too old (3.11 or later is needed). Installing a newer one beside it; the old one is left alone."; break }
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail 'Python 3.11 or later was not found, and this PC cannot install it automatically.' 'Install Python from https://www.python.org/downloads/ (tick "Add python.exe to PATH" on the first screen), then double-click the installer again.'
    }
    Step 'winget install Python 3.12 (this user only)' { winget install --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements } 'Windows could not install Python for you.' 'Install Python from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then double-click the installer again. Check your internet connection if the download failed.'
    if ($DryRun) { Say 'WOULD: continue with the new Python'; $py = @('python') }
    else {
        Save-Record 'python'
        Update-PathFromSystem
        $py = Find-Python
        if (-not $py) { Fail 'Python was installed but this window cannot see it yet.' 'Close this window and double-click the installer again; the second run finishes the job.' }
        Ok 'Python installed'
    }
}
$script:Log.Add("Using Python: $($py -join ' ')")
$pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)

Heading 3 'Preparing the tools (pipx and LibreOffice)'
if ($pipxBefore) { Say '  [skip] pipx is already installed; nothing to install' 'Green' }
else { Step 'install pipx for this user' { & $pyExe @pyArgs -m pip install --user --quiet pipx } 'Could not set up the helper tool (pipx).' 'Check that you are online and run the installer again.' }
Step 'pipx ensurepath (adds the pipx folder to your PATH)' { & $pyExe @pyArgs -m pipx ensurepath } 'Could not finish preparing the helper tool (pipx).' 'Run the installer again.'
if (-not $DryRun) { if (-not $pipxBefore) { Save-Record 'pipx' }; Ok 'Helper tool ready' }

if ($haveSoffice) { Say '  [skip] LibreOffice already installed (makes your PDF resume); nothing to do' 'Green' }
else {
    if (-not $DryRun -and -not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail 'LibreOffice is needed for PDF resumes, and this PC cannot install it automatically.' 'Install LibreOffice from https://www.libreoffice.org/download/ then double-click the installer again.'
    }
    Step 'winget install LibreOffice (about 350 MB; needed for PDF resumes; accept the Windows permission prompt)' { winget install --id TheDocumentFoundation.LibreOffice --silent --accept-package-agreements --accept-source-agreements } 'Windows could not install LibreOffice, which makes your PDF resume.' 'Install LibreOffice from https://www.libreoffice.org/download/ then double-click the installer again. If a permission prompt appeared, choose Yes.'
    if (-not $DryRun) { Save-Record 'libreoffice'; Ok 'LibreOffice installed' }
}

Heading 4 'Installing Command Center'
Step "pipx install '$Source'" { & $pyExe @pyArgs -m pipx install --force $Source } 'Could not install Command Center.' 'Check that you are online and run the installer again.'
if ($DryRun) { Say 'WOULD: run cc start --reinstall'; exit 0 }
Ok 'Command Center installed'

Heading 5 'Starting Command Center'
$cc = $null
try {
    $bin = (& $pyExe @pyArgs -m pipx environment --value PIPX_BIN_DIR 2>$null).Trim()
    $cc = Join-Path $bin 'cc.exe'
} catch { }
if (-not $cc -or -not (Test-Path -LiteralPath $cc)) { Fail 'Command Center installed, but the program to start it was not found.' 'Close this window, open a new PowerShell and type: cc start' }
Ok 'Installed'

# Start menu entry: clicking it runs `cc open`, which starts Command Center if it is not running and opens the browser.
try {
    $lnk = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Command Center.lnk'
    $sh = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    $sh.TargetPath = $cc; $sh.Arguments = 'open'; $sh.WindowStyle = 7; $sh.Description = 'Open Command Center'
    $sh.Save()
    Ok 'Added "Command Center" to the Start menu'
} catch { Say '  (Could not add a Start menu entry; run "cc open" any time instead.)' 'DarkGray' }

Write-Progress -Activity 'Installing Command Center' -Completed
Say ''
Say 'Starting Command Center in the background. It will also start by itself each time you log in.' 'Green'
& $cc start --reinstall --no-foreground
$code = $LASTEXITCODE
if ($code -ne 0) {
    Fail 'Command Center is installed, but it could not be started in the background.' 'Read the message just above. If it mentions Access is denied, an old Command Center task made by another account is in the way: ask whoever manages this PC to delete the Task Scheduler task named CommandCenter-web, then double-click the installer again. To use it right away without the background task, close this window, open a new PowerShell and type: cc setup'
}
if ($code -eq 0) {
    Say ''
    Say "ALL DONE in $(Elapsed $script:Clock). Command Center is running." 'Green'
    Say 'Your browser should open it now. If it did not, or to open it again later: Start menu > Command Center.'
    Say 'You can close this window now.'
    Save-Log -Quiet
}
exit $code
