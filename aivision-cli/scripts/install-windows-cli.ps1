# Requires PowerShell 7. Install from the ZIP and SHA256SUMS of the same release.
param(
    [Parameter(Mandatory)][string] $AssetDirectory,
    [string] $InstallDirectory = (Join-Path $env:LOCALAPPDATA 'AI Vision\bin')
)

$ErrorActionPreference = 'Stop'
$archiveName = 'aiv-windows-x64.zip'
$archivePath = Join-Path $AssetDirectory $archiveName
$checksumLines = @(Get-Content -LiteralPath (Join-Path $AssetDirectory 'SHA256SUMS') | Where-Object {
    $_ -cmatch '^[a-f0-9]{64}  aiv-windows-x64\.zip$'
})
if ($checksumLines.Count -ne 1) {
    throw "SHA256SUMS must contain one checksum for $archiveName."
}
$actual = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -cne $checksumLines[0].Substring(0, 64)) {
    throw "SHA-256 verification failed for $archiveName."
}

$directory = [System.IO.Path]::GetFullPath($InstallDirectory)
if ($directory.Contains(';')) {
    throw 'Installation directory may not contain a PATH separator.'
}
[void][System.IO.Directory]::CreateDirectory($directory)
$executable = Join-Path $directory 'aiv.exe'
$tempFile = Join-Path $directory ('.aiv-' + [Guid]::NewGuid().ToString('N') + '.tmp')
$zip = [System.IO.Compression.ZipFile]::OpenRead($archivePath)
try {
    $entries = @($zip.Entries | Where-Object { $_.FullName -ceq 'aiv.exe' })
    if ($entries.Count -ne 1 -or $entries[0].Length -le 0) {
        throw 'The archive must contain exactly one aiv.exe.'
    }
    [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entries[0], $tempFile)
    [System.IO.File]::Move($tempFile, $executable, $true)
} finally {
    $zip.Dispose()
    if (Test-Path -LiteralPath $tempFile) {
        Remove-Item -LiteralPath $tempFile
    }
}

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($directory -notin ($userPath -split ';')) {
    $newPath = (@($userPath, $directory) | Where-Object { $_ }) -join ';'
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
}
if ($directory -notin ($env:Path -split ';')) {
    $env:Path += ';' + $directory
}
Write-Output "Installed $executable. Open a new terminal to use aiv."
