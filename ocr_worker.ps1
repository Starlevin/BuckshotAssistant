$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
    [Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime] | Out-Null
    [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
    [Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime] | Out-Null
    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
    function Await($op, $type) {
        $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
        $task.Wait()
        return $task.Result
    }
    $engines = @()
    foreach ($tag in @('zh-Hans-CN', 'en-US')) {
        $e = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($tag))
        if ($null -ne $e) { $engines += $e }
    }
    if ($engines.Count -eq 0) {
        $e = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
        if ($null -ne $e) { $engines += $e }
    }
    if ($engines.Count -eq 0) { throw 'Windows OCR language pack is unavailable.' }
} catch {
    @{error=$_.Exception.Message} | ConvertTo-Json -Compress | Write-Output
    exit 1
}
while ($null -ne ($line = [Console]::ReadLine())) {
    $stream = $null
    $bitmap = $null
    try {
        $request = $line | ConvertFrom-Json
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($request.path)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $texts = @()
        foreach ($engine in $engines) {
            $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            $texts += [ordered]@{language=$engine.RecognizerLanguage.LanguageTag; text=$result.Text}
        }
        [ordered]@{results=$texts} | ConvertTo-Json -Depth 5 -Compress | Write-Output
    } catch {
        @{error=$_.Exception.Message} | ConvertTo-Json -Compress | Write-Output
    } finally {
        if ($null -ne $bitmap) { $bitmap.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
    }
}
