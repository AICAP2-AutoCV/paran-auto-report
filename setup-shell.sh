#!/bin/bash

MAC_ZSH="$HOME/.zshrc"
LINUX_BASH="$HOME/.bashrc"
WIN_PROFILE="$HOME/Documents/PowerShell/Microsoft.PowerShell_profile.ps1"

UNIX_SNIPPET='
# uv .venv auto activate
auto_activate_uv() {
  if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
  fi
}
cd() { builtin cd "$@" && auto_activate_uv; }'

WIN_SNIPPET='
# uv .venv auto activate
function Set-LocationWithVenv {
  param([string]$Path = "~")
  Set-Location $Path
  if (Test-Path ".venv\Scripts\Activate.ps1") {
    & .venv\Scripts\Activate.ps1
  }
}
Set-Alias -Name cd -Value Set-LocationWithVenv -Option AllScope -Force'

# 중복 추가 방지
already_added() {
  grep -q "auto_activate_uv" "$1" 2>/dev/null
}

OS="$(uname -s)"

if [[ "$OS" == "Darwin" ]]; then
  if already_added "$MAC_ZSH"; then
    echo "이미 ~/.zshrc에 추가되어 있어요."
  else
    echo "$UNIX_SNIPPET" >> "$MAC_ZSH"
    echo "✅ ~/.zshrc에 추가 완료! 'source ~/.zshrc' 실행해주세요."
  fi

elif [[ "$OS" == "Linux" ]]; then
  if already_added "$LINUX_BASH"; then
    echo "이미 ~/.bashrc에 추가되어 있어요."
  else
    echo "$UNIX_SNIPPET" >> "$LINUX_BASH"
    echo "✅ ~/.bashrc에 추가 완료! 'source ~/.bashrc' 실행해주세요."
  fi

else
  echo "Windows는 PowerShell에서 별도로 실행해주세요."
  echo "setup-shell.ps1 파일을 참고하세요."
fi