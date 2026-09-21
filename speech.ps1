param([switch]$List, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($List) {
        @($speaker.GetInstalledVoices() | Where-Object Enabled | ForEach-Object {
            @{ name=$_.VoiceInfo.Name; culture=$_.VoiceInfo.Culture.Name }
        }) | ConvertTo-Json -Compress
    } else {
        $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
        $voices = @($speaker.GetInstalledVoices() | Where-Object Enabled)
        $selected = $voices | Where-Object { $_.VoiceInfo.Name -eq [string]$request.voice } | Select-Object -First 1
        if (-not $selected) { $selected = $voices | Where-Object { $_.VoiceInfo.Culture.Name -eq 'ko-KR' } | Select-Object -First 1 }
        if ($selected) { $speaker.SelectVoice($selected.VoiceInfo.Name) }
        $speaker.Rate = [Math]::Max(-10, [Math]::Min(10, [int]$request.rate))
        $speaker.Volume = [Math]::Max(0, [Math]::Min(100, [int]$request.volume))
        if ($OutputPath) { $speaker.SetOutputToWaveFile($OutputPath) }
        $speaker.Speak([string]$request.text)
    }
} finally { $speaker.Dispose() }
