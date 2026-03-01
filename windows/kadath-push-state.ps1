# kadath-push-state.ps1 - 카다스 상태 push
param(
    [string]$State = "idle",
    [string]$Detail = "대기중..."
)

$body = @{
    agentId = "agent_1772370923045_a6ih"
    joinKey = "ocj_kadath"
    state = $State
    detail = $Detail
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://192.168.0.4:18795/agent-push" `
    -Method Post `
    -Body $body `
    -ContentType "application/json; charset=utf-8"
