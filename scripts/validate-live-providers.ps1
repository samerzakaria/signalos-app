param(
  [switch]$RequireCloudKeys,
  [switch]$SkipChat,
  [string]$EvidencePath
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Evidence = New-Object System.Collections.Generic.List[object]
$CloudValidated = 0
$CloudMissing = 0

function Add-Evidence {
  param([string]$Provider, [string]$Result, [string]$Status = "pass")
  $Evidence.Add([pscustomobject]@{
    provider = $Provider
    status = $Status
    result = $Result
  }) | Out-Null
  $label = if ($Status -eq "pass") { "PASS" } elseif ($Status -eq "skip") { "SKIP" } else { "FAIL" }
  Write-Host "[$label] $Provider - $Result"
}

function Get-SecretEnv {
  param([string]$Name)
  $value = [Environment]::GetEnvironmentVariable($Name)
  if ([string]::IsNullOrWhiteSpace($value)) { return $null }
  return $value.Trim()
}

function Invoke-Json {
  param(
    [string]$Method,
    [string]$Uri,
    [hashtable]$Headers = @{},
    $Body = $null
  )
  $params = @{
    Method = $Method
    Uri = $Uri
    Headers = $Headers
    TimeoutSec = 45
  }
  if ($null -ne $Body) {
    $params.ContentType = "application/json"
    $params.Body = ($Body | ConvertTo-Json -Depth 8 -Compress)
  }
  return Invoke-RestMethod @params
}

function Validate-Ollama {
  $tags = Invoke-Json -Method Get -Uri "http://localhost:11434/api/tags"
  $models = @($tags.models)
  if (-not $models.Count) { throw "Ollama is running but no local models are available." }
  $model = $models[0].name
  Add-Evidence "Ollama" "Fetched $($models.Count) local model(s); selected $model."
  if (-not $SkipChat) {
    $response = Invoke-Json -Method Post -Uri "http://localhost:11434/api/generate" -Body @{
      model = $model
      prompt = "Reply with exactly: SignalOS local provider ok"
      stream = $false
    }
    if ([string]::IsNullOrWhiteSpace($response.response)) {
      throw "Ollama returned an empty chat response."
    }
    Add-Evidence "Ollama" "Chat returned a non-empty response."
  }
}

function Validate-Anthropic {
  $key = Get-SecretEnv "ANTHROPIC_API_KEY"
  if (-not $key) { $script:CloudMissing++; Add-Evidence "Anthropic" "ANTHROPIC_API_KEY is not set." "skip"; return }
  $headers = @{ "x-api-key" = $key; "anthropic-version" = "2023-06-01" }
  $models = Invoke-Json -Method Get -Uri "https://api.anthropic.com/v1/models" -Headers $headers
  $first = @($models.data)[0]
  if (-not $first.id) { throw "Anthropic did not return a model id." }
  $script:CloudValidated++
  Add-Evidence "Anthropic" "Fetched model list; selected $($first.id)."
  if (-not $SkipChat) {
    $chat = Invoke-Json -Method Post -Uri "https://api.anthropic.com/v1/messages" -Headers $headers -Body @{
      model = $first.id
      max_tokens = 32
      messages = @(@{ role = "user"; content = "Reply with: SignalOS Anthropic ok" })
    }
    if (-not @($chat.content).Count) { throw "Anthropic returned no chat content." }
    Add-Evidence "Anthropic" "Chat returned content."
  }
}

function Validate-OpenAICompatible {
  param(
    [string]$Provider,
    [string]$EnvName,
    [string]$BaseUrl,
    [string[]]$PreferredPrefixes = @()
  )
  $key = Get-SecretEnv $EnvName
  if (-not $key) { $script:CloudMissing++; Add-Evidence $Provider "$EnvName is not set." "skip"; return }
  $headers = @{ Authorization = "Bearer $key" }
  $models = Invoke-Json -Method Get -Uri "$($BaseUrl.TrimEnd('/'))/models" -Headers $headers
  $available = @($models.data)
  if (-not $available.Count) { throw "$Provider did not return any models." }
  $selected = $available |
    Where-Object {
      $id = [string]$_.id
      -not $PreferredPrefixes.Count -or ($PreferredPrefixes | Where-Object { $id.StartsWith($_) })
    } |
    Select-Object -First 1
  if (-not $selected) { $selected = $available[0] }
  $script:CloudValidated++
  Add-Evidence $Provider "Fetched model list; selected $($selected.id)."
  if (-not $SkipChat) {
    $chat = Invoke-Json -Method Post -Uri "$($BaseUrl.TrimEnd('/'))/chat/completions" -Headers $headers -Body @{
      model = $selected.id
      messages = @(@{ role = "user"; content = "Reply with: SignalOS provider ok" })
    }
    $text = $chat.choices[0].message.content
    if ([string]::IsNullOrWhiteSpace($text)) { throw "$Provider returned an empty chat response." }
    Add-Evidence $Provider "Chat returned content."
  }
}

function Validate-Gemini {
  $key = Get-SecretEnv "GEMINI_API_KEY"
  if (-not $key) { $script:CloudMissing++; Add-Evidence "Gemini" "GEMINI_API_KEY is not set." "skip"; return }
  $models = Invoke-Json -Method Get -Uri "https://generativelanguage.googleapis.com/v1beta/models?key=$key"
  $selected = @($models.models) |
    Where-Object { @($_.supportedGenerationMethods) -contains "generateContent" } |
    Select-Object -First 1
  if (-not $selected.name) { throw "Gemini did not return a generateContent model." }
  $modelId = ([string]$selected.name).Replace("models/", "")
  $script:CloudValidated++
  Add-Evidence "Gemini" "Fetched model list; selected $modelId."
  if (-not $SkipChat) {
    $chat = Invoke-Json -Method Post -Uri "https://generativelanguage.googleapis.com/v1beta/models/$($modelId):generateContent?key=$key" -Body @{
      contents = @(@{ parts = @(@{ text = "Reply with: SignalOS Gemini ok" }) })
    }
    if (-not @($chat.candidates).Count) { throw "Gemini returned no candidates." }
    Add-Evidence "Gemini" "Chat returned content."
  }
}

function Write-EvidenceFile {
  if (-not $EvidencePath) { return }
  $target = if ([System.IO.Path]::IsPathRooted($EvidencePath)) {
    $EvidencePath
  } else {
    Join-Path $Root $EvidencePath
  }
  $dir = Split-Path -Parent $target
  if ($dir) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  $lines = @(
    "# SignalOS Live Provider Evidence",
    "",
    "Date: $(Get-Date -Format s)",
    "",
    "## Results",
    ""
  )
  foreach ($item in $Evidence) {
    $lines += "- $($item.provider): $($item.status) - $($item.result)"
  }
  Set-Content -Path $target -Value ($lines -join "`n") -Encoding UTF8
}

Write-Host "SignalOS live provider validation"

try {
  Validate-Ollama
} catch {
  Add-Evidence "Ollama" $_.Exception.Message "skip"
}

Validate-Anthropic
Validate-OpenAICompatible "OpenAI" "OPENAI_API_KEY" "https://api.openai.com/v1" @("gpt-4", "gpt-3.5", "o")
Validate-Gemini
Validate-OpenAICompatible "Qwen" "QWEN_API_KEY" "https://dashscope-intl.aliyuncs.com/compatible-mode/v1" @("qwen")
Validate-OpenAICompatible "OpenRouter" "OPENROUTER_API_KEY" "https://openrouter.ai/api/v1" @()
Validate-OpenAICompatible "DeepSeek" "DEEPSEEK_API_KEY" "https://api.deepseek.com" @("deepseek")
Validate-OpenAICompatible "Mistral" "MISTRAL_API_KEY" "https://api.mistral.ai/v1" @("mistral")
Validate-OpenAICompatible "Groq" "GROQ_API_KEY" "https://api.groq.com/openai/v1" @()
Validate-OpenAICompatible "Cerebras" "CEREBRAS_API_KEY" "https://api.cerebras.ai/v1" @()
Validate-OpenAICompatible "Together AI" "TOGETHER_API_KEY" "https://api.together.xyz/v1" @()
Validate-OpenAICompatible "xAI" "XAI_API_KEY" "https://api.x.ai/v1" @("grok")

Write-EvidenceFile

if ($RequireCloudKeys -and $CloudValidated -eq 0) {
  throw "No cloud provider API keys are available in the environment. Set provider keys and rerun this script."
}

Write-Host ""
Write-Host "Live provider validation completed. Cloud providers validated: $CloudValidated. Missing cloud keys: $CloudMissing."
