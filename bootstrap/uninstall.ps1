<#
Command Center uninstaller for Windows 10/11 (PowerShell 5.1 or later). Most people should double-click
"Uninstall Command Center.cmd" in the folder above this one; it runs this script.
  .\bootstrap\uninstall.ps1                 show what will be removed and ask before each part
  .\bootstrap\uninstall.ps1 -DryRun         print what it would do and change nothing
  .\bootstrap\uninstall.ps1 -Yes            do not ask about the program and the tools the installer added (data is still asked)
  .\bootstrap\uninstall.ps1 -DeleteData     also delete the data folder without asking (a zip copy goes to the Desktop first)
  .\bootstrap\uninstall.ps1 -KeepData       never ask about or touch the data folder
It always removes the background dashboard, the scheduled runs, the Start menu entry and the program itself.
Python, pipx and LibreOffice are removed only if the installer recorded that IT added them, and each is asked about
separately. Your data folder (profile, roles, kits) is kept unless you say otherwise.
The file is plain ASCII on purpose: Windows PowerShell 5.1 misreads other characters in a script without a BOM.
#>
param(
    [switch]$DryRun,
    [switch]$Yes,
    [switch]$DeleteData,
    [switch]$KeepData
)
$ErrorActionPreference = 'Stop'
$script:Problems = New-Object System.Collections.Generic.List[string]

function Say($m, $color) { if ($color) { Write-Host $m -ForegroundColor $color } else { Write-Host $m } }
function Ask($q, $defaultYes) {
    if ($DryRun) { Say "WOULD ASK: $q"; return $true }
    $hint = if ($defaultYes) { '[Y/n]' } else { '[y/N]' }
    $raw = Read-Host "$q $hint"
    if ($null -eq $raw) { return $false }      # no console input at all: never remove anything unasked
    $a = $raw.Trim().ToLower()
    if ($a -eq '') { return [bool]$defaultYes }
    return ($a -eq 'y' -or $a -eq 'yes')
}
# One external command; a failure is reported and the other parts still run.
function Step($what, [scriptblock]$do) {
    if ($DryRun) { Say "WOULD: $what"; return $true }
    Say "  ... $what"
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 0
    $code = 0
    $out = ''
    try { $out = (& $do 2>&1 | Out-String); $code = $LASTEXITCODE } catch { $code = 1 } finally { $ErrorActionPreference = $old }
    if ($code -ne 0) {
        Say "  Could not finish: $what (exit code $code)" 'Yellow'
        # Show the last few readable lines of what the command said (skips progress bars and blank lines).
        foreach ($l in @($out -split "`r?`n" | Where-Object { $_ -match '[A-Za-z]' } | Select-Object -Last 3)) {
            $t = $l.Trim(); if ($t.Length -gt 200) { $t = $t.Substring(0, 200) }
            Say "    $t" 'DarkGray'
        }
        $script:Problems.Add($what); return $false
    }
    return $true
}

# winget uninstall for a tool the installer added. A failure is not a failure when the tool is already gone (winget list
# then says "no installed package", exit code 0x8A150014), so a tool removed by hand does not keep the record alive.
function Remove-WingetTool($what, $id) {
    if (Step $what { winget uninstall --id $id --silent }) { return $true }
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 0
    $gone = $false
    try { & winget list --id $id --exact 2>&1 | Out-Null; $gone = ($LASTEXITCODE -eq -1978335212) } catch { $gone = $false } finally { $ErrorActionPreference = $old }
    if ($gone) {
        [void]$script:Problems.Remove($what)
        Say '  It is already gone from this PC, so there is nothing left to remove.' 'DarkGray'
        return $true
    }
    return $false
}

# The scheduled tasks Command Center creates are all named CommandCenter-*. Used when cc is missing or its own
# uninstall failed (for example after the program was already removed), so no task is left pointing at nothing.
function Remove-LeftoverTasks {
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $names = @()
    try {
        $names = @(& schtasks /Query /FO CSV /NH 2>$null | ForEach-Object { ($_ -split '","')[0].Trim('"').TrimStart('\') } | Where-Object { $_ -like 'CommandCenter-*' })
    } catch { } finally { $ErrorActionPreference = $old }
    foreach ($n in $names) {
        Step "remove the scheduled task $n" { schtasks /End /TN $n 2>&1 | Out-Null; schtasks /Delete /TN $n /F } | Out-Null
    }
}

function Get-PyCmd {
    foreach ($c in @(@('py', '-3'), @('python'))) {
        $exe = Get-Command $c[0] -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        try { & $exe.Source @($c | Select-Object -Skip 1) -c "import sys" 2>$null; $ok = ($LASTEXITCODE -eq 0) } catch { $ok = $false } finally { $ErrorActionPreference = $old }
        if ($ok) { return , (@($exe.Source) + @($c | Select-Object -Skip 1)) }
    }
    $root = Join-Path $env:LOCALAPPDATA 'Programs\Python'
    foreach ($d in @(Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending)) {
        $p = Join-Path $d.FullName 'python.exe'
        if (Test-Path -LiteralPath $p) { return , @($p) }
    }
    return $null
}

Say 'Command Center uninstaller' 'Cyan'

$recDir = Join-Path $env:LOCALAPPDATA 'CommandCenter'
$recFile = Join-Path $recDir 'install-record.json'
$rec = @{}
if (Test-Path -LiteralPath $recFile) {
    try { (Get-Content -LiteralPath $recFile -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $rec[$_.Name] = $_.Value } } catch { Say 'The install record could not be read, so Python, pipx and LibreOffice will be left alone.' 'Yellow' }
}
# After a tool is removed, forget it in the record so a re-run (after another part failed) does not try it again.
function Drop-Record($name) {
    if ($DryRun -or -not $rec.ContainsKey($name)) { return }
    $rec.Remove($name)
    try { Set-Content -LiteralPath $recFile -Value ($rec | ConvertTo-Json) -Encoding ASCII } catch { }
}
$dataDir = if ($env:CC_HOME) { $env:CC_HOME } else { Join-Path $HOME '.commandcenter' }

$cc = $null
$c = Get-Command cc -ErrorAction SilentlyContinue
if ($c) { $cc = $c.Source }
$py = Get-PyCmd
if (-not $cc -and $py) {
    try { $bin = (& $py[0] @($py | Select-Object -Skip 1) -m pipx environment --value PIPX_BIN_DIR 2>$null).Trim(); if ($bin -and (Test-Path -LiteralPath (Join-Path $bin 'cc.exe'))) { $cc = Join-Path $bin 'cc.exe' } } catch { }
}

Say ''
Say 'This will remove:' 'Cyan'
Say '  - the background dashboard, the scheduled runs and the Start menu entry'
Say '  - the Command Center program'
if ($rec['libreoffice']) { Say '  - LibreOffice (the installer added it; you will be asked)' }
if ($rec['pipx']) { Say '  - pipx, the helper tool the installer added (you will be asked)' }
if ($rec['python']) { Say '  - Python 3.12, which the installer added (you will be asked)' }
if (-not ($rec['libreoffice'] -or $rec['pipx'] -or $rec['python'])) { Say '  (Python, pipx and LibreOffice stay: this installer did not add them, or no record was found.)' 'DarkGray' }
if (-not $KeepData) { Say "Your data folder is kept unless you choose to delete it: $dataDir" }
Say ''
if (-not $Yes -and -not (Ask 'Remove Command Center now?' $true)) { Say 'Nothing was changed.'; exit 0 }

# 1. Data decision first, so a zip copy exists before anything is deleted.
$delData = $false
if (-not $KeepData -and (Test-Path -LiteralPath $dataDir)) {
    $delData = [bool]$DeleteData
    if (-not $DeleteData -and -not $DryRun) { $delData = Ask "Also DELETE all your data in $dataDir (profile, roles, kits, saved passwords)? This cannot be undone." $false }
    if ($DryRun -and -not $DeleteData) { Say "WOULD ASK: delete your data in $dataDir (default no)" }
    if ($delData -and $cc) {
        $desk = [Environment]::GetFolderPath('Desktop'); if (-not (Test-Path -LiteralPath $desk)) { $desk = $HOME }
        $zip = Join-Path $desk ("CommandCenter-backup-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.zip')
        if (-not (Step "save a zip copy of your data to $zip (it may contain saved details; keep it private)" { & $cc backup create $zip })) {
            Say 'The zip copy failed, so your data will NOT be deleted.' 'Yellow'; $delData = $false
        }
    }
}

# 2. Dashboard, scheduled runs, shortcut (and the data folder when chosen).
if ($cc) {
    $a = @('uninstall'); if ($delData) { $a += @('--delete-data', '--yes') }
    if (-not (Step 'stop the dashboard, remove the scheduled runs and the Start menu entry' { & $cc @a })) { Remove-LeftoverTasks }
} else {
    Say '  The cc program was not found, so any leftover scheduled tasks are removed directly.' 'DarkGray'
    Remove-LeftoverTasks
}

# 3. The program itself.
if ($py) {
    $pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)
    Step 'remove the Command Center program (pipx uninstall cc)' { & $pyExe @pyArgs -m pipx uninstall cc } | Out-Null
}

# 4. Tools the installer added, each asked about on its own.
if ($rec['libreoffice'] -and ($Yes -or (Ask 'Remove LibreOffice? (the installer added it; PDF resumes need it; Windows may ask for permission)' $true))) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        if (Remove-WingetTool 'winget uninstall LibreOffice' 'TheDocumentFoundation.LibreOffice') { Drop-Record 'libreoffice' }
        else { Say '  LibreOffice is installed for all users, so Windows may need administrator permission to remove it. Accept the permission prompt if one appears, or remove it in Settings > Apps > Installed apps, then run this uninstaller again.' 'Yellow' }
    }
    else { Say '  winget was not found: remove LibreOffice in Settings > Apps.' 'Yellow' }
}
if ($rec['pipx'] -and $py -and ($Yes -or (Ask 'Remove pipx? (the installer added it)' $true))) {
    if (Step 'remove pipx' { & $pyExe @pyArgs -m pip uninstall -y pipx }) { Drop-Record 'pipx' }
    $localBin = Join-Path $HOME '.local\bin'
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($userPath) {
        $keep = @($userPath -split ';' | Where-Object { $_ -and ($_.TrimEnd('\') -ine $localBin.TrimEnd('\')) })
        if ($keep.Count -ne @($userPath -split ';' | Where-Object { $_ }).Count) {
            if ($DryRun) { Say "WOULD: remove $localBin from your PATH (only that entry)" }
            else { [Environment]::SetEnvironmentVariable('Path', ($keep -join ';'), 'User'); Say "  Removed $localBin from your PATH." }
        }
    }
    foreach ($d in @((Join-Path $HOME '.local\bin'), (Join-Path $HOME 'pipx'), (Join-Path $env:LOCALAPPDATA 'pipx'))) {
        if ((Test-Path -LiteralPath $d) -and -not @(Get-ChildItem -LiteralPath $d -Force -ErrorAction SilentlyContinue).Count) {
            if ($DryRun) { Say "WOULD: remove the empty folder $d" } else { Remove-Item -LiteralPath $d -Force -ErrorAction SilentlyContinue }
        }
    }
}
if ($rec['python'] -and ($Yes -or (Ask 'Remove Python 3.12? (the installer added it; other programs of yours may use it)' $false))) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        if (Remove-WingetTool 'winget uninstall Python 3.12' 'Python.Python.3.12') { Drop-Record 'python' }
        else { Say '  Python was installed for your user only. If this window is running as administrator, Windows refuses; close it and run the uninstaller again from a normal (non-administrator) window.' 'Yellow' }
    }
    else { Say '  winget was not found: remove Python in Settings > Apps.' 'Yellow' }
}

# 5. The record goes last, and only when nothing failed, so a re-run can finish the job.
if ($script:Problems.Count -eq 0) {
    if ($DryRun) { Say "WOULD: delete the install record in $recDir" }
    else { Remove-Item -LiteralPath $recDir -Recurse -Force -ErrorAction SilentlyContinue }
}

Say ''
if ($script:Problems.Count -eq 0) {
    Say 'ALL DONE. Command Center has been removed.' 'Green'
    if (-not $delData) { Say "Your data was kept in: $dataDir" }
    exit 0
}
Say 'Some parts could not be removed (listed above). Close Command Center windows, then run this again; running it twice is safe.' 'Yellow'
exit 1
