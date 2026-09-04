# Imprime las URLs publicas de los tuneles de Cloudflare.
#
# Uso:
#   docker compose --profile public up -d
#   .\deploy\urls-publicas.ps1
#
# Las URLs cambian cada vez que los contenedores de tunel se reinician.

function Get-TunnelUrl {
    param([string]$Servicio, [string]$Etiqueta)

    $texto = docker compose logs $Servicio 2>$null | Out-String
    $encontrado = [regex]::Matches($texto, 'https://[a-z0-9-]+\.trycloudflare\.com')

    if ($encontrado.Count -gt 0) {
        # El ultimo match es el del arranque mas reciente.
        $url = $encontrado[$encontrado.Count - 1].Value
        "{0,-6} {1}" -f $Etiqueta, $url
    } else {
        "{0,-6} (aun no disponible; reintente en unos segundos)" -f $Etiqueta
    }
}

Write-Output "URLs publicas"
Write-Output "-------------"
Write-Output (Get-TunnelUrl -Servicio "tunnel-api"  -Etiqueta "API:")
Write-Output (Get-TunnelUrl -Servicio "tunnel-fhir" -Etiqueta "FHIR:")
Write-Output ""
Write-Output "Verificar desde fuera de la red (datos moviles):"
Write-Output "  <URL de API>/docs        -> Swagger"
Write-Output "  <URL de FHIR>/metadata   -> CapabilityStatement del servidor FHIR"
