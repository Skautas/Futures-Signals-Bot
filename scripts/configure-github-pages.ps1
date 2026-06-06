<#
.SYNOPSIS
  Nustato GitHub Pages: šaka gh-pages, šakninis kelias / (legacy).

.EXAMPLE
  $env:GITHUB_TOKEN = "ghp_xxxx"
  .\scripts\configure-github-pages.ps1

  Token: https://github.com/settings/tokens (classic → repo)
#>
param(
  [string]$Token = $env:GITHUB_TOKEN,
  [string]$Owner = "Skautas",
  [string]$Repo = "Futures-Signals-Bot"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $Token) {
  Write-Host "Trūksta GITHUB_TOKEN arba parametro -Token. Žr. komentarus skripto pradžioje." -ForegroundColor Yellow
  exit 1
}

$uri = "https://api.github.com/repos/$Owner/$Repo/pages"
$headers = @{
  Authorization          = "Bearer $Token"
  Accept                 = "application/vnd.github+json"
  "X-GitHub-Api-Version" = "2022-11-28"
}
$body = '{"build_type":"legacy","source":{"branch":"gh-pages","path":"/"}}'

$pagesExists = $false
try {
  Invoke-RestMethod -Uri $uri -Headers $headers -Method Get | Out-Null
  $pagesExists = $true
}
catch {
  $status = $null
  if ($null -ne $_.Exception.Response) {
    $status = [int]$_.Exception.Response.StatusCode
  }
  if ($status -ne 404) {
    throw
  }
}

if ($pagesExists) {
  Write-Host "Atnaujinu Pages šaltinį (PUT)..." -ForegroundColor Cyan
  $resp = Invoke-WebRequest -Uri $uri -Headers $headers -Method Put -Body $body `
    -ContentType "application/json; charset=utf-8" -UseBasicParsing
  if ($resp.StatusCode -ne 204) {
    throw "Netikėtas atsakymas: $($resp.StatusCode)"
  }
  Write-Host "OK (204)." -ForegroundColor Green
}
else {
  Write-Host "Kuriu Pages (POST)..." -ForegroundColor Cyan
  $resp = Invoke-WebRequest -Uri $uri -Headers $headers -Method Post -Body $body `
    -ContentType "application/json; charset=utf-8" -UseBasicParsing
  if ($resp.StatusCode -notin @(201, 204)) {
    throw "Netikėtas atsakymas: $($resp.StatusCode)"
  }
  Write-Host "OK ($($resp.StatusCode))." -ForegroundColor Green
}

Write-Host ""
Write-Host "Palaukite 1–10 min., tada: https://$Owner.github.io/$Repo/" -ForegroundColor Green
