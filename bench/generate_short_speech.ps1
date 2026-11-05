param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
# Deterministic short Japanese utterances with known text. Synthetic, not user recordings.
Add-Type -AssemblyName System.Speech
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SelectVoice('Microsoft Haruka Desktop')
$phrases = @('はい', 'うん', 'え', 'あ', 'そう', 'いや', 'ね', 'おい', '待って', 'ありがとう')
$manifest = @()
try {
    foreach ($rate in @(0, 3)) {
        $synth.Rate = $rate
        for ($i = 0; $i -lt $phrases.Count; $i++) {
            $filename = "short_${rate}_${i}.wav"
            $synth.SetOutputToWaveFile((Join-Path $OutputDirectory $filename))
            $synth.Speak($phrases[$i])
            $synth.SetOutputToNull()
            $manifest += @{file=$filename; expected=$phrases[$i]; voice='Microsoft Haruka Desktop'; rate=$rate}
        }
    }
} finally {
    $synth.Dispose()
}
$manifest | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $OutputDirectory 'manifest.json')
