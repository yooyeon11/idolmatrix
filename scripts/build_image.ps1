# =============================================================
# idolMatrix 镜像构建 + 冒烟验证 + 导出（版本号参数化）
#
# 用法（仓库根目录，需 docker 引擎已就绪）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_image.ps1 -Version 3.9.7
#
# 流程：
#   1. docker build --platform linux/amd64 -t idolmatrix:<Version> .
#   2. 起临时容器冒烟：/api/system/health 报版本号 == 参数值
#   3. 打印前端 index-*.js 资产指纹（人工比对是否为新构建）
#   4. docker save 导出 idolmatrix-<Version>.tar 到仓库根目录
# =============================================================
param(
    [Parameter(Mandatory = $true)]
    [string]$Version
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# ---- 0. 引擎就绪检查 ----
$null = docker version --format "{{.Server.Version}}"
if ($LASTEXITCODE -ne 0) {
    throw "docker 引擎未就绪：请先启动 Docker Desktop 并等引擎起来"
}
Write-Output "== docker 引擎 OK =="

# ---- 1. 构建 ----
Write-Output "== docker build --platform linux/amd64 -t idolmatrix:$Version =="
docker build --platform linux/amd64 -t "idolmatrix:$Version" $root
if ($LASTEXITCODE -ne 0) { throw "docker build 失败" }

# ---- 2. 冒烟：临时容器 + health 版本比对 ----
Write-Output "== 冒烟：临时容器 18010 端口 =="
$cid = docker run -d --rm --name "idolmatrix-smoke-$Version" -p 18010:8010 -e AUTH_REQUIRED=false "idolmatrix:$Version"
if ($LASTEXITCODE -ne 0) { throw "容器启动失败" }

$healthOk = $false
$healthJson = ""
try {
    foreach ($i in 1..30) {
        Start-Sleep -Seconds 2
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:18010/api/system/health" -UseBasicParsing -TimeoutSec 3
            $healthJson = $resp.Content
            if ($resp.Content -match "`"version`":`"$Version`"") { $healthOk = $true; break }
        } catch { Start-Sleep -Seconds 1 }
    }
    $index = (Invoke-WebRequest -Uri "http://127.0.0.1:18010/" -UseBasicParsing -TimeoutSec 5).Content
    $hash = [regex]::Match($index, "assets/index-[\w-]+\.js").Value
    Write-Output "== 冒烟结果 =="
    Write-Output "health: $healthJson"
    Write-Output "前端资产: $hash"
    if (-not $healthOk) { throw "health 版本与 $Version 不符：$healthJson" }
} finally {
    docker stop "idolmatrix-smoke-$Version" 2>$null | Out-Null
}
if (-not $healthOk) { throw "冒烟失败：health 未在时限内返回 $Version" }

# ---- 3. 导出 tar 到仓库根目录 ----
$tar = Join-Path $root "idolmatrix-$Version.tar"
Write-Output "== docker save -> $tar =="
docker save "idolmatrix:$Version" -o $tar
if ($LASTEXITCODE -ne 0) { throw "docker save 失败" }
$size = (Get-Item $tar).Length / 1MB
Write-Output ("导出完成：{0}（{1:N0} MB）" -f $tar, $size)
Write-Output "下一步：把 idolmatrix-$Version.tar + docker-compose.yml 上传 NAS，docker load -i 后 docker compose up -d"
