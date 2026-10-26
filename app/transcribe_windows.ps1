param([Parameter(Mandatory=$true)][string]$WavPath, [Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$recognizerInfo = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() | Where-Object { $_.Culture.Name -eq 'zh-CN' } | Select-Object -First 1
if (-not $recognizerInfo) { throw 'No installed zh-CN speech recognizer' }
$engine = [System.Speech.Recognition.SpeechRecognitionEngine]::new($recognizerInfo)
try {
    $engine.LoadGrammar([System.Speech.Recognition.DictationGrammar]::new())
    $engine.SetInputToWaveFile($WavPath)
    $segments = [System.Collections.Generic.List[object]]::new()
    for ($n=0; $n -lt 100; $n++) {
        $recognized = $engine.Recognize([TimeSpan]::FromSeconds(3))
        if ($null -eq $recognized) { break }
        $segments.Add([pscustomobject]@{text=$recognized.Text;confidence=[double]$recognized.Confidence})
    }
    $record = @{engine='Windows System.Speech zh-CN';segments=@($segments.ToArray());text=(@($segments.ToArray() | ForEach-Object {$_.text}) -join '')}
    [IO.File]::WriteAllText($OutputPath, ($record | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
} finally { $engine.Dispose() }
