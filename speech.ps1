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
        if ($request.voice) { $speaker.SelectVoice([string]$request.voice) }
        $speaker.Rate = [Math]::Max(-10, [Math]::Min(10, [int]$request.rate))
        $speaker.Volume = [Math]::Max(0, [Math]::Min(100, [int]$request.volume))
        if ($OutputPath) { $speaker.SetOutputToWaveFile($OutputPath) }
        $speaker.Speak([string]$request.text)
    }
} finally { $speaker.Dispose() }
