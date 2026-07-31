param(
    [ValidateSet("dev", "docker")]
    [string]$Mode = "dev"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot

if ($Mode -eq "docker") {
    $composeArguments = @(
        "compose",
        "-f", (Join-Path $repositoryRoot "docker/docker-compose.yml"),
        "-f", (Join-Path $repositoryRoot "docker/docker-compose.connector-demo.yml")
    )
} else {
    $composeArguments = @(
        "compose",
        "-f", (Join-Path $repositoryRoot "dev/docker-compose.yml")
    )
}

$password = & docker @composeArguments exec -T connector-test-postgres `
    sh -c 'cat /tls/connector-credentials/demo-password'
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($password)) {
    throw "Connector demo credential is unavailable. Start the connector-demo profile first."
}

$password.Trim() | Set-Clipboard
Write-Output "Connector demo credential copied to the clipboard."
