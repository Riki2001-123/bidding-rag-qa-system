param([string]$LogDirectory)
$ErrorActionPreference = 'Stop'
$ragProjectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue) {
    Write-Output 'Port 8000 is already in use; no second backend instance was started.'
    return
}
$ragOutputDir = Join-Path $ragProjectRoot 'dist/live-system-tests'
if ($LogDirectory) {
    $ragOutputDir = [System.IO.Path]::GetFullPath($LogDirectory)
    if (-not $ragOutputDir.StartsWith('D:\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Local runtime logs must remain on D:.'
    }
}
New-Item -ItemType Directory -Force $ragOutputDir | Out-Null
$env:TEMP = Join-Path $ragProjectRoot 'dist'
$env:TMP = $env:TEMP
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:HF_HOME = 'D:\huggingface_cache'
$env:HF_HUB_CACHE = 'D:\huggingface_cache\hub'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:TORCH_HOME = Join-Path $ragProjectRoot 'dist/torch-cache'
$env:XDG_CACHE_HOME = Join-Path $ragProjectRoot 'dist/cache'
$env:OMP_NUM_THREADS = '2'
$env:MKL_NUM_THREADS = '2'
if (-not $env:RERANKER_BATCH_SIZE) { $env:RERANKER_BATCH_SIZE = '4' }
$env:NO_PROXY = 'localhost,127.0.0.1,::1'
$env:NO_GRPC_PROXY = $env:NO_PROXY
Remove-Item Env:HTTP_PROXY,Env:HTTPS_PROXY,Env:ALL_PROXY,Env:GRPC_PROXY -ErrorAction SilentlyContinue
# Allow this local production preview as well as Vite's development server.
$env:CORS_ORIGINS = 'http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173'
$ragPython = Join-Path $ragProjectRoot 'dist/backend-venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $ragPython)) { $ragPython = 'D:\python\Anaconda3\python.exe' }
$ragProcess = Start-Process -FilePath $ragPython -ArgumentList '-u','-m','uvicorn','app.main:app','--app-dir','backend','--host','127.0.0.1','--port','8000' -WorkingDirectory $ragProjectRoot -RedirectStandardOutput (Join-Path $ragOutputDir 'backend.stdout.log') -RedirectStandardError (Join-Path $ragOutputDir 'backend.stderr.log') -WindowStyle Hidden -PassThru
$ragProcess.Id | Set-Content (Join-Path $ragOutputDir 'backend.pid')
Write-Output ('Backend started on http://127.0.0.1:8000; PID=' + $ragProcess.Id)
