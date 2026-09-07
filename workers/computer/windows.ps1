$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
[Console]::InputEncoding=[Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class DesktopInput {
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint flags,uint dx,uint dy,uint data,UIntPtr extra);
 [DllImport("user32.dll")] public static extern void keybd_event(byte key,byte scan,uint flags,UIntPtr extra);
 [DllImport("user32.dll")] static extern uint SendInput(uint n, INPUT[] data,int size);
 [StructLayout(LayoutKind.Explicit,Size=40)] struct INPUT {
  [FieldOffset(0)] public uint type;
  [FieldOffset(8)] public ushort vk;
  [FieldOffset(10)] public ushort scan;
  [FieldOffset(12)] public uint flags;
 }
 public static void Text(string text) {
  foreach(char c in text) {
   var data=new INPUT[]{new INPUT{type=1,scan=c,flags=4},new INPUT{type=1,scan=c,flags=6}};
   if(SendInput(2,data,40)!=2)throw new Exception("Text input failed");
  }
 }
 [DllImport("user32.dll")] static extern IntPtr OpenInputDesktop(uint flags,bool inherit,uint access);
 [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr desktop);
 [DllImport("user32.dll")] static extern bool SwitchDesktop(IntPtr desktop);
 public static bool Unlocked(){var h=OpenInputDesktop(0,false,0x100);if(h==IntPtr.Zero)return false;try{return SwitchDesktop(h);}finally{CloseDesktop(h);}}
}
'@
[DesktopInput]::SetProcessDPIAware() | Out-Null
try {
 if([Diagnostics.Process]::GetCurrentProcess().SessionId -eq 0){throw 'Interactive desktop session required'}
 if(-not [DesktopInput]::Unlocked()){throw 'Desktop is locked or unavailable'}
 $r=[Console]::In.ReadToEnd() | ConvertFrom-Json
 $screen=[Windows.Forms.SystemInformation]::VirtualScreen
 $width=[Math]::Min(1024,$screen.Width); $height=[int][Math]::Round($screen.Height*$width/$screen.Width)
 switch($r.action){
  'screenshot' {
   $bmp=[Drawing.Bitmap]::new($screen.Width,$screen.Height)
   $g=[Drawing.Graphics]::FromImage($bmp);$stream=[IO.MemoryStream]::new();$small=[Drawing.Bitmap]::new($width,$height);$scaled=[Drawing.Graphics]::FromImage($small)
   try{$g.CopyFromScreen($screen.X,$screen.Y,0,0,$bmp.Size);$scaled.DrawImage($bmp,0,0,$width,$height);$small.Save($stream,[Drawing.Imaging.ImageFormat]::Png);$out=@{ok=$true;width=$width;height=$height;image=[Convert]::ToBase64String($stream.ToArray());mimeType='image/png'}}finally{$scaled.Dispose();$small.Dispose();$g.Dispose();$bmp.Dispose();$stream.Dispose()}
  }
  {$_ -in 'click','move'} {
   if($r.x -lt 0 -or $r.x -ge $width -or $r.y -lt 0 -or $r.y -ge $height){throw 'Coordinates outside screenshot'}
   if(-not [DesktopInput]::SetCursorPos($screen.X+[int]($r.x*$screen.Width/$width),$screen.Y+[int]($r.y*$screen.Height/$height))){throw 'Pointer move failed'}
   if($r.action -eq 'click'){
    $down=2;$up=4;if($r.button -eq 'right'){$down=8;$up=16}
    $count=1;if($r.count -eq 2){$count=2}
    for($i=0;$i -lt $count;$i++){[DesktopInput]::mouse_event($down,0,0,0,[UIntPtr]::Zero);[DesktopInput]::mouse_event($up,0,0,0,[UIntPtr]::Zero);Start-Sleep -Milliseconds 60}
   }
   $out=@{ok=$true}
  }
  'type' {[DesktopInput]::Text([string]$r.text);$out=@{ok=$true}}
  'key' {
   $keys=@{CTRL=17;ALT=18;SHIFT=16;WIN=91;ENTER=13;TAB=9;ESC=27;BACKSPACE=8;DELETE=46;SPACE=32;UP=38;DOWN=40;LEFT=37;RIGHT=39;HOME=36;END=35;PAGEUP=33;PAGEDOWN=34;F1=112;F2=113;F3=114;F4=115;F5=116;F6=117;F7=118;F8=119;F9=120;F10=121;F11=122;F12=123}
   $codes=@();foreach($key in $r.keys){$k=$key.ToUpperInvariant();if($keys.ContainsKey($k)){$codes+=[byte]$keys[$k]}elseif($k -match '^[A-Z0-9]$'){$codes+=[byte][char]$k}else{throw 'Unsupported key'}}
   try{foreach($c in $codes){[DesktopInput]::keybd_event($c,0,0,[UIntPtr]::Zero)}}finally{[Array]::Reverse($codes);foreach($c in $codes){[DesktopInput]::keybd_event($c,0,2,[UIntPtr]::Zero)}}
   $out=@{ok=$true}
  }
  'scroll' {
   $delta=[int]$r.amount*120
   $data=[BitConverter]::ToUInt32([BitConverter]::GetBytes($delta),0)
   [DesktopInput]::mouse_event(0x800,0,0,$data,[UIntPtr]::Zero);$out=@{ok=$true}
  }
  default {throw 'Unsupported action'}
 }
 $out | ConvertTo-Json -Compress -Depth 4
}catch{@{ok=$false;error=$_.Exception.Message} | ConvertTo-Json -Compress;exit 1}
