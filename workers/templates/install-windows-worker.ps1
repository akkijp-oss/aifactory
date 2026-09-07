# Run as Administrator inside a dedicated VM with a restricted network.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$WorkerExe,[Parameter(Mandatory=$true)][string]$ConfigFile,[string]$Root='C:\ProgramData\AIFactoryWorker',[string]$TaskUser='aifactory-task',[switch]$DoNotStart)
$ErrorActionPreference='Stop'
if(Get-Service AIFactoryWorker -ErrorAction SilentlyContinue){throw 'Service exists; stop it and update explicitly'}
if(Get-LocalUser $TaskUser -ErrorAction SilentlyContinue){throw 'Use a fresh dedicated task account'}
$config=Get-Content -LiteralPath $ConfigFile -Raw | ConvertFrom-Json
if($config.work_root -ne "$Root\work" -or $config.task_user -ne $TaskUser -or $config.task_password_file -ne "$Root\private\task.password" -or $config.state_dir -ne "$Root\private\state" -or $config.token_file -ne "$Root\private\worker.token"){throw 'Configuration paths must match installation root/account'}
New-Item -ItemType Directory -Force $Root,"$Root\private","$Root\private\state","$Root\bin","$Root\work" | Out-Null
function Set-ProtectedACL([string]$Path,[string]$Account,[string]$Rights){
 $acl=[Security.AccessControl.DirectorySecurity]::new(); $acl.SetAccessRuleProtection($true,$false)
 foreach($sid in @('S-1-5-18','S-1-5-32-544')){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','ContainerInherit,ObjectInherit','None','Allow'))}
 if($Account){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($Account,$Rights,'ContainerInherit,ObjectInherit','None','Allow'))}
 Set-Acl -LiteralPath $Path -AclObject $acl
}
Set-ProtectedACL "$Root\private" '' ''
$bytes=New-Object byte[] 48; $rng=[Security.Cryptography.RandomNumberGenerator]::Create()
try{$rng.GetBytes($bytes)}finally{$rng.Dispose()}
$password=[Convert]::ToBase64String($bytes)+'aA1!'
New-LocalUser -Name $TaskUser -Password (ConvertTo-SecureString $password -AsPlainText -Force) -AccountNeverExpires -PasswordNeverExpires -UserMayNotChangePassword -Description 'AIFactory ordinary task account' | Out-Null
Add-LocalGroupMember -Group (Get-LocalGroup -SID 'S-1-5-32-545') -Member $TaskUser -ErrorAction SilentlyContinue
[IO.File]::WriteAllText("$Root\private\task.password",$password,[Text.UTF8Encoding]::new($false))
$password=$null; [Array]::Clear($bytes,0,$bytes.Length)
Set-ProtectedACL $Root $TaskUser 'ReadAndExecute'
Set-ProtectedACL "$Root\private" '' ''
Set-ProtectedACL "$Root\bin" $TaskUser 'ReadAndExecute'
Set-ProtectedACL "$Root\work" $TaskUser 'Modify'
$policy="$Root\private\rights.inf"
& secedit.exe /export /cfg $policy /areas USER_RIGHTS /quiet
if($LASTEXITCODE){throw 'Cannot export user rights'}
$sid=(Get-LocalUser $TaskUser).SID.Value
$batch=([IO.File]::ReadAllLines($policy) | Where-Object {$_ -match '^SeBatchLogonRight\s*='} | Select-Object -First 1)
if($batch){$batch=$batch.TrimEnd()+',*'+$sid}else{$batch='SeBatchLogonRight = *'+$sid}
$lines=@('[Unicode]','Unicode=yes','[Version]','signature="$CHICAGO$"','Revision=1','[Privilege Rights]',$batch)
[IO.File]::WriteAllLines($policy,$lines,[Text.Encoding]::Unicode)
& secedit.exe /configure /db "$Root\private\rights.sdb" /cfg $policy /areas USER_RIGHTS /quiet
if($LASTEXITCODE){throw 'Cannot grant batch logon'}
Copy-Item -LiteralPath $WorkerExe -Destination "$Root\bin\aifactory-worker.exe"
Copy-Item -LiteralPath $ConfigFile -Destination "$Root\private\config.json" -Force
$binary='"'+$Root+'\bin\aifactory-worker.exe" --config "'+$Root+'\private\config.json"'
New-Service -Name AIFactoryWorker -BinaryPathName $binary -DisplayName 'AIFactory Windows Worker' -StartupType Automatic -Description 'Pull queue worker; tasks run as a separate ordinary local account' | Out-Null
& sc.exe failure AIFactoryWorker reset= 86400 actions= restart/10000/restart/30000/restart/60000 | Out-Null
if(-not [Diagnostics.EventLog]::SourceExists('AIFactoryWorker')){New-EventLog -LogName Application -Source AIFactoryWorker}
if(-not $DoNotStart){Start-Service AIFactoryWorker}
Get-Service AIFactoryWorker | Select-Object Name,Status,StartType
