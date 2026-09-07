# Store the generated task-account password as an LSA secret, never in argv or DefaultPassword.
param([string]$TaskUser,[string]$PasswordFile)
$ErrorActionPreference='Stop'
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class AIFactoryLogonSecret {
 [StructLayout(LayoutKind.Sequential)] struct US {public ushort Length;public ushort MaximumLength;public IntPtr Buffer;}
 [StructLayout(LayoutKind.Sequential)] struct OA {public uint Length;public IntPtr RootDirectory;public IntPtr ObjectName;public uint Attributes;public IntPtr SecurityDescriptor;public IntPtr SecurityQualityOfService;}
 [DllImport("advapi32.dll")] static extern uint LsaOpenPolicy(IntPtr system,ref OA attrs,uint access,out IntPtr handle);
 [DllImport("advapi32.dll")] static extern uint LsaStorePrivateData(IntPtr handle,ref US key,ref US value);
 [DllImport("advapi32.dll")] static extern uint LsaClose(IntPtr handle);
 [DllImport("advapi32.dll")] static extern uint LsaNtStatusToWinError(uint status);
 static US Str(string s){return new US{Length=checked((ushort)(s.Length*2)),MaximumLength=checked((ushort)((s.Length+1)*2)),Buffer=Marshal.StringToHGlobalUni(s)};}
 public static void Store(string password){
  var attrs=new OA();attrs.Length=(uint)Marshal.SizeOf(typeof(OA));IntPtr h;
  uint status=LsaOpenPolicy(IntPtr.Zero,ref attrs,0x20,out h);
  if(status!=0)throw new System.ComponentModel.Win32Exception((int)LsaNtStatusToWinError(status));
  var key=Str("DefaultPassword");var value=Str(password);
  try{status=LsaStorePrivateData(h,ref key,ref value);if(status!=0)throw new System.ComponentModel.Win32Exception((int)LsaNtStatusToWinError(status));}
  finally{Marshal.ZeroFreeGlobalAllocUnicode(value.Buffer);Marshal.FreeHGlobal(key.Buffer);LsaClose(h);}
 }
}
'@
$password=[IO.File]::ReadAllText($PasswordFile)
try{[AIFactoryLogonSecret]::Store($password)}finally{$password=$null}
$key='HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
Set-ItemProperty $key DefaultUserName $TaskUser
Set-ItemProperty $key DefaultDomainName $env:COMPUTERNAME
Set-ItemProperty $key AutoAdminLogon '1'
Remove-ItemProperty $key DefaultPassword -ErrorAction SilentlyContinue
Write-Output 'Automatic task-user login configured in LSA. It takes effect at the next reboot; no reboot was forced.'
