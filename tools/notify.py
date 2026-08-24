#!/usr/bin/env python3
"""notify.py — Windows Toast 桌面通知（定时任务告警用）。

无第三方依赖，通过 PowerShell BurntToast/原生 toast 发送。
用法：
    python tools/notify.py "标题" "正文"
    python tools/notify.py --error "标题" "正文"   # 红色错误样式
"""

import subprocess
import sys

_PS_TEMPLATE = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template)
$texts = $xml.GetElementsByTagName('text')
$texts.Item(0).AppendChild($xml.CreateTextNode('{title}')) > $null
$texts.Item(1).AppendChild($xml.CreateTextNode('{body}')) > $null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('AI Berkshire').Show($toast)
"""


def _send(title: str, body: str) -> bool:
    ps = _PS_TEMPLATE.format(title=title.replace("'", "''"),
                             body=body.replace("'", "''"))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=20)
        return r.returncode == 0
    except Exception:
        return False


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    title, body = "AI Berkshire", ""
    if args[0] == "--error":
        title = "❌ " + args[1] if len(args) > 1 else "❌ AI Berkshire"
        body = args[2] if len(args) > 2 else ""
    else:
        title = args[0]
        body = args[1] if len(args) > 1 else ""
    ok = _send(title, body)
    print(f"通知已发送: {title} | {body}" if ok else "通知发送失败")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
