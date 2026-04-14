$snippet = '
# uv .venv auto activate
function Set-LocationWithVenv {
  param([string]$Path = "~")
  Set-Location $Path
  if (Test-Path ".venv\Scripts\Activate.ps1") {
    & .venv\Scripts\Activate.ps1
  }
}
Set-Alias -Name cd -Value Set-LocationWithVenv -Option AllScope -Force'

$profile_path = $PROFILE

if (!(Test-Path $profile_path)) {
  New-Item $profile_path -Force | Out-Null
}

if (Select-String -Path $profile_path -Pattern "auto_activate_uv" -Quiet) {
  Write-Host "이미 PowerShell 프로파일에 추가되어 있어요."
} else {
  Add-Content $profile_path $snippet
  Write-Host "✅ PowerShell 프로파일에 추가 완료! PowerShell을 재시작해주세요."
}