param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('x86_64', 'aarch64')]
  [string]$Architecture
)
$ErrorActionPreference = 'Stop'
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = & $vswhere -latest -products '*' -property installationPath
if ($LASTEXITCODE -ne 0 -or -not $vs) { throw 'Visual Studio was not found' }
Import-Module "$vs\Common7\Tools\Microsoft.VisualStudio.DevShell.dll"
$arch = if ($Architecture -eq 'aarch64') { 'arm64' } else { 'x64' }
$hostArch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
if ($hostArch -notin @('x64', 'arm64')) { throw "Unsupported Windows host: $hostArch" }
$env:LLGO_MSVC_BUILD_HOST = $hostArch
Enter-VsDevShell -VsInstallPath $vs -SkipAutomaticLocation `
  -DevCmdArguments "-arch=$arch -host_arch=$hostArch"
python "$PSScriptRoot/build.py" --arch $Architecture `
  --work "$env:RUNNER_TEMP/llvm-msvc-deps" --output "$PWD/dist"
if ($LASTEXITCODE -ne 0) { throw 'Building or validating MSVC dependencies failed' }
