<#
Deletes the Antigravity conversations that ag_start created: their files, their rows in the App's
index, and the bridge state that points at them. Conversations you started by hand are never touched.

Per conversation id it removes:
  brain\<id>\                    transcript and artifacts
  conversations\<id>.db(+shm,wal)
  annotations\<id>.pbtxt
  presence\<id>.lock
  rows in conversation_summaries.db  (the list the App shows), after a backup of that file

Dry run (default): lists everything and changes nothing.
  powershell -NoProfile -File delete-bridge-conversations.ps1
Delete: close the Antigravity App fully first, then
  powershell -NoProfile -File delete-bridge-conversations.ps1 -Delete
#>
param([switch]$Delete)

$ErrorActionPreference = 'Stop'
$ag = (Resolve-Path (Join-Path $HOME '.gemini\antigravity')).Path
$state = Join-Path $HOME '.gemini\ag-bridge'
$tasksFile = Join-Path $state 'tasks.json'
$purge = Join-Path $PSScriptRoot 'purge_index.py'
$uuidPattern = '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'

$tasks = Get-Content -LiteralPath $tasksFile -Raw | ConvertFrom-Json
$ids = @($tasks.PSObject.Properties | ForEach-Object { $_.Value.conversation_id } | Sort-Object -Unique)

function Inside($path, $folder) {
    $parent = Split-Path $path -Parent
    return ($parent -eq (Join-Path $ag $folder))
}

$items = @()
foreach ($id in $ids) {
    if ($id -notmatch $uuidPattern) { Write-Host "skip, not a conversation id: '$id'"; continue }
    $candidates = @(
        @{ folder = 'brain'; path = (Join-Path $ag "brain\$id"); dir = $true },
        @{ folder = 'conversations'; path = (Join-Path $ag "conversations\$id.db"); dir = $false },
        @{ folder = 'conversations'; path = (Join-Path $ag "conversations\$id.db-shm"); dir = $false },
        @{ folder = 'conversations'; path = (Join-Path $ag "conversations\$id.db-wal"); dir = $false },
        @{ folder = 'annotations'; path = (Join-Path $ag "annotations\$id.pbtxt"); dir = $false },
        @{ folder = 'presence'; path = (Join-Path $ag "presence\$id.lock"); dir = $false }
    )
    foreach ($c in $candidates) {
        if (-not (Test-Path -LiteralPath $c.path)) { continue }
        $resolved = (Resolve-Path -LiteralPath $c.path).Path
        if (-not (Inside $resolved $c.folder)) { Write-Host "skip, unexpected location: $resolved"; continue }
        $items += [pscustomobject]@{ Path = $resolved; Dir = $c.dir }
    }
}

$keptBrain = @(Get-ChildItem -LiteralPath (Join-Path $ag 'brain') -Directory | Where-Object { $ids -notcontains $_.Name })

Write-Host ''
Write-Host "WILL DELETE $($items.Count) files and folders for $($ids.Count) bridge conversations, for example:"
$items | Select-Object -First 8 | ForEach-Object { Write-Host "  $($_.Path)" }
Write-Host "  ... and the matching rows in conversation_summaries.db"
Write-Host ''
Write-Host "STAYS: $($keptBrain.Count) conversations not started by the bridge:"
$keptBrain | ForEach-Object { Write-Host "  $($_.Name)" }
Write-Host ''

$idsFile = Join-Path $state 'purge_ids.txt'
[IO.File]::WriteAllLines($idsFile, $ids)
$db = Join-Path $ag 'conversation_summaries.db'

if (-not $Delete) {
    Write-Host 'Index rows a real run would remove (dry):'
    python $purge $db $idsFile --dry
    Write-Host ''
    Write-Host 'Dry run only. Nothing deleted. Close Antigravity, then run again with -Delete.'
    return
}

if (Get-Process -Name 'Antigravity', 'language_server' -ErrorAction SilentlyContinue) {
    throw 'Antigravity is still running. Close it fully, then run again.'
}
$answer = Read-Host "Type DELETE to permanently remove $($ids.Count) conversations"
if ($answer -ne 'DELETE') { Write-Host 'Cancelled.'; return }

$backup = Join-Path $state ("backup-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backup | Out-Null
foreach ($suffix in '', '-wal', '-shm') {
    if (Test-Path -LiteralPath ($db + $suffix)) { Copy-Item -LiteralPath ($db + $suffix) -Destination $backup }
}
Write-Host "index backed up to $backup"

foreach ($item in $items) {
    if ($item.Dir) { Remove-Item -LiteralPath $item.Path -Recurse -Force } else { Remove-Item -LiteralPath $item.Path -Force }
    Write-Host "deleted $($item.Path)"
}

python $purge $db $idsFile

$stateFiles = @($tasksFile, (Join-Path $state 'events.jsonl'), $idsFile)
$stateFiles += @(Get-ChildItem -LiteralPath (Join-Path $state 'questions') -File -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
foreach ($f in $stateFiles) {
    if (Test-Path -LiteralPath $f -PathType Leaf) {
        Remove-Item -LiteralPath $f -Force
        Write-Host "cleared $f"
    }
}
Write-Host 'Done. Start Antigravity again. If a deleted chat still shows, tell Claude.'
