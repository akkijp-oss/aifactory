$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
Set-PSDebug -Off
$source=Split-Path $PSScriptRoot -Parent
$endpoint=$env:AIFACTORY_URL
$worker=$env:AIFACTORY_WORKER
$token=$env:AIFACTORY_TOKEN
Remove-Item Env:AIFACTORY_TOKEN -ErrorAction SilentlyContinue
$uri=$null
if(-not [uri]::TryCreate($endpoint,[UriKind]::Absolute,[ref]$uri) -or $uri.Scheme -ne 'https' -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -ne '/'){throw 'AIFACTORY_URL must be an HTTPS origin without credentials, path or query'}
$endpoint=$endpoint.TrimEnd('/')
if($worker -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$'){throw 'Invalid AIFACTORY_WORKER'}
if(-not $token -or $token.Trim().Length -lt 32 -or $token.Trim() -match '\s'){throw 'AIFACTORY_TOKEN must be a dedicated enrolled worker token'}
if(-not [Environment]::Is64BitProcess -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64'){throw 'Run 64-bit PowerShell on Windows x64'}
if($env:AIFACTORY_CA_B64 -and $env:AIFACTORY_CA_FILE){throw 'Set only one of AIFACTORY_CA_B64 or AIFACTORY_CA_FILE'}
if($env:AIFACTORY_AUTOLOGIN -and $env:AIFACTORY_AUTOLOGIN -notin @('0','1')){throw 'AIFACTORY_AUTOLOGIN must be 0 or 1'}
$root='C:\ProgramData\AIFactoryWorker'
$taskUser='aifactory-task'
$old=$null
if(Test-Path "$root\private\config.json"){
 $old=Get-Content "$root\private\config.json" -Raw | ConvertFrom-Json
 if($old.worker -ne $worker -or $old.url.TrimEnd('/') -ne $endpoint){throw 'Existing installation belongs to another worker/endpoint; explicit migration required'}
 if(Test-Path "$($old.state_dir)\guest-lease"){throw 'Worker has an active lease; release it before installation'}
 if($old.task_user -ne $taskUser -or $old.work_root -ne "$root\work"){throw 'Unsupported existing installation layout'}
}
$utf8=[Text.UTF8Encoding]::new($false)
$ca=''
if($env:AIFACTORY_CA_B64){$ca=$utf8.GetString([Convert]::FromBase64String($env:AIFACTORY_CA_B64))}
elseif($env:AIFACTORY_CA_FILE){$ca=[IO.File]::ReadAllText($env:AIFACTORY_CA_FILE)}
else{
 foreach($storeName in @('Root','CA')){
  $store=[Security.Cryptography.X509Certificates.X509Store]::new($storeName,'LocalMachine');$store.Open('ReadOnly')
  try{foreach($cert in $store.Certificates){$ca+='-----BEGIN CERTIFICATE-----'+"`n"+[Convert]::ToBase64String($cert.RawData,[Base64FormattingOptions]::InsertLineBreaks)+"`n-----END CERTIFICATE-----`n"}}finally{$store.Close()}
 }
}
if($ca -notmatch '-----BEGIN CERTIFICATE-----'){throw 'No CA certificates supplied or available'}
if($env:AIFACTORY_SERVER_IP){$parsedIP=$null;if(-not [Net.IPAddress]::TryParse($env:AIFACTORY_SERVER_IP,[ref]$parsedIP)){throw 'Invalid AIFACTORY_SERVER_IP'}}
if($env:AIFACTORY_CHECK_ONLY -eq '1'){'Configuration valid; no system changes made';return}
function Run-Checked([string]$File,[string[]]$Arguments){& $File @Arguments;if($LASTEXITCODE){throw "External tool failed: $File (exit $LASTEXITCODE)"}}
function Download-Verified([string]$Url,[string]$Path,[string]$Hash){
 if(-not $Url.StartsWith('https://')){throw 'Downloads require HTTPS'}
 Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Path
 if($Hash -and (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $Hash){throw 'Download checksum mismatch'}
}
function Github-Asset([string]$Repo,[string]$Pattern,[string]$Destination){
 $release=Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest"
 $asset=@($release.assets | Where-Object {$_.name -match $Pattern})
 if($asset.Count -ne 1 -or $asset[0].digest -notmatch '^sha256:([0-9a-f]{64})$'){throw 'Expected a unique GitHub asset with a SHA256 digest'}
 Download-Verified $asset[0].browser_download_url $Destination $Matches[1]
}
$build=Join-Path (Split-Path $source -Parent) 'install-build'
New-Item -ItemType Directory -Force $build | Out-Null
$goReleases=Invoke-RestMethod -Uri 'https://go.dev/dl/?mode=json'
$goAsset=@($goReleases[0].files | Where-Object {$_.os -eq 'windows' -and $_.arch -eq 'amd64' -and $_.kind -eq 'archive'})[0]
Download-Verified ('https://go.dev/dl/'+$goAsset.filename) "$build\go.zip" $goAsset.sha256
Expand-Archive "$build\go.zip" "$build\toolchain" -Force
$go="$build\toolchain\go\bin\go.exe"
$env:CGO_ENABLED='0';$env:GOTOOLCHAIN='auto'
Push-Location $source
try{
 Run-Checked $go @('build','-trimpath','-o',"$build\aifactory-worker.exe",'./cmd/aifactory-worker')
 Run-Checked $go @('build','-trimpath','-o',"$build\aifactory-computer.exe",'./cmd/aifactory-computer')
 Run-Checked $go @('build','-trimpath','-ldflags=-H=windowsgui','-o',"$build\aifactory-desktop.exe",'./cmd/aifactory-computer')
}finally{Pop-Location}
if(-not (Test-Path 'C:\Program Files\Git\cmd\git.exe')){
 Github-Asset 'git-for-windows/git' '^Git-[0-9.]+-64-bit.exe$' "$build\git.exe"
 $p=Start-Process "$build\git.exe" -ArgumentList '/VERYSILENT /NORESTART /ALLUSERS /SUPPRESSMSGBOXES' -Wait -PassThru
 if($p.ExitCode -notin @(0,3010)){throw 'Git installation failed'}
}
if(-not (Test-Path 'C:\Program Files\GitHub CLI\gh.exe')){
 Github-Asset 'cli/cli' '^gh_[0-9.]+_windows_amd64.msi$' "$build\gh.msi"
 $p=Start-Process msiexec.exe -ArgumentList ('/i "'+$build+'\gh.msi" /qn /norestart') -Wait -PassThru
 if($p.ExitCode -notin @(0,3010)){throw 'GitHub CLI installation failed'}
}
if(-not (Test-Path "$root\bin\claude.exe")){
 # Native binary endpoint and per-platform checksums used by the official Claude installer.
 $installer="$build\claude-install.ps1"
 Download-Verified 'https://claude.ai/install.ps1' $installer ''
 & $installer
 if(-not (Test-Path "$env:USERPROFILE\.local\bin\claude.exe")){throw 'Claude installation failed'}
 Copy-Item "$env:USERPROFILE\.local\bin\claude.exe" "$build\claude.exe"
}
if($env:AIFACTORY_SERVER_IP){
 $hosts="$env:SystemRoot\System32\drivers\etc\hosts"
 $content=[IO.File]::ReadAllText($hosts)
 $entries=@($content -split "`n" | Where-Object {($_.Split('#')[0] -split '\s+') -contains $uri.DnsSafeHost})
 if($entries.Count){foreach($line in $entries){if(($line.Trim() -split '\s+')[0] -ne $env:AIFACTORY_SERVER_IP){throw 'Existing hosts entry differs from AIFACTORY_SERVER_IP'}}}
 else{[IO.File]::AppendAllText($hosts,"`r`n"+$env:AIFACTORY_SERVER_IP+' '+$uri.DnsSafeHost+" # aifactory-worker`r`n",$utf8)}
}
[IO.File]::WriteAllText("$build\check.token",$token.Trim(),$utf8)
[IO.File]::WriteAllText("$build\check.crt",$ca,$utf8)
$check=@{worker=$worker;url=$endpoint;token_file="$build\check.token";ca_file="$build\check.crt";state_dir="$build\check-state"}
[IO.File]::WriteAllText("$build\check.json",($check | ConvertTo-Json),$utf8)
Run-Checked "$build\aifactory-worker.exe" @('--config',"$build\check.json",'--check')
try {
if($old){
 Stop-Service AIFactoryWorker
 if(Test-Path "$($old.state_dir)\guest-lease"){Start-Service AIFactoryWorker;throw 'Worker acquired a lease during setup; retry after release'}
 if(Get-ScheduledTask AIFactoryDesktop -ErrorAction SilentlyContinue){Stop-ScheduledTask AIFactoryDesktop}
 # Task Scheduler may return before its interactive process releases the executable.
 foreach($process in @(Get-Process -Name aifactory-desktop -ErrorAction SilentlyContinue | Where-Object {$_.Path -eq "$root\bin\aifactory-desktop.exe"})){
  Stop-Process -Id $process.Id -Force
  if(-not $process.WaitForExit(15000)){throw 'Desktop agent did not stop; binary update aborted'}
 }
}
New-Item -ItemType Directory -Force "$root\private" | Out-Null
$acl=[Security.AccessControl.DirectorySecurity]::new();$acl.SetAccessRuleProtection($true,$false)
foreach($sid in @('S-1-5-18','S-1-5-32-544')){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','ContainerInherit,ObjectInherit','None','Allow'))}
Set-Acl "$root\private" $acl
[IO.File]::WriteAllText("$root\private\worker.token",$token.Trim(),$utf8);$token=$null
[IO.File]::WriteAllText("$root\private\server.crt",$ca,$utf8)
$config=@{worker=$worker;url=$endpoint;token_file="$root\private\worker.token";ca_file="$root\private\server.crt";state_dir="$root\private\state";work_root="$root\work";task_user=$taskUser;task_password_file="$root\private\task.password"}
[IO.File]::WriteAllText("$build\config.json",($config | ConvertTo-Json),$utf8)
if(-not $old){& "$source\templates\install-windows-worker.ps1" -WorkerExe "$build\aifactory-worker.exe" -ConfigFile "$build\config.json" -DoNotStart}
else{Copy-Item "$build\aifactory-worker.exe" "$root\bin\aifactory-worker.exe" -Force;Copy-Item "$build\config.json" "$root\private\config.json" -Force}
foreach($name in @('aifactory-computer.exe','aifactory-desktop.exe')){Copy-Item "$build\$name" "$root\bin\$name" -Force}
Copy-Item "$source\computer\windows.ps1" "$root\bin\windows.ps1" -Force
if(Test-Path "$build\claude.exe"){Copy-Item "$build\claude.exe" "$root\bin\claude.exe" -Force}
& "$source\computer\install-windows.ps1"
if($env:AIFACTORY_AUTOLOGIN -eq '1'){& "$PSScriptRoot\autologon.ps1" -TaskUser $taskUser -PasswordFile "$root\private\task.password"}
Start-Service AIFactoryWorker
if((Get-Service AIFactoryWorker).Status -ne 'Running'){throw 'Worker service failed to start'}
Write-Output "Installed Windows worker $worker. Check control list for online status."
Write-Output 'Computer use requires the dedicated task user to be logged in with an unlocked desktop. Autologon, if requested, takes effect after reboot.'

} catch {
 if($old){Start-Service AIFactoryWorker -ErrorAction SilentlyContinue}
 throw
}
