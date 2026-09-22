param(
    [Parameter(Mandatory = $true)][string]$CacheDir,
    [Parameter(Mandatory = $true)][string]$OutFile
)
# 微信 cache 明文图片批量 OCR（Windows 内置引擎，零外部依赖）。
# 输出 JSONL：{file, size, mtime, text}（仅文本 >10 字的图）。
$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}
function CleanText($t) {
    $t = ($t -replace "\r?\n", " ") -replace "\s+", " "
    $t = $t -replace "(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", ""
    return $t.Trim()
}

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) { Write-Host "NO_ENGINE"; exit 2 }
if (-not (Test-Path $CacheDir)) { Write-Host "NO_CACHE"; exit 0 }

$all = Get-ChildItem $CacheDir -Recurse -File | Where-Object { $_.Length -gt 10KB -and $_.Length -lt 8MB }
$files = @()
foreach ($f in $all) {
    try {
        $h = $f.OpenRead(); $b = New-Object byte[] 2; $null = $h.Read($b, 0, 2); $h.Close()
        if (($b[0] -eq 0xFF -and $b[1] -eq 0xD8) -or ($b[0] -eq 0x89 -and $b[1] -eq 0x50)) { $files += $f }
    } catch { }
}
$writer = [System.IO.StreamWriter]::new($OutFile, $false, (New-Object System.Text.UTF8Encoding($false)))
$ok = 0
foreach ($f in $files) {
    try {
        $sf = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($f.FullName)) ([Windows.Storage.StorageFile])
        $stream = Await ($sf.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bmp = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $res = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
        $stream.Dispose()
        $text = CleanText $res.Text
        if ($text.Length -gt 10) {
            $ok++
            $obj = [PSCustomObject]@{ file = $f.FullName; size = $f.Length; mtime = $f.LastWriteTime.ToString('yyyy-MM-dd'); text = $text }
            $writer.WriteLine(($obj | ConvertTo-Json -Compress))
        }
    } catch { }
}
$writer.Close()
Write-Host "OCR_DONE: $ok / $($files.Count)"
