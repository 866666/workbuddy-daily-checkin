# WorkBuddy积分助手 - 系统通知（自包含，无外部模块依赖）
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File toast.ps1 <payload.json>
# payload.json: {"title": "...", "message": "..."}
# 优先用 WinRT ToastNotification；失败回退 Windows Forms 气泡；再失败静默退出。

param([string]$PayloadPath)

function Show-Toast {
    param([string]$Title, [string]$Message)
    $appId = "WorkBuddy.Integration.Score"
    # 加载 WinRT 类型
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null

    $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
        [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $texts = $template.GetElementsByTagName("text")
    $texts.Item(0).AppendChild($template.CreateTextNode($Title)) | Out-Null
    $texts.Item(1).AppendChild($template.CreateTextNode($Message)) | Out-Null

    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId)
    $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
    $notifier.Show($toast)
}

function Show-Balloon {
    param([string]$Title, [string]$Message)
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $notify = New-Object System.Windows.Forms.NotifyIcon
    $notify.Icon = [System.Drawing.SystemIcons]::Information
    $notify.Visible = $true
    $notify.BalloonTipTitle = $Title
    $notify.BalloonTipText = $Message
    $notify.ShowBalloonTip(5000)
    Start-Sleep -Seconds 5
    $notify.Dispose()
}

try {
    if (-not $PayloadPath -or -not (Test-Path $PayloadPath)) {
        exit 1
    }
    $raw = Get-Content -Path $PayloadPath -Raw -Encoding UTF8
    $obj = $raw | ConvertFrom-Json
    $title = [string]($obj.title)
    $message = [string]($obj.message)
    if ($title -eq "") { $title = "WorkBuddy 积分播报" }

    try {
        Show-Toast -Title $title -Message $message
        exit 0
    } catch {
        # WinRT 不可用（如服务器核心/无交互会话），回退气泡
        try {
            Show-Balloon -Title $title -Message $message
            exit 0
        } catch {
            exit 2
        }
    }
} catch {
    exit 2
}
