param([string]$Root = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath $Root).Path
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
$helperPath = Join-Path $repoRoot 'scripts\configure_env.py'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Create .venv and install dependencies before configuring credentials.'
}
if ([Threading.Thread]::CurrentThread.ApartmentState -ne 'STA') {
    throw 'Run this script with powershell.exe -STA -File scripts\ConfigureEnv.ps1.'
}
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$script:configurationSaved = $false

function Save-LocalSettings([hashtable]$Fields) {
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $pythonPath
    $startInfo.Arguments = '"' + $helperPath + '" --root "' + $repoRoot + '"'
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.StandardOutputEncoding = [Text.Encoding]::UTF8
    $startInfo.StandardErrorEncoding = [Text.Encoding]::UTF8
    $startInfo.EnvironmentVariables['PYTHONUTF8'] = '1'
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    $payload = $null
    $inputWriter = $null
    try {
        [void]$process.Start()
        # No credential values are included in the command line or terminal output.
        $payload = $Fields | ConvertTo-Json -Compress
        # .NET Framework otherwise uses the Windows console encoding (e.g. Big5).
        $inputWriter = [System.IO.StreamWriter]::new(
            $process.StandardInput.BaseStream, [System.Text.UTF8Encoding]::new($false))
        $inputWriter.WriteLine($payload)
        $inputWriter.Dispose()
        $reply = $process.StandardOutput.ReadToEnd()
        [void]$process.StandardError.ReadToEnd()
        $process.WaitForExit()
        return ($reply | ConvertFrom-Json)
    }
    catch {
        return @{ saved = $false; error_code = 'configuration_write_failed' }
    }
    finally {
        $payload = $null
        $Fields.Clear()
        if ($inputWriter) { $inputWriter.Dispose() }
        $process.Dispose()
    }
}

$form = New-Object System.Windows.Forms.Form
$form.Text = 'Credit Savior - NTU COOL local configuration'
$form.ClientSize = New-Object System.Drawing.Size(520, 300)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false

$hint = New-Object System.Windows.Forms.Label
$hint.Text = "Enter credentials here. They are saved only to this checkout's .env.`nLeave a field blank to keep its existing value. No values enter Codex chat."
$hint.Location = New-Object System.Drawing.Point(16, 14)
$hint.Size = New-Object System.Drawing.Size(490, 44)
$form.Controls.Add($hint)

$boxes = @{}
$rows = @(
    @{ Key = 'COOL_USERNAME'; Label = 'NTU account'; Mask = $false },
    @{ Key = 'COOL_PASSWORD'; Label = 'NTU password'; Mask = $true },
    @{ Key = 'DISCORD_WEBHOOK_URL'; Label = 'Discord Webhook (optional)'; Mask = $true }
)
for ($index = 0; $index -lt $rows.Count; $index++) {
    $label = New-Object System.Windows.Forms.Label
    $label.Text = $rows[$index].Label
    $label.Location = New-Object System.Drawing.Point(16, (70 + 48 * $index))
    $label.Size = New-Object System.Drawing.Size(190, 22)
    $box = New-Object System.Windows.Forms.TextBox
    $box.Location = New-Object System.Drawing.Point(208, (68 + 48 * $index))
    $box.Size = New-Object System.Drawing.Size(292, 24)
    $box.UseSystemPasswordChar = $rows[$index].Mask
    $box.MaxLength = 2048
    $boxes[$rows[$index].Key] = $box
    $form.Controls.Add($label)
    $form.Controls.Add($box)
}

$save = New-Object System.Windows.Forms.Button
$save.Text = 'Save locally'
$save.Location = New-Object System.Drawing.Point(272, 240)
$save.Size = New-Object System.Drawing.Size(110, 30)
$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = 'Cancel'
$cancel.Location = New-Object System.Drawing.Point(392, 240)
$cancel.Size = New-Object System.Drawing.Size(110, 30)
$cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.AcceptButton = $save
$form.CancelButton = $cancel
$form.Controls.Add($save)
$form.Controls.Add($cancel)

$save.Add_Click({
    $fields = @{}
    foreach ($key in $boxes.Keys) { $fields[$key] = $boxes[$key].Text }
    $reply = Save-LocalSettings $fields
    if ($reply.saved) {
        $script:configurationSaved = $true
        foreach ($box in $boxes.Values) { $box.Clear() }
        $form.Close()
    }
    else {
        $messages = @{
            COOL_USERNAME_required = 'Enter an NTU account.'
            COOL_PASSWORD_required = 'Enter the NTU password.'
            password_required_for_account_change = 'Enter the new password when changing the NTU account.'
            invalid_webhook = 'The Webhook URL format is invalid. Check the local value.'
            invalid_credential_input = 'Credentials must fit one line and the input length limit.'
        }
        $message = $messages[$reply.error_code]
        if (-not $message) { $message = 'Could not save the local configuration. Check the local installation and file access.' }
        [void][System.Windows.Forms.MessageBox]::Show($form, $message, 'Configuration not saved')
    }
})

try {
    [void]$form.ShowDialog()
}
finally {
    foreach ($box in $boxes.Values) { $box.Clear() }
    $boxes.Clear()
    $form.Dispose()
}
if ($script:configurationSaved) {
    Write-Output 'Local .env saved. Input dialog closed; credentials were not printed.'
    exit 0
}
Write-Output 'Configuration cancelled; no changes were saved.'
exit 1
