# Public Windows entry point. Run in an elevated PowerShell on the dedicated machine.
function Install-AIFactoryWorker {
 $ErrorActionPreference='Stop'
 $ProgressPreference='SilentlyContinue'
 Set-ExecutionPolicy -Scope Process Bypass -Force
 [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
 foreach($name in @('AIFACTORY_URL','AIFACTORY_WORKER','AIFACTORY_TOKEN')){
  if(-not [Environment]::GetEnvironmentVariable($name)){throw "Set $name before running this script"}
 }
 if(-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Run in an elevated Administrator PowerShell'}
 $ref=$env:AIFACTORY_REF; if(-not $ref){$ref='install/worker-bootstrap'}
 if($ref -notmatch '^[a-zA-Z0-9][a-zA-Z0-9._/-]*$' -or $ref.Contains('..')){throw 'Invalid AIFACTORY_REF'}
 $stage=Join-Path $env:ProgramData ('AIFactoryInstall-'+[guid]::NewGuid().ToString('N'))
 New-Item -ItemType Directory $stage | Out-Null
 $acl=[Security.AccessControl.DirectorySecurity]::new();$acl.SetAccessRuleProtection($true,$false)
 foreach($sid in @('S-1-5-18','S-1-5-32-544')){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','ContainerInherit,ObjectInherit','None','Allow'))}
 Set-Acl -LiteralPath $stage -AclObject $acl
 try {
  Invoke-WebRequest -UseBasicParsing -Uri ('https://codeload.github.com/akkijp-oss/aifactory/zip/'+[uri]::EscapeDataString($ref)) -OutFile "$stage\source.zip"
  Expand-Archive -LiteralPath "$stage\source.zip" -DestinationPath "$stage\source"
  $source=Get-ChildItem "$stage\source" -Directory | Select-Object -First 1
  & "$($source.FullName)\workers\bootstrap\install-windows.ps1"
 } finally { Remove-Item -LiteralPath $stage -Recurse -Force }
}
Install-AIFactoryWorker
