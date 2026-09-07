# Administrator setup inside the dedicated worker VM. Copy the three files to worker bin first.
[CmdletBinding()]
param([string]$Root='C:\ProgramData\AIFactoryWorker',[string]$TaskUser='aifactory-task')
$ErrorActionPreference='Stop'
foreach($file in @('aifactory-computer.exe','aifactory-desktop.exe','windows.ps1')){if(-not (Test-Path "$Root\bin\$file")){throw "Missing $file"}}
$desktop="$Root\desktop"
New-Item -ItemType Directory -Force $desktop | Out-Null
$acl=[Security.AccessControl.DirectorySecurity]::new();$acl.SetAccessRuleProtection($true,$false)
foreach($sid in @('S-1-5-18','S-1-5-32-544')){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','ContainerInherit,ObjectInherit','None','Allow'))}
$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($TaskUser,'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow'))
Set-Acl -LiteralPath $desktop -AclObject $acl
if(-not (Test-Path "$desktop\agent.token")){
 & "$Root\bin\aifactory-computer.exe" -mode init-token -token "$desktop\agent.token"
 if($LASTEXITCODE){throw 'Desktop token initialization failed'}
}
$action=New-ScheduledTaskAction -Execute "$Root\bin\aifactory-desktop.exe" -Argument ('-mode serve -token "'+$desktop+'\agent.token"')
$trigger=New-ScheduledTaskTrigger -AtLogOn -User $TaskUser
$principal=New-ScheduledTaskPrincipal -UserId $TaskUser -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval ([TimeSpan]::FromMinutes(1)) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
if(Get-ScheduledTask AIFactoryDesktop -ErrorAction SilentlyContinue){Stop-ScheduledTask AIFactoryDesktop}
Register-ScheduledTask -TaskName AIFactoryDesktop -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask AIFactoryDesktop
Get-ScheduledTask AIFactoryDesktop | Select-Object TaskName,State
